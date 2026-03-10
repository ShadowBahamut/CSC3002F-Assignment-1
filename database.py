"""
Database module for the chat application.

This module provides SQLite database management for users, sessions, groups,
and message history. It uses context managers for safe database connections
and provides thread-safe operations.

Author: Group 68 (Anson Vattakunnel, Daniel Yu, Reece Baker)
Date: 03/06/26
"""

import sqlite3
import threading
import hashlib
import os
from datetime import datetime
from typing import Optional, List, Tuple, Any
from contextlib import contextmanager

from models import User, Session, Group, ChatMessage, generate_session_token


class Database:
    """
    SQLite database manager for chat application.

    The class handles all database operations including user authentication,
    session management, group operations, and message history. It provides
    thread-safe operations using locks and context managers for safe connections.

    Attributes:
        db_path: Path to the SQLite database file
        lock: Thread lock for concurrent database access
    """

    def __init__(self, db_path: str = "chat.db"):
        """
        Initializes database connection.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self.lock = threading.Lock()
        self._initialize_database()

    def _initialize_database(self) -> None:
        """
        Initialize database schema.

        Creates all necessary tables if they don't exist:
        - users: User accounts
        - sessions: Active user sessions
        - groups: Chat groups
        - group_members: Group membership relationships
        - messages: Chat message history
        """
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Users table: stores registered users
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Sessions table: stores active user sessions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    ip_address TEXT,
                    udp_port INTEGER,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """)

            # Groups table: stores chat groups
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    owner_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (owner_id) REFERENCES users (id)
                )
            """)

            # Group members table: stores group membership
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_members (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, user_id),
                    FOREIGN KEY (group_id) REFERENCES groups (id),
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """)

            # Messages table: stores chat history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    recipient_id INTEGER,
                    group_id INTEGER,
                    message_type TEXT NOT NULL,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (sender_id) REFERENCES users (id),
                    FOREIGN KEY (recipient_id) REFERENCES users (id),
                    FOREIGN KEY (group_id) REFERENCES groups (id)
                )
            """)

            # Create indexes for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_user
                ON sessions (user_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_sender
                ON messages (sender_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_recipient
                ON messages (recipient_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_group
                ON messages (group_id)
            """)

            conn.commit()
            conn.close()

    @contextmanager
    def get_connection(self):
        """
        Context manager for database connections.

        Yields:
            sqlite3.Connection: Database connection with row factory set

        Example:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _hash_password(self, password: str) -> str:
        """
        Hash a password using SHA-256 with salt.

        Args:
            password: Plain text password

        Returns:
            Hashed password with salt
        """
        # Use simple salt based on the password itself for demo
        # *In production, use proper salt from os.urandom()
        salt = "chat_app_salt_2026"
        return hashlib.sha256((salt + password).encode()).hexdigest()

    # ==================== User Operations ====================

    def register_user(self, username: str, password: str) -> Tuple[bool, str]:
        """
        Register a new user.

        Args:
            username: Desired username
            password: Plain text password

        Returns:
            Tuple of (success, message)
        """
        with self.lock:
            try:
                with self.get_connection() as conn:
                    cursor = conn.cursor()
                    password_hash = self._hash_password(password)

                    cursor.execute(
                        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (username, password_hash)
                    )
                    conn.commit()
                    return True, "User registered successfully"
            except sqlite3.IntegrityError:
                return False, "Username already exists"
            except Exception as e:
                return False, f"Registration failed: {str(e)}"

    def authenticate_user(self, username: str, password: str) -> Tuple[bool, Optional[int], str]:
        """
        Authenticate user with username and password.

        Args:
            username: Username
            password: Plain text password

        Returns:
            Tuple of (success, user_id, message)
        """
        with self.lock:
            try:
                with self.get_connection() as conn:
                    cursor = conn.cursor()
                    password_hash = self._hash_password(password)

                    cursor.execute(
                        "SELECT id FROM users WHERE username = ? AND password_hash = ?",
                        (username, password_hash)
                    )
                    result = cursor.fetchone()

                    if result:
                        return True, result['id'], "Authentication successful"
                    else:
                        return False, None, "Invalid username or password"
            except Exception as e:
                return False, None, f"Authentication failed: {str(e)}"

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """
        Get user by ID.

        Args:
            user_id: User ID

        Returns:
            User object or None if not found
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, username, password_hash, created_at FROM users WHERE id = ?",
                    (user_id,)
                )
                row = cursor.fetchone()
                if row:
                    return User(
                        id=row['id'],
                        username=row['username'],
                        password_hash=row['password_hash'],
                        created_at=datetime.fromisoformat(row['created_at'])
                    )
                return None

    def get_user_by_username(self, username: str) -> Optional[User]:
        """
        Get user by username.

        Args:
            username: Username

        Returns:
            User object or None if not found
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
                    (username,)
                )
                row = cursor.fetchone()
                if row:
                    return User(
                        id=row['id'],
                        username=row['username'],
                        password_hash=row['password_hash'],
                        created_at=datetime.fromisoformat(row['created_at'])
                    )
                return None

    def username_exists(self, username: str) -> bool:
        """
        Check if username exists.

        Args:
            username: Username to check

        Returns:
            True if username exists
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
                return cursor.fetchone() is not None

    def get_all_users(self) -> List[str]:
        """
        Get list of all registered usernames.

        Returns:
            List of usernames
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT username FROM users ORDER BY username")
                return [row['username'] for row in cursor.fetchall()]

    # ==================== Session Operations ====================

    def create_session(self, user_id: int, ip_address: str, udp_port: Optional[int] = None) -> Session:
        """
        Create new session for authenticated user.

        Args:
            user_id: User ID
            ip_address: Client IP address
            udp_port: Client UDP port for P2P

        Returns:
            Session object
        """
        with self.lock:
            token = generate_session_token()
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO sessions (token, user_id, ip_address, udp_port) VALUES (?, ?, ?, ?)",
                    (token, user_id, ip_address, udp_port)
                )
                # Fetch username using same connection to avoid re-acquiring
                # self.lock (non-reentrant and would deadlock).
                cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
                user_row = cursor.fetchone()
                username = user_row['username'] if user_row else "unknown"
                conn.commit()

            return Session(
                token=token,
                user_id=user_id,
                username=username,
                ip_address=ip_address,
                udp_port=udp_port
            )

    def get_session(self, token: str) -> Optional[Session]:
        """
        Get session by token.

        Args:
            token: Session token

        Returns:
            Session object or None if not found/expired
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT s.token, s.user_id, s.ip_address, s.udp_port, s.last_active, u.username
                    FROM sessions s
                    JOIN users u ON s.user_id = u.id
                    WHERE s.token = ?""",
                    (token,)
                )
                row = cursor.fetchone()
                if row:
                    session = Session(
                        token=row['token'],
                        user_id=row['user_id'],
                        username=row['username'],
                        ip_address=row['ip_address'],
                        udp_port=row['udp_port'],
                        last_active=datetime.fromisoformat(row['last_active'])
                    )
                    # Check if session expired. Delete inline using
                    # existing connection to avoid re-acquiring self.lock
                    # (non-reentrant) which would deadlock.
                    if session.is_expired():
                        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
                        conn.commit()
                        return None
                    return session
                return None

    def update_session_activity(self, token: str) -> None:
        """
        Update session last active timestamp.

        Args:
            token: Session token
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE sessions SET last_active = CURRENT_TIMESTAMP WHERE token = ?",
                    (token,)
                )
                conn.commit()

    def delete_session(self, token: str) -> None:
        """
        Delete a session.

        Args:
            token: Session token
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()

    def delete_user_sessions(self, user_id: int) -> None:
        """
        Delete all sessions for a user.

        Args:
            user_id: User ID
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                conn.commit()

    def cleanup_expired_sessions(self, timeout_seconds: int = 60) -> int:
        """
        Clean up expired sessions.

        Args:
            timeout_seconds: Session timeout in seconds

        Returns:
            Number of sessions deleted
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""DELETE FROM sessions
                    WHERE datetime(last_active) < datetime('now', '-{timeout_seconds} seconds')"""
                )
                deleted = cursor.rowcount
                conn.commit()
                return deleted

    # ==================== Group Operations =====================

    def create_group(self, name: str, owner_id: int) -> Tuple[bool, str, Optional[int]]:
        """
        Create new chat group.

        Args:
            name: Group name
            owner_id: User ID of group creator

        Returns:
            Tuple of (success, message, group_id)
        """
        with self.lock:
            try:
                with self.get_connection() as conn:
                    cursor = conn.cursor()

                    # Insert group
                    cursor.execute(
                        "INSERT INTO groups (name, owner_id) VALUES (?, ?)",
                        (name, owner_id)
                    )
                    group_id = cursor.lastrowid

                    # Add owner as first member
                    cursor.execute(
                        "INSERT INTO group_members (group_id, user_id) VALUES (?, ?)",
                        (group_id, owner_id)
                    )

                    conn.commit()
                    return True, "Group created successfully", group_id
            except sqlite3.IntegrityError:
                return False, "Group name already exists", None
            except Exception as e:
                return False, f"Failed to create group: {str(e)}", None

    def get_group_by_name(self, name: str) -> Optional[Group]:
        """
        Get group by name.

        Args:
            name: Group name

        Returns:
            Group object or None if not found
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT g.id, g.name, g.owner_id, g.created_at,
                    GROUP_CONCAT(u.username) as members
                    FROM groups g
                    LEFT JOIN group_members gm ON g.id = gm.group_id
                    LEFT JOIN users u ON gm.user_id = u.id
                    WHERE g.name = ?
                    GROUP BY g.id""",
                    (name,)
                )
                row = cursor.fetchone()
                if row:
                    members = row['members'].split(',') if row['members'] else []
                    return Group(
                        id=row['id'],
                        name=row['name'],
                        owner_id=row['owner_id'],
                        created_at=datetime.fromisoformat(row['created_at']),
                        members=members
                    )
                return None

    def get_group_by_id(self, group_id: int) -> Optional[Group]:
        """
        Get group by ID.

        Args:
            group_id: Group ID

        Returns:
            Group object or None if not found
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT g.id, g.name, g.owner_id, g.created_at,
                    GROUP_CONCAT(u.username) as members
                    FROM groups g
                    LEFT JOIN group_members gm ON g.id = gm.group_id
                    LEFT JOIN users u ON gm.user_id = u.id
                    WHERE g.id = ?
                    GROUP BY g.id""",
                    (group_id,)
                )
                row = cursor.fetchone()
                if row:
                    members = row['members'].split(',') if row['members'] else []
                    return Group(
                        id=row['id'],
                        name=row['name'],
                        owner_id=row['owner_id'],
                        created_at=datetime.fromisoformat(row['created_at']),
                        members=members
                    )
                return None

    def join_group(self, group_name: str, user_id: int) -> Tuple[bool, str]:
        """
        Add user to a group.

        Args:
            group_name: Group name
            user_id: User ID

        Returns:
            Tuple of (success, message)
        """
        with self.lock:
            try:
                with self.get_connection() as conn:
                    cursor = conn.cursor()

                    # Get group ID
                    cursor.execute("SELECT id FROM groups WHERE name = ?", (group_name,))
                    group_row = cursor.fetchone()
                    if not group_row:
                        return False, "Group not found"

                    group_id = group_row['id']

                    # Check if already member
                    cursor.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id)
                    )
                    if cursor.fetchone():
                        return False, "Already a member of this group"

                    # Add member
                    cursor.execute(
                        "INSERT INTO group_members (group_id, user_id) VALUES (?, ?)",
                        (group_id, user_id)
                    )
                    conn.commit()
                    return True, "Joined group successfully"
            except sqlite3.IntegrityError:
                return False, "Already a member of this group"
            except Exception as e:
                return False, f"Failed to join group: {str(e)}"

    def leave_group(self, group_name: str, user_id: int) -> Tuple[bool, str]:
        """
        User leaves a group.

        Args:
            group_name: Group name
            user_id: User ID

        Returns:
            Tuple of (success, message)
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Get group
                cursor.execute("SELECT id, owner_id FROM groups WHERE name = ?", (group_name,))
                group_row = cursor.fetchone()
                if not group_row:
                    return False, "Group not found"

                group_id = group_row['id']

                # Check if owner tries to leave
                if group_row['owner_id'] == user_id:
                    # Deletes group entirely
                    cursor.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
                    cursor.execute("DELETE FROM groups WHERE id = ?", (group_id,))
                    conn.commit()
                    return True, "Group deleted (owner left)"

                # Remove member
                cursor.execute(
                    "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id)
                )
                if cursor.rowcount == 0:
                    return False, "Not a member of this group"

                conn.commit()
                return True, "Left group successfully"

    def is_member(self, group_name: str, user_id: int) -> bool:
        """
        Check if user is a member of a group.

        Args:
            group_name: Group name
            user_id: User ID

        Returns:
            True if user is a member
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT 1 FROM groups g
                    JOIN group_members gm ON g.id = gm.group_id
                    WHERE g.name = ? AND gm.user_id = ?""",
                    (group_name, user_id)
                )
                return cursor.fetchone() is not None

    def get_group_members(self, group_name: str) -> List[str]:
        """
        Get list of usernames in the group.

        Args:
            group_name: Group name

        Returns:
            List of member usernames
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT u.username FROM users u
                    JOIN group_members gm ON u.id = gm.user_id
                    JOIN groups g ON gm.group_id = g.id
                    WHERE g.name = ?""",
                    (group_name,)
                )
                return [row['username'] for row in cursor.fetchall()]

    def get_all_groups(self) -> List[str]:
        """
        Get list of all group names.

        Returns:
            List of group names
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM groups ORDER BY name")
                return [row['name'] for row in cursor.fetchall()]

    def get_user_groups(self, user_id: int) -> List[str]:
        """
        Get list of groups a user is a member of.

        Args:
            user_id: User ID

        Returns:
            List of group names
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT g.name FROM groups g
                    JOIN group_members gm ON g.id = gm.group_id
                    WHERE gm.user_id = ?
                    ORDER BY g.name""",
                    (user_id,)
                )
                return [row['name'] for row in cursor.fetchall()]

    # ==================== Message Operations ====================

    def save_message(self, sender_id: int, recipient_id: Optional[int],
                     group_id: Optional[int], message_type: str, content: str) -> int:
        """
        Save a chat message to history.

        Args:
            sender_id: Sender user ID
            recipient_id: Recipient user ID (for 1-to-1)
            group_id: Group ID (for group messages)
            message_type: Type of message
            content: Message content

        Returns:
            Message ID
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO messages
                    (sender_id, recipient_id, group_id, message_type, content)
                    VALUES (?, ?, ?, ?, ?)""",
                    (sender_id, recipient_id, group_id, message_type, content)
                )
                conn.commit()
                return cursor.lastrowid

    def get_messages(self, user_id: int, limit: int = 50) -> List[ChatMessage]:
        """
        Get chat history for a user.

        Args:
            user_id: User ID
            limit: Maximum number of messages

        Returns:
            List of chat messages
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT id, sender_id, recipient_id, group_id,
                    message_type, content, timestamp
                    FROM messages
                    WHERE sender_id = ? OR recipient_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?""",
                    (user_id, user_id, limit)
                )
                messages = []
                for row in cursor.fetchall():
                    messages.append(ChatMessage(
                        id=row['id'],
                        sender_id=row['sender_id'],
                        recipient_id=row['recipient_id'],
                        group_id=row['group_id'],
                        message_type=row['message_type'],
                        content=row['content'],
                        timestamp=datetime.fromisoformat(row['timestamp'])
                    ))
                return messages

    def get_group_messages(self, group_id: int, limit: int = 50) -> List[ChatMessage]:
        """
        Get chat history for a group.

        Args:
            group_id: Group ID
            limit: Maximum number of messages

        Returns:
            List of chat messages
        """
        with self.lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT id, sender_id, recipient_id, group_id,
                    message_type, content, timestamp
                    FROM messages
                    WHERE group_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?""",
                    (group_id, limit)
                )
                messages = []
                for row in cursor.fetchall():
                    messages.append(ChatMessage(
                        id=row['id'],
                        sender_id=row['sender_id'],
                        recipient_id=row['recipient_id'],
                        group_id=row['group_id'],
                        message_type=row['message_type'],
                        content=row['content'],
                        timestamp=datetime.fromisoformat(row['timestamp'])
                    ))
                return messages


# Global database instance
db = Database()

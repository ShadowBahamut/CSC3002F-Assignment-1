"""
Models module for the chat application.

This module defines the core data structures used throughout the application,
including messages, users, sessions, groups, and transfer states.

Author: MiniMax Agent
Date: 2026-03-03
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, List, Any
import uuid


class MessageCategory(Enum):
    """
    Enumeration of message categories in the chat protocol.

    CMND (Command): Operations like login, register, create group
    CTRL (Control): Acknowledgements, errors, media coordination
    DATA (Data): Actual chat content and media chunks
    """
    CMND = "CMND"
    CTRL = "CTRL"
    DATA = "DATA"


class MessageType(Enum):
    """
    Enumeration of message types in the chat protocol.

    Commands:
        REGISTER: Create a new user account
        LOGIN: Authenticate user and create session
        LOGOUT: End user session
        CREATE_GROUP: Create a new chat group
        JOIN_GROUP: Join an existing group
        LEAVE_GROUP: Leave a group
        SEND_TEXT: Send text message
        SEND_GROUP_TEXT: Send text to group

    Controls:
        OK: Operation successful
        ERROR: Operation failed
        MEDIA_OFFER: Offer file transfer
        MEDIA_ACCEPT: Accept file transfer
        MEDIA_REJECT: Reject file transfer
        MEDIA_NACK: Negative acknowledgement for media
        MEDIA_DONE: Media transfer complete

    Data:
        TEXT: Text message content
        MEDIA_CHUNK: Binary media data
    """
    # Command types
    REGISTER = "REGISTER"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    CREATE_GROUP = "CREATE_GROUP"
    JOIN_GROUP = "JOIN_GROUP"
    LEAVE_GROUP = "LEAVE_GROUP"
    SEND_TEXT = "SEND_TEXT"
    SEND_GROUP_TEXT = "SEND_GROUP_TEXT"
    LIST_GROUPS = "LIST_GROUPS"
    LIST_USERS = "LIST_USERS"

    # Control types
    OK = "OK"
    ERROR = "ERROR"
    MEDIA_OFFER = "MEDIA_OFFER"
    MEDIA_ACCEPT = "MEDIA_ACCEPT"
    MEDIA_REJECT = "MEDIA_REJECT"
    MEDIA_NACK = "MEDIA_NACK"
    MEDIA_DONE = "MEDIA_DONE"
    BROADCAST = "BROADCAST"

    # Data types
    TEXT = "TEXT"
    MEDIA_CHUNK = "MEDIA_CHUNK"


class ErrorCode(Enum):
    """
    HTTP-like error codes for the chat protocol.

    400: Bad Request - Malformed message
    401: Unauthorized - Not logged in or invalid credentials
    403: Forbidden - Not allowed to perform action
    404: Not Found - User or group doesn't exist
    409: Conflict - Username already exists
    500: Internal Server Error
    408: Request Timeout
    """
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    INTERNAL_ERROR = 500
    TIMEOUT = 408


@dataclass
class Message:
    """
    Represents a chat protocol message.

    The message follows an HTTP-like structure:
        CHAT <Category> <Type>\n
    Attributes:
        category: Message category (CMND, CTRL, DATA)
        type: Message type (LOGIN, TEXT, etc.)
        headers: Dictionary of header key-value pairs
        body: Optional message body (text or binary)
        raw_data: Original raw data received (if any)
    """
    category: MessageCategory
    type: MessageType
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    raw_data: Optional[bytes] = None

    def get_header(self, key: str, default: str = "") -> str:
        """
        Get a header value with optional default.

        Args:
            key: Header name (case-insensitive)
            default: Default value if header not found

        Returns:
            Header value or default
        """
        # Case-insensitive lookup
        key_lower = key.lower()
        for k, v in self.headers.items():
            if k.lower() == key_lower:
                return v
        return default

    def set_header(self, key: str, value: str) -> None:
        """
        Set a header value.

        Args:
            key: Header name
            value: Header value
        """
        self.headers[key] = value

    def has_body(self) -> bool:
        """Check if message has a body."""
        return self.body is not None and len(self.body) > 0

    def get_body_text(self) -> str:
        """
        Get body as text (UTF-8 decoding).

        Returns:
            Decoded body text, empty string if no body
        """
        if self.body:
            return self.body.decode('utf-8', errors='replace')
        return ""


@dataclass
class User:
    """
    Represents a registered user in the system.

    Attributes:
        id: Unique database ID
        username: Unique username for login
        password_hash: Hashed password (not stored in plain text)
        created_at: Account creation timestamp
    """
    id: int
    username: str
    password_hash: str
    created_at: datetime

    def __str__(self) -> str:
        return f"User({self.username})"


@dataclass
class Session:
    """
    Represents an active user session.

    Attributes:
        token: Unique session token (UUID)
        user_id: Associated user ID
        username: Username for display
        ip_address: Client IP address
        udp_port: Client's UDP listening port for P2P
        last_active: Last activity timestamp
        client_socket: Active TCP socket connection
    """
    token: str
    user_id: int
    username: str
    ip_address: str
    udp_port: Optional[int] = None
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    client_socket: Any = None

    def update_activity(self) -> None:
        """Update the last active timestamp."""
        self.last_active = datetime.now(timezone.utc).replace(tzinfo=None)

    def is_expired(self, timeout_seconds: int = 60) -> bool:
        """
        Check if session has expired due to inactivity.

        Args:
            timeout_seconds: Session timeout in seconds

        Returns:
            True if session is expired
        """
        elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - self.last_active).total_seconds()
        return elapsed > timeout_seconds

    def __str__(self) -> str:
        return f"Session({self.username}, {self.token[:8]}...)"


@dataclass
class Group:
    """
    Represents a chat group.

    Attributes:
        id: Unique database ID
        name: Group name (unique)
        owner_id: User ID of group creator
        created_at: Group creation timestamp
        members: List of member usernames
    """
    id: int
    name: str
    owner_id: int
    created_at: datetime
    members: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"Group({self.name}, {len(self.members)} members)"


@dataclass
class ChatMessage:
    """
    Represents a chat message for history storage.

    Attributes:
        id: Unique database ID
        sender_id: User ID of sender
        recipient_id: User ID of recipient (for 1-to-1)
        group_id: Group ID (for group messages)
        message_type: Type of message (TEXT, etc.)
        content: Message content
        timestamp: Message timestamp
    """
    id: int
    sender_id: int
    recipient_id: Optional[int]
    group_id: Optional[int]
    message_type: str
    content: str
    timestamp: datetime


@dataclass
class MediaTransfer:
    """
    Represents an in-progress media transfer.

    Attributes:
        transfer_id: Unique transfer identifier
        sender_username: Username of sender
        recipient_username: Username of recipient
        filename: Name of file being transferred
        file_size: Total size in bytes
        chunks_total: Total number of chunks
        chunks_received: Number of chunks received
        status: Transfer status (PENDING, ACTIVE, COMPLETED, FAILED)
        created_at: Transfer start timestamp
    """
    transfer_id: str
    sender_username: str
    recipient_username: str
    filename: str
    file_size: int
    chunks_total: int = 0
    chunks_received: int = 0
    status: str = "PENDING"
    created_at: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        return f"Transfer({self.filename}, {self.chunks_received}/{self.chunks_total})"


def generate_session_token() -> str:
    """
    Generate a unique session token.

    Returns:
        UUID-based session token
    """
    return str(uuid.uuid4())


def generate_transfer_id() -> str:
    """
    Generate a unique transfer ID.

    Returns:
        UUID-based transfer ID
    """
    return str(uuid.uuid4())

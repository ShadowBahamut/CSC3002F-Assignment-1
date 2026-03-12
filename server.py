#!/usr/bin/env python3
"""
Chat Server module for the chat application.

This module implements the TCP server that handles client connections,
user authentication, message routing (one-to-one and group chat), and group management 
and supports for UDP one-to-one and group file transfer coordination. 
It uses SQLite for data persistence and supports concurrent clients using threads.

Author: Group 68 (Anson Vattakunnel, Daniel Yu, Reece Baker)
Date: 13/03/26
"""

import socket
import threading
import sys
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

from models import (
    Message, MessageCategory, MessageType, ErrorCode, Session
)
from database import Database
from protocol import (
    ProtocolHandler, ProtocolError, parse_message_id
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ChatServer')


class ChatServer:
    """
    TCP Chat Server implementation.

    This server handles:
    - User registration and authentication
    - Session management
    - One-to-one message routing
    - Group chat message routing
    - Group creation and membership

    Attributes:
        host: Server host address
        port: Server port number
        db: Database instance
        active_sessions: Dictionary of active sessions by token
        active_clients: Dictionary of client sockets by username
        lock: Thread lock for session management
    """

    # Server configuration
    HOST = '0.0.0.0'    # Listen on all interfaces
    PORT = 8888 # Default TCP port
    MAX_CLIENTS = 100   # Maximum concurrent clients
    SESSION_TIMEOUT = 600   # Session timeout in 600 seconds/10 min

    def __init__(self, host: str = None, port: int = None):
        """
        Initialize chat server.

        Args:
            host: Server host address (default: 0.0.0.0)
            port: Server port number (default: 8888)
        """
        self.host = host or self.HOST
        self.port = port or self.PORT
        self.db = Database()

        # Session and client management
        self.active_sessions: Dict[str, Session] = {}
        self.active_clients: Dict[str, Tuple[socket.socket, Session]] = {}
        self.lock = threading.Lock()

        # Server socket
        self.server_socket: Optional[socket.socket] = None
        self.running = False

        logger.info(f"ChatServer initialized on {self.host}:{self.port}")

    def start(self) -> None:
        """
        Start the chat server.

        Creates server socket, binds to the configured address, and starts
        accepting client connections in a loop.
        """
        try:
            # Create TCP socket
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Bind to address
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(self.MAX_CLIENTS)

            self.running = True
            logger.info(f"Server started on {self.host}:{self.port}")

            # Start session cleanup thread
            cleanup_thread = threading.Thread(target=self._cleanup_sessions, daemon=True)
            cleanup_thread.start()

            # Accept connections loop
            while self.running:
                try:
                    client_socket, client_address = self.server_socket.accept()
                    logger.info(f"New connection from {client_address}")

                    # Handle client in a new thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, client_address),
                        daemon=True
                    )
                    client_thread.start()

                except socket.error as e:
                    if self.running:
                        logger.error(f"Socket error: {e}")
                    break

        except Exception as e:
            logger.error(f"Server error: {e}")
            raise

        finally:
            self.stop()

    def stop(self) -> None:
        """
        Stop the server and clean up resources.

        Closes all client connections and the server socket.
        """
        logger.info("Stopping server...")
        self.running = False

        # Close all client connections
        with self.lock:
            for username in list(self.active_clients.keys()):
                try:
                    sock, _ = self.active_clients[username]
                    sock.close()
                except Exception:
                    pass
            self.active_clients.clear()
            self.active_sessions.clear()

        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

        logger.info("Server stopped")

    def _cleanup_sessions(self) -> None:
        """
        Periodically clean up expired sessions.

        Runs as a daemon thread, checking for expired sessions every 30 seconds.
        """
        while self.running:
            try:
                import time
                time.sleep(30)

                # Clean up expired sessions
                expired = []
                with self.lock:
                    for token, session in self.active_sessions.items():
                        if session.is_expired(self.SESSION_TIMEOUT):
                            expired.append(token)

                # Call _remove_session outside the lock: it acquires self.lock
                # internally, and threading.Lock is non-reentrant, so calling
                # it while already holding the lock would deadlock.
                for token in expired:
                    self._remove_session(token)

                if expired:
                    logger.info(f"Cleaned up {len(expired)} expired sessions")

            except Exception as e:
                logger.error(f"Error in cleanup thread: {e}")

    def _remove_session(self, token: str) -> None:
        """
        Remove a session and notify the client if possible.

        Args:
            token: Session token to remove
        """
        with self.lock:
            if token in self.active_sessions:
                session = self.active_sessions[token]

                # Remove from active clients
                if session.username in self.active_clients:
                    try:
                        sock, _ = self.active_clients[session.username]
                        sock.close()
                    except Exception:
                        pass
                    del self.active_clients[session.username]

                # Remove from active sessions
                del self.active_sessions[token]

                # Delete from database
                self.db.delete_session(token)

                logger.info(f"Session removed: {session.username}")

    def _handle_client(self, client_socket: socket.socket,
                      client_address: tuple) -> None:
        """
        Handles client connection.

        The method runs in a separate thread for each client. It reads
        messages from the client, processes them, and sends responses.

        Args:
            client_socket: Client's socket connection
            client_address: Client's (IP, port) tuple
        """
        protocol = ProtocolHandler()
        current_session: Optional[Session] = None

        try:
            while self.running:
                try:
                    # Receive and parse message
                    message = protocol.receive_message_buffered(client_socket)
                    logger.debug(f"Received: {message.category.value} {message.type.value}")

                    # Route message based on type
                    response = self._route_message(
                        message, current_session, client_socket, client_address
                    )

                    # If this was a successful LOGIN, bind the session to this thread
                    if (message.type == MessageType.LOGIN and
                            response and response.type == MessageType.OK):
                        session_token = response.get_header('Session-Token')
                        with self.lock:
                            current_session = self.active_sessions.get(session_token)

                    # If this was a successful LOGOUT, clear the session so
                    # the client can no longer issue authenticated commands on
                    # this connection without logging in again.
                    elif (message.type == MessageType.LOGOUT and
                            response and response.type == MessageType.OK):
                        current_session = None

                    # Send response if there  is one
                    if response:
                        protocol.send_message(client_socket, response)

                    # Update session activity
                    if current_session:
                        current_session.update_activity()
                        self.db.update_session_activity(current_session.token)

                except ProtocolError as e:
                    logger.warning(f"Protocol error from {client_address}: {e}")
                    try:
                        code = ErrorCode(e.code)
                    except ValueError:
                        code = ErrorCode.BAD_REQUEST
                    error_response = MessageEncoder.encode_error(code, e.message)
                    client_socket.sendall(error_response)

                except socket.timeout:
                    # Handle timeout
                    if current_session and current_session.is_expired(self.SESSION_TIMEOUT):
                        logger.info(f"Session timeout: {current_session.username}")
                        break

                except ConnectionResetError:
                    logger.info(f"Client disconnected: {client_address}")
                    break

                except Exception as e:
                    logger.error(f"Error handling client: {e}")
                    break

        finally:
            # Clean up on disconnect
            if current_session:
                self._remove_session(current_session.token)

            try:
                client_socket.close()
            except Exception:
                pass

            logger.info(f"Connection closed: {client_address}")

    def _route_message(self, message: Message, current_session: Optional[Session],
                      client_socket: socket.socket,
                      client_address: tuple) -> Optional[Message]:
        """
        Route a message to the appropriate handler.

        Args:
            message: Received message
            current_session: Current user session (if authenticated)
            client_socket: Client socket
            client_address: Client address tuple

        Returns:
            Response message or None
        """
        msg_id = parse_message_id(message)
        category = message.category
        msg_type = message.type

        # Handle commands
        if category == MessageCategory.CMND:
            return self._handle_command(
                message, current_session, client_socket, client_address, msg_id
            )

        # Handle control messages
        elif category == MessageCategory.CTRL:
            return self._handle_control(
                message, current_session, client_socket, msg_id
            )

        # Handle data messages
        elif category == MessageCategory.DATA:
            return self._handle_data(
                message, current_session, msg_id
            )

        # Unknown category
        return self._create_error_response(
            ErrorCode.BAD_REQUEST, "Unknown message category", msg_id
        )

    def _handle_command(self, message: Message, current_session: Optional[Session],
                       client_socket: socket.socket,
                       client_address: tuple, msg_id: str) -> Message:
        """
        Handle command messages.

        Args:
            message: Command message
            current_session: Current session
            client_socket: Client socket
            client_address: Client address
            msg_id: Message ID

        Returns:
            Response message
        """
        msg_type = message.type

        # Login/Register don't require session
        if msg_type == MessageType.REGISTER:
            return self._handle_register(message, msg_id)
        elif msg_type == MessageType.LOGIN:
            return self._handle_login(message, client_socket, client_address, msg_id)
        elif msg_type == MessageType.LOGOUT:
            return self._handle_logout(current_session, msg_id)

        # Other commands require authentication
        if not current_session:
            return self._create_error_response(
                ErrorCode.UNAUTHORIZED, "Not logged in", msg_id
            )

        # Authenticated commands
        if msg_type == MessageType.CREATE_GROUP:
            return self._handle_create_group(message, current_session, msg_id)
        elif msg_type == MessageType.JOIN_GROUP:
            return self._handle_join_group(message, current_session, msg_id)
        elif msg_type == MessageType.LEAVE_GROUP:
            return self._handle_leave_group(message, current_session, msg_id)
        elif msg_type == MessageType.SEND_TEXT:
            return self._handle_send_text(message, current_session, msg_id)
        elif msg_type == MessageType.SEND_GROUP_TEXT:
            return self._handle_send_group_text(message, current_session, msg_id)
        elif msg_type == MessageType.LIST_GROUPS:
            return self._handle_list_groups(current_session, msg_id)
        elif msg_type == MessageType.LIST_USERS:
            return self._handle_list_users(current_session, msg_id)
        elif msg_type == MessageType.GET_USER_INFO:
            return self._handle_get_user_info(message, current_session, msg_id)
        elif msg_type == MessageType.LIST_GROUP_MEMBERS:
            return self._handle_list_group_members(message, current_session, msg_id)
        elif msg_type == MessageType.GET_GROUP_MEMBERS:
            return self._handle_get_group_members(message, current_session, msg_id)
        else:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, f"Unknown command: {msg_type.value}", msg_id
            )

    def _handle_control(self, message: Message, current_session: Optional[Session],
                      client_socket: socket.socket, msg_id: str) -> Message:
        """
        Handle control messages.

        Note: Media-related control messages are handled here for coordination,
        but actual media transfer happens via P2P (handled by someone else).

        Args:
            message: Control message
            current_session: Current session
            client_socket: Client socket
            msg_id: Message ID

        Returns:
            Response message
        """
        # For now, just acknowledge media coordination requests
        # Actual media transfer is done by clients via P2P
        return self._create_ok_response(msg_id)

    def _handle_data(self, message: Message, current_session: Optional[Session],
                    msg_id: str) -> Message:
        """
        Handle data messages.

        Args:
            message: Data message
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message
        """
        # Data messages are forwarded, not handled directly
        return self._create_ok_response(msg_id)

    # ==================== Command Handlers ====================

    def _handle_register(self, message: Message, msg_id: str) -> Message:
        """
        Handle user registration.

        Expected headers: Username, Password
        Expected body: None

        Args:
            message: Register message
            msg_id: Message ID

        Returns:
            Response message
        """
        username = message.get_header('Username')
        password = message.get_header('Password')

        if not username or not password:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing username or password", msg_id
            )

        success, result = self.db.register_user(username, password)

        if success:
            return self._create_ok_response(msg_id)
        else:
            return self._create_error_response(
                ErrorCode.CONFLICT, result, msg_id
            )

    def _handle_login(self, message: Message, client_socket: socket.socket,
                    client_address: tuple, msg_id: str) -> Message:
        """
        Handle user login.

        Expected headers: Username, Password, UDP-Port (optional)
        Expected body: None

        Args:
            message: Login message
            client_socket: Client socket
            client_address: Client address tuple
            msg_id: Message ID

        Returns:
            Response message with session token
        """
        username = message.get_header('Username')
        password = message.get_header('Password')
        udp_port_str = message.get_header('UDP-Port', '0')

        if not username or not password:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing username or password", msg_id
            )

        try:
            udp_port = int(udp_port_str) if udp_port_str else None
        except ValueError:
            udp_port = None

        # Authenticate user
        success, user_id, msg = self.db.authenticate_user(username, password)

        if not success:
            return self._create_error_response(
                ErrorCode.UNAUTHORIZED, msg, msg_id
            )

        ip_address = client_address[0]

        # Remove existing login for this username if present
        with self.lock:
            if username in self.active_clients:
                old_sock, old_session = self.active_clients[username]
                try:
                    old_sock.close()
                except Exception:
                    pass

                if old_session.token in self.active_sessions:
                    del self.active_sessions[old_session.token]

                self.db.delete_session(old_session.token)
                del self.active_clients[username]

        # Create new session after old one is removed
        session = self.db.create_session(user_id, ip_address, udp_port)

        # Register active session and client
        with self.lock:
            self.active_sessions[session.token] = session
            self.active_clients[username] = (client_socket, session)

        logger.info(f"User logged in: {username}")

        return self._create_ok_response(
            msg_id,
            extra_headers={'Session-Token': session.token}
        )

    def _handle_logout(self, current_session: Optional[Session],
                      msg_id: str) -> Message:
        """
        Handle user logout.

        Args:
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message
        """
        if not current_session:
            return self._create_error_response(
                ErrorCode.UNAUTHORIZED, "Not logged in", msg_id
            )

        username = current_session.username

        # Remove session data without closing the socket. _remove_session
        # closes the socket, which would prevent the OK response from being
        # sent back to the client. The socket will be closed naturally when
        # _handle_client's finally block runs after current_session is set
        # to None (handled in _handle_client after this returns OK).
        with self.lock:
            if current_session.token in self.active_sessions:
                del self.active_sessions[current_session.token]
            if username in self.active_clients:
                del self.active_clients[username]
        self.db.delete_session(current_session.token)

        logger.info(f"User logged out: {username}")

        return self._create_ok_response(msg_id)

    def _handle_create_group(self, message: Message, current_session: Session,
                           msg_id: str) -> Message:
        """
        Handle group creation.

        Expected headers: Group-Name
        Expected body: None

        Args:
            message: Create group message
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message
        """
        group_name = message.get_header('Group-Name')

        if not group_name:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing group name", msg_id
            )

        success, result, group_id = self.db.create_group(
            group_name, current_session.user_id
        )

        if success:
            return self._create_ok_response(
                msg_id,
                extra_headers={'Group-Id': str(group_id)}
            )
        else:
            return self._create_error_response(
                ErrorCode.CONFLICT, result, msg_id
            )

    def _handle_join_group(self, message: Message, current_session: Session,
                          msg_id: str) -> Message:
        """
        Handle joining a group.

        Expected headers: Group-Name
        Expected body: None

        Args:
            message: Join group message
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message
        """
        group_name = message.get_header('Group-Name')

        if not group_name:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing group name", msg_id
            )

        success, result = self.db.join_group(group_name, current_session.user_id)

        if success:
            return self._create_ok_response(msg_id)
        else:
            if "not found" in result.lower():
                return self._create_error_response(
                    ErrorCode.NOT_FOUND, result, msg_id
                )
            else:
                return self._create_error_response(
                    ErrorCode.FORBIDDEN, result, msg_id
                )

    def _handle_leave_group(self, message: Message, current_session: Session,
                           msg_id: str) -> Message:
        """
        Handle leaving a group.

        Expected headers: Group-Name
        Expected body: None

        Args:
            message: Leave group message
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message
        """
        group_name = message.get_header('Group-Name')

        if not group_name:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing group name", msg_id
            )

        success, result = self.db.leave_group(group_name, current_session.user_id)

        if success:
            return self._create_ok_response(msg_id)
        else:
            if "not found" in result.lower():
                return self._create_error_response(
                    ErrorCode.NOT_FOUND, result, msg_id
                )
            else:
                return self._create_error_response(
                    ErrorCode.FORBIDDEN, result, msg_id
                )

    def _handle_send_text(self, message: Message, current_session: Session,
                         msg_id: str) -> Message:
        """
        Handle sending a text message to another user.

        Expected headers: To (recipient username)
        Expected body: Text message

        Args:
            message: Send text message
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message
        """
        recipient = message.get_header('To')
        text = message.get_body_text()

        if not recipient:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing recipient", msg_id
            )

        if not text:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Empty message", msg_id
            )

        # Check if recipient exists
        if not self.db.username_exists(recipient):
            return self._create_error_response(
                ErrorCode.NOT_FOUND, "User not found", msg_id
            )

        # Get recipient user
        recipient_user = self.db.get_user_by_username(recipient)
        if not recipient_user:
            return self._create_error_response(
                ErrorCode.NOT_FOUND, "User not found", msg_id
            )

        # Save message to database
        self.db.save_message(
            sender_id=current_session.user_id,
            recipient_id=recipient_user.id,
            group_id=None,
            message_type='TEXT',
            content=text
        )

        # Forward message to recipient
        self._forward_message(
            from_user=current_session.username,
            to_user=recipient,
            text=text
        )

        logger.info(f"Text message from {current_session.username} to {recipient}")

        return self._create_ok_response(msg_id)

    def _handle_send_group_text(self, message: Message, current_session: Session,
                               msg_id: str) -> Message:
        """
        Handle sending a text message to a group.

        Expected headers: Group
        Expected body: Text message

        Args:
            message: Send group text message
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message
        """
        group_name = message.get_header('Group')
        text = message.get_body_text()

        if not group_name:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing group name", msg_id
            )

        if not text:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Empty message", msg_id
            )

        # Get group
        group = self.db.get_group_by_name(group_name)
        if not group:
            return self._create_error_response(
                ErrorCode.NOT_FOUND, "Group not found", msg_id
            )

        # Check if user is a member
        if not self.db.is_member(group_name, current_session.user_id):
            return self._create_error_response(
                ErrorCode.FORBIDDEN, "Not a member of this group", msg_id
            )

        # Save message to database
        self.db.save_message(
            sender_id=current_session.user_id,
            recipient_id=None,
            group_id=group.id,
            message_type='TEXT',
            content=text
        )

        # Get group members
        members = self.db.get_group_members(group_name)

        # Forward message to all members
        for member in members:
            if member != current_session.username:  # Don't send to self
                self._forward_message(
                    from_user=current_session.username,
                    to_user=member,
                    group=group_name,
                    text=text
                )

        logger.info(f"Group message from {current_session.username} to {group_name}")

        return self._create_ok_response(msg_id)

    def _handle_list_groups(self, current_session: Session, msg_id: str) -> Message:
        """
        Handle listing available groups.

        Args:
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message with group list
        """
        # Get all groups
        groups = self.db.get_all_groups()

        # Build response
        group_list = ','.join(groups) if groups else ""

        return self._create_ok_response(
            msg_id,
            extra_headers={'Groups': group_list}
        )

    def _handle_list_users(self, current_session: Session, msg_id: str) -> Message:
        """
        Handle listing registered users.

        Args:
            current_session: Current session
            msg_id: Message ID

        Returns:
            Response message with user list
        """
        # Get all users
        users = self.db.get_all_users()

        # Build response
        user_list = ','.join(users) if users else ""

        return self._create_ok_response(
            msg_id,
            extra_headers={'Users': user_list}
        )

    def _handle_list_group_members(self, message: Message, current_session: Session,
                              msg_id: str) -> Message:
        """
        Handles listing members of a specific group.

        Expected headers:
            Group-Name: Name of the target group

        Args:
            message: Incoming command message
            current_session: Current authenticated session
            msg_id: Message ID

        Returns:
            OK response with Members header if successful,
            otherwise an ERROR response
        """
        group_name = message.get_header('Group-Name')

        if not group_name:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing group name", msg_id
            )

        group = self.db.get_group_by_name(group_name)
        if not group:
            return self._create_error_response(
                ErrorCode.NOT_FOUND, "Group not found", msg_id
            )

        """#only members can view members
        if not self.db.is_member(group_name, current_session.user_id):
            return self._create_error_response(
                ErrorCode.FORBIDDEN, "Not a member of this group", msg_id
            )
        """

        members = self.db.get_group_members(group_name)
        members_str = ",".join(members)

        return self._create_ok_response(
            msg_id,
            extra_headers={'Members': members_str}
        )

    def _handle_get_user_info(self, message: Message, current_session: Session,
                             msg_id: str) -> Message:
        """
        Return the IP address and UDP port for an online user.

        Used by clients to establish P2P UDP file transfers.

        Expected headers: Username
        """
        username = message.get_header('Username')

        if not username:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Username required", msg_id
            )

        with self.lock:
            for token, session in self.active_sessions.items():
                if session.username == username:
                    return self._create_ok_response(
                        msg_id,
                        extra_headers={
                            'IP-Address': session.ip_address,
                            'UDP-Port': str(session.udp_port) if session.udp_port else '0'
                        }
                    )

        return self._create_error_response(
            ErrorCode.NOT_FOUND, f"User '{username}' not found or not online", msg_id
        )
    

    def _handle_get_group_members(self, message: Message, current_session: Session,
                                msg_id: str) -> Message:
        """
        Return the usernames of all members in a group.

        Used by client before sending a group UDP file so it can
        discover which users belong to the target group.

        Expected headers:
            Group-Name: Name of the group

        Args:
            message: Incoming command message
            current_session: Authenticated session of the requesting user
            msg_id: Message ID used for the response

        Returns:
            OK response with a Members header containing a comma-separated
            list of usernames, or an ERROR response if group not found.
        """
        group_name = message.get_header('Group-Name')

        if not group_name:
            return self._create_error_response(
                ErrorCode.BAD_REQUEST, "Missing group name", msg_id
            )

        group = self.db.get_group_by_name(group_name)
        if not group:
            return self._create_error_response(
                ErrorCode.NOT_FOUND, "Group not found", msg_id
            )

        if not self.db.is_member(group_name, current_session.user_id):
            return self._create_error_response(
                ErrorCode.FORBIDDEN, "Not a member of this group", msg_id
            )

        members = self.db.get_group_members(group_name)
        members_str = ",".join(members)

        return self._create_ok_response(
            msg_id,
            extra_headers={'Members': members_str}
        )


    # ==================== Message Forwarding ====================

    def _forward_message(self, from_user: str, to_user: str = None,
                        group: str = None, text: str = None) -> bool:
        """
        Forward a message to a recipient.

        Args:
            from_user: Sender username
            to_user: Recipient username (for 1-to-1)
            group: Group name (for group chat)
            text: Message text

        Returns:
            True if forwarded successfully
        """
        # Get recipient's socket
        with self.lock:
            if to_user and to_user in self.active_clients:
                sock, _ = self.active_clients[to_user]

                # Encode and send message
                from protocol import MessageEncoder
                if group:
                    msg_data = MessageEncoder.encode_group_text(
                        from_user=from_user,
                        group_name=group,
                        text=text
                    )
                else:
                    msg_data = MessageEncoder.encode_text(
                        from_user=from_user,
                        to_user=to_user,
                        text=text
                    )

                try:
                    sock.sendall(msg_data)
                    return True
                except Exception as e:
                    logger.error(f"Error forwarding message: {e}")

        return False

    # ==================== Response Helpers ====================

    def _create_ok_response(self, in_reply_to: str,
                           extra_headers: dict = None) -> Message:
        """
        Create an OK response message.

        Args:
            in_reply_to: Message ID being replied to
            extra_headers: Additional headers

        Returns:
            OK message
        """
        headers = {'In-Reply-To': in_reply_to}
        if extra_headers:
            headers.update(extra_headers)
        return Message(
            category=MessageCategory.CTRL,
            type=MessageType.OK,
            headers=headers
        )

    def _create_error_response(self, code: ErrorCode, reason: str,
                              in_reply_to: str) -> Message:
        """
        Create an error response message.

        Args:
            code: Error code
            reason: Error reason
            in_reply_to: Message ID being replied to

        Returns:
            Error message
        """
        return Message(
            category=MessageCategory.CTRL,
            type=MessageType.ERROR,
            headers={
                'In-Reply-To': in_reply_to,
                'Code': str(code.value),
                'Reason': reason
            }
        )


# Need to import MessageEncoder for _forward_message
from protocol import MessageEncoder


def main():
    """
    Main entry point for the chat server.
    """
    import argparse

    parser = argparse.ArgumentParser(description='Chat Server')
    parser.add_argument('--host', default='0.0.0.0', help='Server host')
    parser.add_argument('--port', type=int, default=8888, help='Server port')
    args = parser.parse_args()

    server = ChatServer(host=args.host, port=args.port)

    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server interrupted")
        server.stop()
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

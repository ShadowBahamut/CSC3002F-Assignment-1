#!/usr/bin/env python3
"""
Chat Client module for the chat application.

The module implements the TCP client that connects to the chat server,
handles user authentication, and provides a command-line interface for
sending and receiving messages (one-to-one and group chat) as well as UDP one-to-one file transfers.

Author: Group 68 (Anson Vattakunnel, Daniel Yu, Reece Baker)
Date: 03/06/26
"""

import socket
import threading
import sys
import logging
import shlex
import os
import time
from typing import Optional, Dict
from datetime import datetime

from models import Message, MessageCategory, MessageType, ErrorCode
from protocol import ProtocolHandler, MessageEncoder


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ChatClient')


class ChatClient:
    """
    TCP Chat Client implementation.

    This client provides a command-line interface for:
    - User registration and login
    - One-to-one messaging
    - Group chat messaging
    - Group management (create, join, leave)
    - One-to-one file transfers
    - Listing users and groups
    - Help Page

    Attributes:
        host: Server host address
        port: Server port number
        socket: TCP socket connection
        protocol: Protocol handler
        session_token: Current session token
        username: Current username
        running: Client running state
    """

    def __init__(self, host: str = 'localhost', port: int = 8888):
        """
        Initializes the chat client and setup client values.

        Args:
            host: Server host address
            port: Server port number
        """
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.protocol = ProtocolHandler()
        self.session_token: Optional[str] = None
        self.username: Optional[str] = None
        self.running = False
        self.lock = threading.Lock()

        # UDP for P2P file transfers
        self.udp_socket: Optional[socket.socket] = None
        self.udp_port: Optional[int] = None
        self.udp_running = False
        self.file_transfers: Dict[str, dict] = {}

        import queue
        self.response_queue: queue.Queue = queue.Queue()

        logger.info(f"ChatClient initialized for {host}:{port}")

    def connect(self) -> bool:
        """
        Connects to chat server.

        Returns:
            True if connection successful
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            logger.info(f"Connected to {self.host}:{self.port}")
            return True

        except socket.error as e:
            logger.error(f"Connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """
        Disconnects from the server.
        """
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass

        self.socket = None
        self.session_token = None
        self.username = None

        # Stops UDP listener and closes and cleans up UDP socket
        self.udp_running = False
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except Exception:
                pass
        self.udp_socket = None
        self.udp_port = None
        self.file_transfers.clear()

        logger.info("Disconnected from server")

    def _init_udp(self) -> bool:
        """
        Initialize UDP socket for P2P file transfers.

        Returns:
            True if UDP socket initialized successfully
        """
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.bind(('0.0.0.0', 0))
            self.udp_socket.settimeout(1.0)
            self.udp_port = self.udp_socket.getsockname()[1]
            self.udp_running = True
            threading.Thread(target=self._udp_listener, daemon=True).start()
            logger.info(f"UDP socket initialized on port {self.udp_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize UDP socket: {e}")
            return False

    def _udp_listener(self) -> None:
        """
        Listens for incoming UDP packets (file chunks) in a daemon thread.
        """
        while self.udp_socket and self.udp_running:
            try:
                data, addr = self.udp_socket.recvfrom(65536)
                self._handle_udp_packet(data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self.udp_running:
                    logger.error(f"UDP listener error: {e}")
                break

    def _handle_udp_packet(self, data: bytes, addr: tuple) -> None:
        """
        Handles one incoming UDP file-chunk packet.
        Packet format: transfer_id:chunk_num:total_chunks:filename:<base64 data>
        """
        try:
            parts = data.split(b':', 4)
            if len(parts) != 5:
                logger.error("Invalid UDP packet format")
                return

            import base64
            transfer_id  = parts[0].decode('utf-8')
            chunk_num    = int(parts[1].decode('utf-8'))
            total_chunks = int(parts[2].decode('utf-8'))
            filename     = parts[3].decode('utf-8')
            chunk_data   = base64.b64decode(parts[4])

            with self.lock:
                if transfer_id not in self.file_transfers:
                    self.file_transfers[transfer_id] = {
                        'filename': f"received_{filename}",
                        'chunks': {},
                        'total_chunks': total_chunks,
                        'sender_addr': addr
                    }
                transfer = self.file_transfers[transfer_id]
                transfer['chunks'][chunk_num] = chunk_data
                #show progress
                print(f"\rReceiving {transfer['filename']}: "
                      f"{len(transfer['chunks'])}/{total_chunks} chunks", end="")
                #if all received, rebuild file
                if len(transfer['chunks']) == total_chunks:
                    print()
                    self._assemble_file(transfer_id)

        except Exception as e:
            logger.error(f"Error handling UDP packet: {e}")

    def _assemble_file(self, transfer_id: str) -> None:
        """
        Assemble received chunks into a complete file and clean up state.
        """
        transfer = self.file_transfers[transfer_id]
        filename = transfer['filename']
        try:
            with open(filename, 'wb') as f:
                for i in range(transfer['total_chunks']):
                    if i in transfer['chunks']:
                        f.write(transfer['chunks'][i])
                    else:
                        logger.error(f"Missing chunk {i} for transfer {transfer_id}")
                        return
            print(f"File received: {filename}")
            logger.info(f"File transfer {transfer_id} completed: {filename}")
        except Exception as e:
            logger.error(f"Error assembling file {filename}: {e}")
        finally:
            del self.file_transfers[transfer_id]

    def send_file(self, recipient: str, filepath: str) -> bool:
        """
        Send a file to another user via UDP P2P.
        Looks up the recipient's UDP endpoint via GET_USER_INFO, then
        sends file in base64-encoded chunks.

        Args:
            recipient: Recipient username
            filepath: Path to the file to send

        Returns:
            True if file sent successfully
        """
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            return False
        if not self.udp_socket:
            print("UDP not initialized — please login first")
            return False

        # Look up recipient's IP and UDP port from the server
        user_info = self._get_user_info(recipient)
        if not user_info:
            print(f"Could not find UDP info for '{recipient}' — are they online?")
            return False
        recipient_ip, recipient_udp_port = user_info

        try:
            import uuid, math, base64
            filename    = os.path.basename(filepath)
            file_size   = os.path.getsize(filepath)
            transfer_id = str(uuid.uuid4())
            chunk_size  = 1000
            total_chunks = math.ceil(file_size / chunk_size)

            print(f"Sending {filename} ({file_size} bytes) to {recipient} "
                  f"({recipient_ip}:{recipient_udp_port}) in {total_chunks} chunks")

            with open(filepath, 'rb') as f:
                for chunk_num in range(total_chunks):
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    header = f"{transfer_id}:{chunk_num}:{total_chunks}:{filename}:".encode()
                    packet = header + base64.b64encode(chunk)
                    self.udp_socket.sendto(packet, (recipient_ip, recipient_udp_port))
                    print(f"\rSent {chunk_num + 1}/{total_chunks} chunks", end="")
                    time.sleep(0.005)

            print(f"\nFile '{filename}' sent successfully!")
            return True

        except Exception as e:
            logger.error(f"Error sending file: {e}")
            print(f"Failed to send file: {e}")
            return False

    def _get_user_info(self, username: str) -> Optional[tuple]:
        """
        Query the server for a user's IP and UDP port.

        Returns:
            (ip, udp_port) tuple or None if not found / not online
        """
        success, ip, udp_port = self.get_user_info(username)
        if success and ip and udp_port:
            return (ip, udp_port)
        return None

    def get_user_info(self, username: str) -> tuple:
        """
        Send a GET_USER_INFO command to the server.

        Returns:
            Tuple of (success, ip, udp_port)
        """
        if not self.session_token:
            return False, None, None

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.GET_USER_INFO
        )
        message.set_header('Session', self.session_token)
        message.set_header('Username', username)

        response = self.send_message(message)
        if not response:
            return False, None, None

        if response.type == MessageType.OK:
            ip = response.get_header('IP-Address')
            udp_port_str = response.get_header('UDP-Port', '0')
            try:
                udp_port = int(udp_port_str) if udp_port_str != '0' else None
                return True, ip, udp_port
            except ValueError:
                return False, None, None
        return False, None, None

    def send_message(self, message: Message) -> Optional[Message]:
        """
        Send a message and wait for response.

        If the background listener thread is active, responses arrive via
        response_queue (avoids a race condition between the listener and this call).
        Before the listener starts (pre-login), we read the response directly.
        """
        if not self.socket:
            logger.error("Not connected to server")
            return None

        try:
            self.protocol.send_message(self.socket, message)

            if self.running:
                #listener thread active — wait on the queue
                import queue as _queue
                try:
                    return self.response_queue.get(timeout=5.0)
                except _queue.Empty:
                    logger.error("Request timed out")
                    return None
            else:
                #no listener yet — read directly from socket
                return self.protocol.receive_message_buffered(self.socket)

        except socket.timeout:
            logger.error("Request timed out")
            return None
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None

    def register(self, username: str, password: str) -> tuple:
        """
        Registers new user account.

        Args:
            username: Desired username
            password: Password

        Returns:
            Tuple of (success, message)
        """
        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.REGISTER
        )
        message.set_header('Username', username)
        message.set_header('Password', password)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            return True, "Registration successful"
        else:
            reason = response.get_header('Reason', 'Registration failed')
            return False, reason

    def login(self, username: str, password: str) -> tuple:
        """
        Login to server.

        Args:
            username: Username
            password: Password

        Returns:
            Tuple of (success, message, session_token)
        """
        # Initialize UDP before login so we can advertise our UDP port
        if not self.udp_socket:
            if not self._init_udp():
                return False, "Failed to initialize UDP socket", None

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.LOGIN
        )
        message.set_header('Username', username)
        message.set_header('Password', password)
        message.set_header('UDP-Port', str(self.udp_port))

        response = self.send_message(message)

        if not response:
            return False, "Server not responding", None

        if response.type == MessageType.OK:
            session_token = response.get_header('Session-Token')
            self.session_token = session_token
            self.username = username
            return True, "Login successful", session_token
        else:
            reason = response.get_header('Reason', 'Login failed')
            return False, reason, None

    def logout(self) -> bool:
        """
        Logout from the server.

        Returns:
            True if successful
        """
        if not self.session_token:
            return False

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.LOGOUT
        )
        message.set_header('Session', self.session_token)

        response = self.send_message(message)

        self.session_token = None
        self.username = None

        if response and response.type == MessageType.OK:
            return True

        return False

    def create_group(self, group_name: str) -> tuple:
        """
        Create new chat group.

        Args:
            group_name: Name for the new group

        Returns:
            Tuple of (success, message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.CREATE_GROUP
        )
        message.set_header('Session', self.session_token)
        message.set_header('Group-Name', group_name)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            return True, f"Group '{group_name}' created"
        else:
            reason = response.get_header('Reason', 'Failed to create group')
            return False, reason

    def join_group(self, group_name: str) -> tuple:
        """
        Join existing group chat.

        Args:
            group_name: Name of the group to join

        Returns:
            Tuple of (success, message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.JOIN_GROUP
        )
        message.set_header('Session', self.session_token)
        message.set_header('Group-Name', group_name)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            return True, f"Joined group '{group_name}'"
        else:
            reason = response.get_header('Reason', 'Failed to join group')
            return False, reason

    def leave_group(self, group_name: str) -> tuple:
        """
        Leave group chat.

        Args:
            group_name: Name of group to leave

        Returns:
            Tuple of (success, message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.LEAVE_GROUP
        )
        message.set_header('Session', self.session_token)
        message.set_header('Group-Name', group_name)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            return True, f"Left group '{group_name}'"
        else:
            reason = response.get_header('Reason', 'Failed to leave group')
            return False, reason

    def send_text(self, recipient: str, text: str) -> tuple:
        """
        Send text message to a user.

        Args:
            recipient: Recipient username
            text: Message text

        Returns:
            Tuple of (success, message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.SEND_TEXT
        )
        message.set_header('Session', self.session_token)
        message.set_header('To', recipient)
        message.body = text.encode('utf-8')

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            return True, f"Message sent to {recipient}"
        else:
            reason = response.get_header('Reason', 'Failed to send message')
            return False, reason

    def send_group_text(self, group_name: str, text: str) -> tuple:
        """
        Send text message to a group.

        Args:
            group_name: Group name
            text: Message text

        Returns:
            Tuple of (success, message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.SEND_GROUP_TEXT
        )
        message.set_header('Session', self.session_token)
        message.set_header('Group', group_name)
        message.body = text.encode('utf-8')

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            return True, f"Message sent to group '{group_name}'"
        else:
            reason = response.get_header('Reason', 'Failed to send message')
            return False, reason

    def list_groups(self) -> tuple:
        """
        List all groups.

        Returns:
            Tuple of (success, groups_list_or_error_message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.LIST_GROUPS
        )
        message.set_header('Session', self.session_token)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            groups_str = response.get_header('Groups', '')
            groups = groups_str.split(',') if groups_str else []
            return True, groups
        else:
            reason = response.get_header('Reason', 'Failed to list groups')
            return False, reason

    def list_users(self) -> tuple:
        """
        List all registered users.

        Returns:
            Tuple of (success, users_list_or_error_message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.LIST_USERS
        )
        message.set_header('Session', self.session_token)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            users_str = response.get_header('Users', '')
            users = users_str.split(',') if users_str else []
            return True, users
        else:
            reason = response.get_header('Reason', 'Failed to list users')
            return False, reason

    def _check_session(self) -> bool:
        """
        Check if user has an active session.

        Returns:
            True if session is active
        """
        return bool(self.session_token)

    # ==================== Incoming Message Handling ====================

    def start_listening(self) -> None:
        """
        Starts listening for incoming messages in a separate thread.
        """
        self.running = True

        listener_thread = threading.Thread(target=self._listen_for_messages, daemon=True)
        listener_thread.start()

    def _listen_for_messages(self) -> None:
        """
        Listen for incoming messages from the server.

        Runs in a separate daemon thread.
        """
        while self.running and self.socket:
            try:
                # Set a timeout to allow checking running state
                self.socket.settimeout(1.0)

                try:
                    message = self.protocol.receive_message_buffered(self.socket)
                    # CTRL messages are responses to commands — route to queue
                    if message.category == MessageCategory.CTRL:
                        self.response_queue.put(message)
                    else:
                        self._handle_incoming_message(message)
                except socket.timeout:
                    continue

            except Exception as e:
                if self.running:
                    logger.error(f"Error listening for messages: {e}")
                break

        logger.info("Message listener stopped")

    def _handle_incoming_message(self, message: Message) -> None:
        """
        Handles incoming message from server.

        Args:
            message: Received message
        """
        if message.category == MessageCategory.DATA:
            if message.type == MessageType.TEXT:
                # Incoming text message
                from_user = message.get_header('From')
                group = message.get_header('Group')
                text = message.get_body_text()

                if group:
                    print(f"\n[Group: {group}] {from_user}: {text}")
                else:
                    print(f"\n[PM] {from_user}: {text}")

                print("> ", end="", flush=True)

        elif message.category == MessageCategory.CTRL:
            if message.type == MessageType.ERROR:
                code = message.get_header('Code')
                reason = message.get_header('Reason')
                print(f"\n[Error {code}] {reason}")
                print("> ", end="", flush=True)

    # ==================== Command Line Interface ====================

    def run_cli(self) -> None:
        """
        Run the command-line interface.
        """
        print("=" * 50)
        print("Welcome to Chat Client")
        print("=" * 50)
        print("Commands:")
        print("  register <username> <password>  - Register new account")
        print("  login <username> <password>     - Login to server")
        print("  logout                          - Logout from server")
        print("  create-group <name>             - Create a new group")
        print("  join-group <name>               - Join an existing group")
        print("  leave-group <name>              - Leave a group")
        print("  msg <user> <message>           - Send private message")
        print("  group-msg <group> <message>    - Send group message")
        print("  send-file <user> <filepath>     - Send file to user via UDP")
        print("  list-users                      - List all users")
        print("  list-groups                     - List all groups")
        print("  help                            - Show this help")
        print("  quit                            - Exit client")
        print("=" * 50)

        while True:
            try:
                command = input("\n> ").strip()

                if not command:
                    continue

                # Split commands safely
                try:
                    parts = shlex.split(command)
                except ValueError:
                    parts = command.split()

                cmd = parts[0].lower()

                # Handle commands
                if cmd == "register":
                    if len(parts) != 3:
                        print("Usage: register <username> <password>")
                    else:
                        if not self.socket:
                            if not self.connect():
                                print("Failed to connect to server")
                                continue
                        success, msg = self.register(parts[1], parts[2])
                        print(msg)

                elif cmd == "login":
                    if len(parts) != 3:
                        print("Usage: login <username> <password>")
                    else:
                        if not self.socket:
                            if not self.connect():
                                print("Failed to connect to server")
                                continue

                        success, msg, token = self.login(parts[1], parts[2])
                        print(msg)

                        if success:
                            self.start_listening()
                            print(f"Logged in as {parts[1]}")
                        else:
                            self.disconnect()

                elif cmd == "logout":
                    if self.logout():
                        print("Logged out")
                    else:
                        print("Not logged in")

                elif cmd == "create-group":
                    if len(parts) != 2:
                        print("Usage: create-group <name>")
                    else:
                        success, msg = self.create_group(parts[1])
                        print(msg)

                elif cmd == "join-group":
                    if len(parts) != 2:
                        print("Usage: join-group <name>")
                    else:
                        success, msg = self.join_group(parts[1])
                        print(msg)

                elif cmd == "leave-group":
                    if len(parts) != 2:
                        print("Usage: leave-group <name>")
                    else:
                        success, msg = self.leave_group(parts[1])
                        print(msg)

                elif cmd == "msg":
                    if len(parts) < 3:
                        print("Usage: msg <user> <message>")
                    else:
                        recipient = parts[1]
                        text = ' '.join(parts[2:])
                        success, msg = self.send_text(recipient, text)
                        print(msg)

                elif cmd == "group-msg":
                    if len(parts) < 3:
                        print("Usage: group-msg <group> <message>")
                    else:
                        group = parts[1]
                        text = ' '.join(parts[2:])
                        success, msg = self.send_group_text(group, text)
                        print(msg)

                elif cmd == "send-file":
                    if len(parts) < 3:
                        print("Usage: send-file <user> <filepath>")
                    else:
                        recipient = parts[1]
                        filepath = ' '.join(parts[2:])
                        self.send_file(recipient, filepath)

                elif cmd == "list-users":
                    success, result = self.list_users()
                    if success:
                        print("Users:")
                        for user in result:
                            print(f"  - {user}")
                    else:
                        print(result)

                elif cmd == "list-groups":
                    success, result = self.list_groups()
                    if success:
                        print("Groups:")
                        for group in result:
                            print(f"  - {group}")
                    else:
                        print(result)

                elif cmd == "help":
                    print("Commands:")
                    print("  register <username> <password>  - Register new account")
                    print("  login <username> <password>     - Login to server")
                    print("  logout                          - Logout from server")
                    print("  create-group <name>             - Create a new group")
                    print("  join-group <name>              - Join an existing group")
                    print("  leave-group <name>             - Leave a group")
                    print("  msg <user> <message>           - Send private message")
                    print("  group-msg <group> <message>    - Send group message")
                    print("  send-file <user> <filepath>    - Send file to user via UDP")
                    print("  list-users                     - List all users")
                    print("  list-groups                    - List all groups")
                    print("  help                           - Show this help")
                    print("  quit                           - Exit client")

                elif cmd in ["quit", "exit"]:
                    print("Goodbye!")
                    break

                else:
                    print(f"Unknown command: {cmd}")
                    print("Type 'help' for available commands")

            except KeyboardInterrupt:
                print("\nInterrupted")
                break
            except EOFError:
                break
            except Exception as e:
                print(f"Error: {e}")

        # Clean up before exiting
        self.running = False
        if self.session_token:
            self.logout()
        self.disconnect()


def main():
    """
    Main entry point for the chat client.
    """
    import argparse

    parser = argparse.ArgumentParser(description='Chat Client')
    parser.add_argument('--host', default='localhost', help='Server host')
    parser.add_argument('--port', type=int, default=8888, help='Server port')
    args = parser.parse_args()

    client = ChatClient(host=args.host, port=args.port)
    client.run_cli()


if __name__ == '__main__':
    main()

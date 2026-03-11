#!/usr/bin/env python3
"""
Chat Client module for the chat application.

The module implements the TCP client that connects to the chat server,
handles user authentication, and provides a command-line interface for
sending and receiving messages (one-to-one and group chat) as well as UDP one-to-one and group file transfers.

Author: Group 68 (Anson Vattakunnel, Daniel Yu, Reece Baker)
Date: 13/3/26
"""

import socket
import threading
import sys
import logging
import shlex
import os
import time
import uuid
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
    - One-to-one and group file transfers
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
        self.pending_lock = threading.Lock()
        self.command_lock = threading.Lock()

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
        Handles one incoming UDP file-transfer packet.

        Packet format:
            FILE|transfer_id|sender|target_type|target_name|filename|chunk_num|total_chunks\\n
            <raw binary chunk>

        Method stores each received chunk in memory until all chunks for
        the transfer have arrived, then reassembles the file.

        Args:
            data: Raw UDP packet bytes
            addr: Sender UDP address tuple

        Returns:
            None
        """
        try:
            header_bytes, chunk_data = data.split(b'\n', 1)
            header = header_bytes.decode('utf-8')
            parts = header.split('|')

            if len(parts) != 8 or parts[0] != "FILE":
                logger.error("Invalid UDP packet format")
                return

            _, transfer_id, sender, target_type, target_name, filename, chunk_num, total_chunks = parts
            filename = os.path.basename(filename)
            chunk_num = int(chunk_num)
            total_chunks = int(total_chunks)

            with self.lock:
                if transfer_id not in self.file_transfers:
                    safe_name = f"received_{sender}_{transfer_id[:8]}_{filename}"
                    self.file_transfers[transfer_id] = {
                        'filename': safe_name,
                        'chunks': {},
                        'total_chunks': total_chunks,
                        'sender_addr': addr,
                        'sender': sender,
                        'target_type': target_type,
                        'target_name': target_name,
                    }

                transfer = self.file_transfers[transfer_id]
                transfer['chunks'][chunk_num] = chunk_data

                print(f"\rReceiving {transfer['filename']}: "
                    f"{len(transfer['chunks'])}/{total_chunks} chunks", end="")

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
        Sends a file to another user via UDP P2P.

        Method looks up the recipient's UDP endpoint from the server
        using GET_USER_INFO, then sends file to that endpoint in UDP
        chunks using the shared file-transfer helper.

        Args:
            recipient: Recipient username
            filepath: Path to the file to send

        Returns:
            True if the file was sent successfully, False otherwise
        """
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            return False

        if not self.udp_socket:
            print("UDP not initialized — please login first")
            return False

        user_info = self._get_user_info(recipient)
        if not user_info:
            print(f"Could not find UDP info for '{recipient}' — are they online?")
            return False

        recipient_ip, recipient_udp_port = user_info

        return self._send_file_to_endpoint(
            recipient_name=recipient,
            recipient_ip=recipient_ip,
            recipient_udp_port=recipient_udp_port,
            filepath=filepath,
            target_type="USER",
            target_name=recipient
        )
        
    def _send_file_to_endpoint(self, recipient_name: str, recipient_ip: str,
                           recipient_udp_port: int, filepath: str,
                           target_type: str = "USER",
                           target_name: str = "") -> bool:
        """
        Sends a file to specific UDP endpoint.
        Helper is used by both one-to-one file transfer and group
        file transfer. It reads file in chunks and sends each chunk
        as a UDP datagram with a simple text header and raw binary payload.

        Args:
            recipient_name: Username of the receiving user
            recipient_ip: IP address of the receiving user
            recipient_udp_port: UDP port of the receiving user
            filepath: Path to the file to send
            target_type: Transfer target type, e.g. USER or GROUP
            target_name: Target username or group name

        Returns:
            True if the file was sent without a local socket/file error,
            False otherwise
        """
        try:
            import uuid
            filename = os.path.basename(filepath)
            file_size = os.path.getsize(filepath)
            transfer_id = str(uuid.uuid4())

            chunk_size = 1200
            total_chunks = (file_size + chunk_size - 1) // chunk_size

            print(f"Sending {filename} to {recipient_name} "
                f"({recipient_ip}:{recipient_udp_port}) in {total_chunks} chunks")

            with open(filepath, 'rb') as f:
                for chunk_num in range(total_chunks):
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    header = (
                        f"FILE|{transfer_id}|{self.username}|{target_type}|"
                        f"{target_name}|{filename}|{chunk_num}|{total_chunks}\n"
                    ).encode("utf-8")

                    packet = header + chunk
                    self.udp_socket.sendto(packet, (recipient_ip, recipient_udp_port))
                    print(f"\rSent {chunk_num + 1}/{total_chunks} chunks to {recipient_name}", end="")
                    time.sleep(0.002)

            print(f"\nFile '{filename}' sent to {recipient_name}")
            return True

        except Exception as e:
            logger.error(f"Error sending file to {recipient_name}: {e}")
            print(f"Failed to send file to {recipient_name}: {e}")
            return False
        
    def send_group_file(self, group_name: str, filepath: str) -> bool:
        """
        Sends a file to all online members of a group via UDP P2P.

        Method first asks server for the members of the group,
        then looks up the UDP endpoint for each online member, and finally
        sends the file separately to each of those members.

        Args:
            group_name: Name of the target group
            filepath: Path to the file to send

        Returns:
            True if sending completed for all reachable recipients,
            False if there was a failure or no valid online targets
        """
        if not self._check_session():
            print("Not logged in")
            return False

        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            return False

        if not self.udp_socket:
            print("UDP not initialized — please login first")
            return False

        success, result = self.get_group_members(group_name)
        if not success:
            print(result)
            return False

        members = [m for m in result if m != self.username]
        if not members:
            print(f"No other members in group '{group_name}'")
            return False

        online_targets = []
        for member in members:
            user_info = self._get_user_info(member)
            if user_info:
                online_targets.append((member, user_info[0], user_info[1]))

        if not online_targets:
            print(f"No online members with UDP endpoints in '{group_name}'")
            return False

        print(f"Sending file to group '{group_name}' ({len(online_targets)} online members)")
        overall_success = True

        for member, ip, port in online_targets:
            ok = self._send_file_to_endpoint(
                recipient_name=member,
                recipient_ip=ip,
                recipient_udp_port=port,
                filepath=filepath,
                target_type="GROUP",
                target_name=group_name
            )
            if not ok:
                overall_success = False

        return overall_success


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
    
    def get_group_members(self, group_name: str) -> tuple:
        """
        Requests member list for a chat group from the server.

        This is used before group UDP file transfer so sender can
        determine which users should receive the file.

        Args:
            group_name: Name of the target group

        Returns:
            Tuple of (success, result), where result is either a list of
            usernames if successful or an error message if unsuccessful
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.GET_GROUP_MEMBERS
        )
        message.set_header('Session', self.session_token)
        message.set_header('Group-Name', group_name)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            members_str = response.get_header('Members', '')
            members = members_str.split(',') if members_str else []
            return True, members
        else:
            reason = response.get_header('Reason', 'Failed to get group members')
            return False, reason
        
    def send_message(self, message: Message) -> Optional[Message]:
        """
        Sends message and waits for its matching response.

        Method attaches unique message ID to each outgoing command.
        When the listener thread is active, it waits until a CTRL response
        with the matching In-Reply-To value is received. It avoids mixing
        unrelated control replies in the shared response queue.

        Args:
            message: Message to send

        Returns:
            Matching response message, or None if sending fails or times out
        """
        if not self.socket:
            logger.error("Not connected to server")
            return None

        try:
            msg_id = str(uuid.uuid4())
            message.set_header("Msg-Id", msg_id)

            with self.command_lock:
                self.protocol.send_message(self.socket, message)

                if self.running:
                    import queue as _queue
                    deadline = time.time() + 5.0
                    deferred = []

                    while time.time() < deadline:
                        remaining = max(0.0, deadline - time.time())
                        try:
                            response = self.response_queue.get(timeout=remaining)
                        except _queue.Empty:
                            logger.error("Request timed out")
                            for item in deferred:
                                self.response_queue.put(item)
                            return None

                        in_reply_to = response.get_header("In-Reply-To")
                        if in_reply_to == msg_id:
                            for item in deferred:
                                self.response_queue.put(item)
                            return response
                        else:
                            deferred.append(response)

                    logger.error("Request timed out")
                    for item in deferred:
                        self.response_queue.put(item)
                    return None

                else:
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
        self.running = False

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
        
    def list_group_members(self, group_name: str) -> tuple:
        """
        Lists members of a specific group.

        Args:
            group_name: Name of the target group

        Returns:
            Tuple of (success, members_list_or_error_message)
        """
        if not self._check_session():
            return False, "Not logged in"

        message = Message(
            category=MessageCategory.CMND,
            type=MessageType.LIST_GROUP_MEMBERS
        )
        message.set_header('Session', self.session_token)
        message.set_header('Group-Name', group_name)

        response = self.send_message(message)

        if not response:
            return False, "Server not responding"

        if response.type == MessageType.OK:
            members_str = response.get_header('Members', '')
            members = members_str.split(',') if members_str else []
            return True, members
        else:
            reason = response.get_header('Reason', 'Failed to list group members')
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
                    # Queue only command replies that reference a request
                    if message.category == MessageCategory.CTRL and message.get_header("In-Reply-To"):
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

        elif message.category == MessageCategory.CTRL:#only unsolicited CTRL messages should reach here
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
        print("  register <username> <password>     - Register new account")
        print("  login <username> <password>        - Login to server")
        print("  logout                             - Logout from server")
        print("  create-group <name>                - Create a new group")
        print("  join-group <name>                  - Join an existing group")
        print("  leave-group <name>                 - Leave a group")
        print("  msg <user> <message>               - Send private message")
        print("  group-msg <group> <message>        - Send group message")
        print("  send-file <user> <filepath>        - Send file to user via UDP")
        print("  send-group-file <group> <filepath> - Send file to all online group members via UDP")
        print("  list-users                         - List all users")
        print("  list-groups                        - List all groups")
        print("  list-group-members <group>         - List all members of a specific group")
        print("  help                               - Show this help")
        print("  quit                               - Exit client")
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

                elif cmd == "send-group-file":
                    if len(parts) < 3:
                        print("Usage: send-group-file <group> <filepath>")
                    else:
                        group_name = parts[1]
                        filepath = ' '.join(parts[2:])
                        self.send_group_file(group_name, filepath)

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

                elif cmd == "list-group-members":
                    if len(parts) != 2:
                        print("Usage: list-group-members <group>")
                    else:
                        success, result = self.list_group_members(parts[1])
                        if success:
                            print(f"Members of '{parts[1]}':")
                            for member in result:
                                print(f"  - {member}")
                        else:
                            print(result)

                elif cmd == "help":
                    print("Commands:")
                    print("  register <username> <password>     - Register new account")
                    print("  login <username> <password>        - Login to server")
                    print("  logout                             - Logout from server")
                    print("  create-group <name>                - Create a new group")
                    print("  join-group <name>                  - Join an existing group")
                    print("  leave-group <name>                 - Leave a group")
                    print("  msg <user> <message>               - Send private message")
                    print("  group-msg <group> <message>        - Send group message")
                    print("  send-file <user> <filepath>        - Send file to user via UDP")
                    print("  send-group-file <group> <filepath> - Send file to all online group members via UDP")
                    print("  list-users                         - List all users")
                    print("  list-groups                        - List all groups")
                    print("  list-group-members <group>         - List all members of a specific group")
                    print("  help                               - Show this help")
                    print("  quit                               - Exit client")

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

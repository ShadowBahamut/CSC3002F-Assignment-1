"""
Protocol module for the chat application.

This module implements the HTTP-like message protocol for the chat system.
It handles message parsing, encoding, framing, and validation according to
the specification:

    CHAT <Category> <Type>\n
    Headers: Key: Value\n
    \n
    [Body]

Author: MiniMax Agent
Date: 2026-03-03
"""

import socket
import io
from typing import Optional, Tuple
from models import (
    Message, MessageCategory, MessageType, ErrorCode
)


class ProtocolError(Exception):
    """
    Exception raised for protocol parsing errors.

    Attributes:
        code: Error code
        message: Error message
    """
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Error {code}: {message}")


class MessageParser:
    """
    Parser for the chat protocol.

    This class handles parsing of incoming byte streams into Message objects,
    implementing the framing rules described in the protocol specification.

    The parser reads until it sees \\n\\n (end of header), then reads the
    Content-Length value and reads exactly that many bytes for the body.
    """

    # Maximum header size (4KB)
    MAX_HEADER_SIZE = 4096

    # Maximum body size (10MB for text, media handled separately)
    MAX_BODY_SIZE = 10 * 1024 * 1024

    def __init__(self):
        """Initialize the parser."""
        self.buffer = b""

    def parse(self, data: bytes) -> Tuple[Optional[Message], bytes]:
        """
        Parse incoming data into a Message object.

        This method implements the framing protocol:
        1. Read until \\n\\n to get all headers
        2. Parse Content-Length to determine body size
        3. Read exactly Content-Length bytes for body

        Args:
            data: Raw bytes received from socket

        Returns:
            Tuple of (parsed Message or None if incomplete, remaining data)
        """
        self.buffer += data

        # Try to find the end of headers (blank line)
        header_end = self.buffer.find(b'\n\n')
        if header_end == -1:
            # Check if we have too much data without headers
            if len(self.buffer) > self.MAX_HEADER_SIZE:
                raise ProtocolError(400, "Header too large")
            return None, self.buffer

        # Extract header section
        header_section = self.buffer[:header_end].decode('utf-8', errors='replace')
        remaining = self.buffer[header_end + 2:]  # Skip \n\n

        # Parse the start line and headers
        try:
            message = self._parse_headers(header_section)
        except ValueError as e:
            raise ProtocolError(400, f"Invalid header format: {str(e)}")

        # Check if we have the full body
        content_length = int(message.get_header('Content-Length', '0'))

        if len(remaining) < content_length:
            # Need more data
            # Keep buffer for next call
            self.buffer = header_section.encode('utf-8') + b'\n\n' + remaining
            return None, self.buffer

        # Extract body if present
        if content_length > 0:
            if content_length > self.MAX_BODY_SIZE:
                raise ProtocolError(400, "Body too large")

            message.body = remaining[:content_length]
            remaining = remaining[content_length:]

        # Clear buffer and return message
        self.buffer = b""
        return message, remaining

    def _parse_headers(self, header_section: str) -> Message:
        """
        Parse header section into a Message object.

        Args:
            header_section: String containing start line and headers

        Returns:
            Parsed Message object

        Raises:
            ValueError: If header format is invalid
        """
        lines = header_section.strip().split('\n')
        if not lines:
            raise ValueError("Empty header")

        # Parse start line: CHAT <Category> <Type>
        start_line = lines[0].strip()
        parts = start_line.split()

        if len(parts) < 3 or parts[0] != 'CHAT':
            raise ValueError(f"Invalid start line: {start_line}")

        try:
            category = MessageCategory(parts[1])
            message_type = MessageType(parts[2])
        except ValueError as e:
            raise ValueError(f"Invalid category or type: {str(e)}")

        message = Message(category=category, type=message_type)

        # Parse headers (lines after start line)
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue

            # Headers must have a colon
            if ':' not in line:
                raise ValueError(f"Invalid header line: {line}")

            key, value = line.split(':', 1)
            message.set_header(key.strip(), value.strip())

        return message

    def parse_from_socket(self, sock: socket.socket,
                         timeout: float = 5.0) -> Message:
        """
        Read and parse a complete message from a socket.

        This is a convenience method that handles socket reads until
        a complete message is received.

        Args:
            sock: Socket to read from
            timeout: Read timeout in seconds

        Returns:
            Parsed Message object

        Raises:
            ProtocolError: If protocol parsing fails
            socket.timeout: If socket times out
        """
        sock.settimeout(timeout)

        # First, read headers
        header_data = b""
        while b'\n\n' not in header_data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ProtocolError(400, "Connection closed")
            header_data += chunk

            if len(header_data) > self.MAX_HEADER_SIZE:
                raise ProtocolError(400, "Header too large")

        # Find header end position
        header_end = header_data.find(b'\n\n')
        header_section = header_data[:header_end].decode('utf-8', errors='replace')

        # Parse headers to get content length
        message = self._parse_headers(header_section)
        content_length = int(message.get_header('Content-Length', '0'))

        # Calculate how much body data we already have
        existing_body = header_data[header_end + 2:]
        body_received = len(existing_body)

        # Read remaining body if needed
        body = bytearray(existing_body)
        while body_received < content_length:
            chunk = sock.recv(min(4096, content_length - body_received))
            if not chunk:
                raise ProtocolError(400, "Connection closed")
            body.extend(chunk)
            body_received += len(chunk)

        if content_length > 0:
            message.body = bytes(body)

        return message

    def reset(self) -> None:
        """Reset the parser state."""
        self.buffer = b""


class MessageEncoder:
    """
    Encoder for the chat protocol.

    This class handles encoding Message objects into byte streams
    for transmission over sockets.
    """

    @staticmethod
    def encode(message: Message) -> bytes:
        """
        Encode a Message object into bytes.

        Format:
            CHAT <Category> <Type>\n
            Header-Key: Header-Value\n
            \n
            [Body]

        Args:
            message: Message to encode

        Returns:
            Encoded bytes ready for transmission
        """
        # Start line
        lines = [f"CHAT {message.category.value} {message.type.value}"]

        # Add headers
        for key, value in message.headers.items():
            lines.append(f"{key}: {value}")

        # Add content length if there's a body
        if message.body:
            lines.append(f"Content-Length: {len(message.body)}")
        else:
            lines.append("Content-Length: 0")

        # Build header section
        header_section = '\n'.join(lines) + '\n\n'

        # Combine with body if present
        if message.body:
            return header_section.encode('utf-8') + message.body
        else:
            return header_section.encode('utf-8')

    @staticmethod
    def encode_error(code: ErrorCode, reason: str,
                    in_reply_to: str = "") -> bytes:
        """
        Encode an error response message.

        Args:
            code: Error code
            reason: Error reason message
            in_reply_to: Message ID being replied to

        Returns:
            Encoded error message
        """
        message = Message(
            category=MessageCategory.CTRL,
            type=MessageType.ERROR
        )
        message.set_header('Code', str(code.value))
        message.set_header('Reason', reason)
        if in_reply_to:
            message.set_header('In-Reply-To', in_reply_to)

        return MessageEncoder.encode(message)

    @staticmethod
    def encode_ok(in_reply_to: str = "", extra_headers: dict = None) -> bytes:
        """
        Encode an OK response message.

        Args:
            in_reply_to: Message ID being replied to
            extra_headers: Additional headers to include

        Returns:
            Encoded OK message
        """
        message = Message(
            category=MessageCategory.CTRL,
            type=MessageType.OK
        )
        message.set_header('In-Reply-To', in_reply_to)

        if extra_headers:
            for key, value in extra_headers.items():
                message.set_header(key, value)

        return MessageEncoder.encode(message)

    @staticmethod
    def encode_text(from_user: str, to_user: str,
                   text: str, session: str = "") -> bytes:
        """
        Encode a text message for routing.

        Args:
            from_user: Sender username
            to_user: Recipient username
            text: Message text
            session: Session token

        Returns:
            Encoded text message
        """
        message = Message(
            category=MessageCategory.DATA,
            type=MessageType.TEXT
        )
        message.set_header('From', from_user)
        message.set_header('To', to_user)
        if session:
            message.set_header('Session', session)

        message.body = text.encode('utf-8')

        return MessageEncoder.encode(message)

    @staticmethod
    def encode_group_text(from_user: str, group_name: str,
                         text: str, session: str = "") -> bytes:
        """
        Encode a group text message for routing.

        Args:
            from_user: Sender username
            group_name: Group name
            text: Message text
            session: Session token

        Returns:
            Encoded group text message
        """
        message = Message(
            category=MessageCategory.DATA,
            type=MessageType.TEXT
        )
        message.set_header('From', from_user)
        message.set_header('Group', group_name)
        if session:
            message.set_header('Session', session)

        message.body = text.encode('utf-8')

        return MessageEncoder.encode(message)


class ProtocolHandler:
    """
    High-level protocol handler combining parsing and encoding.

    This class provides a convenient interface for sending and receiving
    messages over sockets, handling all protocol details.
    """

    def __init__(self):
        """Initialize the protocol handler."""
        self.parser = MessageParser()

    def send_message(self, sock: socket.socket, message: Message) -> None:
        """
        Send a message over a socket.

        Args:
            sock: Socket to send on
            message: Message to send
        """
        data = MessageEncoder.encode(message)
        sock.sendall(data)

    def send_error(self, sock: socket.socket, code: ErrorCode,
                  reason: str, in_reply_to: str = "") -> None:
        """
        Send an error response.

        Args:
            sock: Socket to send on
            code: Error code
            reason: Error reason
            in_reply_to: Original message ID
        """
        data = MessageEncoder.encode_error(code, reason, in_reply_to)
        sock.sendall(data)

    def send_ok(self, sock: socket.socket, in_reply_to: str = "",
               extra_headers: dict = None) -> None:
        """
        Send an OK response.

        Args:
            sock: Socket to send on
            in_reply_to: Original message ID
            extra_headers: Additional headers
        """
        data = MessageEncoder.encode_ok(in_reply_to, extra_headers)
        sock.sendall(data)

    def receive_message(self, sock: socket.socket,
                       timeout: float = 5.0) -> Message:
        """
        Receive a message from a socket.

        Args:
            sock: Socket to receive from
            timeout: Read timeout

        Returns:
            Received message
        """
        return self.parser.parse_from_socket(sock, timeout)

    def receive_message_buffered(self, sock: socket.socket) -> Message:
        """
        Receive a message using buffered parsing.

        Use this when data might arrive in multiple chunks.

        Args:
            sock: Socket to receive from

        Returns:
            Received message
        """
        while True:
            # Check if there is already a complete message in the buffer
            # before blocking on recv. This handles the case where two
            # messages arrived in one chunk from a previous recv call.
            if self.parser.buffer:
                message, remaining = self.parser.parse(b"")
                if message:
                    self.parser.buffer = remaining
                    return message

            # Need more data from the socket
            chunk = sock.recv(4096)
            if not chunk:
                raise ProtocolError(400, "Connection closed")

            # Pass each chunk individually to the parser so it accumulates
            # data in self.parser.buffer only once (the old code also kept a
            # local `data` variable that grew with each iteration, causing the
            # same bytes to be added to self.parser.buffer twice).
            message, remaining = self.parser.parse(chunk)
            if message:
                self.parser.buffer = remaining
                return message


def create_command_message(command: str, headers: dict = None,
                          body: str = "") -> Message:
    """
    Convenience function to create a command message.

    Args:
        command: Command type (REGISTER, LOGIN, etc.)
        headers: Optional headers
        body: Optional body text

    Returns:
        Message object
    """
    try:
        msg_type = MessageType(command)
    except ValueError:
        raise ValueError(f"Invalid command: {command}")

    message = Message(
        category=MessageCategory.CMND,
        type=msg_type
    )

    if headers:
        for key, value in headers.items():
            message.set_header(key, value)

    if body:
        message.body = body.encode('utf-8')

    return message


def parse_message_id(message: Message) -> str:
    """
    Get or generate a message ID for a message.

    Args:
        message: Message to get ID from

    Returns:
        Message ID (from header or generated)
    """
    msg_id = message.get_header('Msg-Id')
    if not msg_id:
        import uuid
        msg_id = str(uuid.uuid4())
    return msg_id


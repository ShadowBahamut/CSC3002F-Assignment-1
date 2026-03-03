# Chat Application - Socket Programming Project

A Python-based client-server chat application implementing both TCP and UDP protocols with a custom application-layer protocol. This project was developed as part of the CSC3002F Networks Assignment at the University of Cape Town.

## Overview

This chat application supports:

- **User Authentication**: Registration and login with session management
- **One-to-One Messaging**: Private text messaging between users
- **Group Chat**: Create, join, and chat in groups (3+ users)
- **TCP Client-Server Architecture**: Reliable message routing
- **UDP Peer-to-Peer**: Media transfer capability (coordinated via server)
- **SQLite Database**: Persistent storage for users, sessions, groups, and messages

## Architecture

### Protocol Design

The application uses an HTTP-like text-based protocol:

```
CHAT <Category> <Type>
Header-Key: Header-Value

[Optional Body]
```

**Categories:**
- `CMND` - Commands (LOGIN, REGISTER, CREATE_GROUP, etc.)
- `CTRL` - Control messages (OK, ERROR, MEDIA_OFFER, etc.)
- `DATA` - Data messages (TEXT, MEDIA_CHUNK)

**Message Framing:**
- Headers end with `\n\n` (blank line)
- Body length specified via `Content-Length` header
- TCP receiver reads exactly `Content-Length` bytes for body

### Network Architecture

```
┌─────────────┐         TCP (8888)         ┌─────────────┐
│   Client    │ ◄────────────────────────► │   Server    │
│             │                           │             │
│  - Login    │                           │ - Auth      │
│  - Send Msg │                           │ - Route     │
│  - Receive  │                           │ - Groups    │
└─────────────┘                           └─────────────┘
        │                                         │
        │ UDP (P2P)                               │
        │ (Media Transfer)                        │
        ▼                                         ▼
   ┌─────────┐                              ┌─────────┐
   │ Client  │ ◄──────────────────────────► │ Client  │
   │  (P2P)  │   Direct Media Transfer     │  (P2P)  │
   └─────────┘                              └─────────┘
```

## File Structure

```
/workspace/
├── models.py      # Data structures (Message, User, Session, Group)
├── database.py    # SQLite database management
├── protocol.py    # Message parsing and encoding
├── server.py      # TCP server implementation
├── client.py      # TCP client with CLI interface
├── chat.db        # SQLite database (created on first run)
└── README.md      # This file
```

## Installation

### Requirements

- Python 3.7+
- No external dependencies (uses standard library only)

### Setup

1. Ensure you have Python 3.7 or higher installed:

```bash
python3 --version
```

2. No additional packages required - the application uses only Python standard library.

## Usage

### Starting the Server

Run the server on a machine (default port 8888):

```bash
python3 server.py
```

**Server Options:**
```bash
python3 server.py --host 0.0.0.0 --port 8888
```

- `--host`: Server bind address (default: 0.0.0.0)
- `--port`: Server port (default: 8888)

### Running the Client

Run the client to connect to the server:

```bash
python3 client.py
```

**Client Options:**
```bash
python3 client.py --host localhost --port 8888
```

- `--host`: Server address (default: localhost)
- `--port`: Server port (default: 8888)

## Client Commands

Once connected to the server, use these commands:

### Authentication

| Command | Description |
|---------|-------------|
| `register <username> <password>` | Create a new account |
| `login <username> <password>` | Login to server |
| `logout` | Logout from server |

### Group Management

| Command | Description |
|---------|-------------|
| `create-group <name>` | Create a new group |
| `join-group <name>` | Join an existing group |
| `leave-group <name>` | Leave a group |

### Messaging

| Command | Description |
|---------|-------------|
| `msg <user> <message>` | Send private message to user |
| `group-msg <group> <message>` | Send message to group |

### Information

| Command | Description |
|---------|-------------|
| `list-users` | List all registered users |
| `list-groups` | List all groups |
| `help` | Show available commands |
| `quit` | Exit client |

## Example Session

### Terminal 1 - Server
```bash
$ python3 server.py
2026-03-03 17:10:13 - ChatServer - INFO - ChatServer initialized on 0.0.0.0:8888
2026-03-03 17:10:13 - ChatServer - INFO - Server started on 0.0.0.0:8888
```

### Terminal 2 - Client A
```bash
$ python3 client.py
> register alice password123
User registered successfully
> login alice password123
Login successful
Logged in as alice
> create-group friends
Group 'friends' created
> group-msg friends Hello everyone!
Message sent to group 'friends'
```

### Terminal 3 - Client B
```bash
$ python3 client.py
> register bob password456
User registered successfully
> login bob password456
Login successful
> join-group friends
Joined group 'friends'
> list-users
Users:
  - alice
  - bob
```

## Protocol Details

### Message Format

Every message follows this structure:

```
CHAT <Category> <Type>
Header-Key: Header-Value
Another-Header: Value
Content-Length: <body_size>

<Optional Body>
```

### Common Headers

| Header | Description |
|--------|-------------|
| `Session` | Session token (after login) |
| `Username` | User's username |
| `Password` | User's password |
| `From` | Message sender |
| `To` | Message recipient (1-to-1) |
| `Group` | Group name (for group messages) |
| `Group-Name` | Group name (for group operations) |
| `Content-Length` | Body size in bytes |
| `Session-Token` | Authentication token |
| `In-Reply-To` | Original message ID |
| `Code` | Error code |
| `Reason` | Error reason |

### Error Codes

| Code | Meaning |
|------|---------|
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict |
| 500 | Internal Server Error |
| 408 | Request Timeout |

## Database Schema

### Users Table
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Sessions Table
```sql
CREATE TABLE sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    ip_address TEXT,
    udp_port INTEGER,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Groups Table
```sql
CREATE TABLE groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    owner_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Group Members Table
```sql
CREATE TABLE group_members (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, user_id)
);
```

### Messages Table
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER,
    group_id INTEGER,
    message_type TEXT NOT NULL,
    content TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Design Decisions

### TCP vs UDP

- **TCP**: Used for all client-server communication (login, messaging, group management) because reliability is critical for authentication and message delivery.

- **UDP**: Reserved for peer-to-peer media transfer where some packet loss is acceptable and low latency is preferred.

### Thread-Based Concurrency

The server uses a thread-per-client model:
- One thread accepts new connections
- One thread per connected client handles requests
- A daemon thread periodically cleans up expired sessions

This approach is suitable for the target scale of 5+ concurrent clients.

### ASCII Protocol

An HTTP-like text protocol was chosen because:
1. Human-readable for easier debugging
2. Platform-independent (no endianness issues)
3. Easy to test with telnet/netcat

## Future Enhancements

- Media transfer (images, audio, video) via UDP P2P
- End-to-end encryption
- Message history persistence on client
- File attachments
- Online status indicators
- Typing indicators

## Authors

Developed as part of CSC3002F Networks Assignment 2026
Department of Computer Science, University of Cape Town

## License

This project is for educational purposes.

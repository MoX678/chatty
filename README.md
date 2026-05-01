
Real-time multi-user chat over **plain TCP sockets** with newline-delimited
JSON. PyQt6 server GUI + PyQt6 desktop client.

## Install

```bash
pip install -r requirements.txt
```

Only PyQt6 is required — the server uses Python's standard `socket` and
`threading` modules.

## Run

```bash
python server/server.py
```

The server window opens. Click **Start** to bind on `127.0.0.1:5050`,
then click **Spawn client** to launch as many client windows as you want
from the server itself.

You can also launch clients independently:

```bash
python client/main.py
```

Sample credentials live in `server/users.json`:

| user   | password |
|--------|----------|
| ahmed  | 1234     |
| sara   | 1234     |
| omar   | 1234     |
| lina   | 1234     |

## Architecture

### Server (`server/server.py`)

- PyQt6 GUI: Start / Stop, host + port, live activity log, connected
  user list, message-routing stats, **Spawn client** button
- Plain TCP socket; one acceptor thread feeds **one thread per
  connection**. Each connection has its own `threading.Lock` for sends,
  so a slow or dead peer can never knock other clients off the server.
- All state changes are pushed to the GUI via `pyqtSignal`s (queued
  connections, fully thread-safe).

### Client (`client/main.py`, `client/network.py`)

- Login dialog → main window (sidebar + chat pane)
- A dedicated `QThread` (`NetworkClient`) owns a synchronous
  `socket.socket`, runs a blocking `readline()` loop, and emits
  `pyqtSignal`s. The Qt UI thread never blocks on I/O.
- Auto-save: every inbound message is appended to
  `client/logs/<context>.txt` in append mode (`"a"`).
- Manual save: hollow disk button in the chat header opens a
  `QFileDialog`.

## Sending images

Three ways:

- **Paste** (`Ctrl+V`) while the message input is focused — works for raw
  clipboard images (e.g. Snipping Tool / Print Screen) **and** for image
  files copied from a file explorer.
- **Drag & drop** one or more image files (`png`, `jpg`, `jpeg`, `gif`,
  `webp`, `bmp`) anywhere onto the chat window.
- **Attach button** (paperclip-style icon, left of the input) opens a
  native file picker.

Images larger than 1280 px on the longest edge are automatically
downscaled. If a PNG re-encode is still over 7 MB the client retries
as JPEG at quality 88. Anything still too big is refused with a dialog.
The server caps each JSON line at 12 MB and drops the offending
connection if exceeded.

Received images are written to
`client/logs/images/<context>/<timestamp>_<sender>.<ext>` and are
clickable in chat to open in the OS image viewer.

## Protocol

Each line is a single JSON object terminated by `\n`.

```json
{"action": "login",        "username": "...", "password": "..."}
{"action": "auth_status",  "status": "success|fail", "reason": "..."}
{"action": "user_list",    "users": ["..."]}
{"action": "system_event", "type":  "join|leave", "user": "..."}
{"action": "dm",           "sender": "...", "target": "...", "message": "..."}
{"action": "group_msg",    "sender": "...", "target": "general", "message": "..."}
```

`dm` and `group_msg` may also include an `attachment`:

```json
{
  "action": "dm",
  "sender": "ahmed",
  "target": "omar",
  "message": "look at this",
  "attachment": {
    "mime": "image/png",
    "name": "screenshot.png",
    "data": "<base64-encoded image bytes>"
  }
}
```

The server only forwards `attachment` objects whose `mime` starts with
`image/` and ignores any unknown fields.

## Project layout

```
chat app/
├── server/
│   ├── server.py        # PyQt6 GUI server, raw TCP
│   └── users.json
├── client/
│   ├── main.py          # PyQt6 GUI client
│   ├── network.py       # QThread network worker (raw TCP)
│   ├── theme.py         # tokens, QSS, hollow-gradient SVG icons
│   └── logs/            # auto-saved chat history (append mode)
├── requirements.txt
└── README.md
```
# chatty

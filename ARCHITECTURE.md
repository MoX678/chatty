# Chatty — End-to-End Architecture

A real-time chat application with a TCP server, multi-user clients, group
chat, direct messages, image attachments, and per-user persistent history.

This document walks through every flow in the system: which function gets
called, with which parameters, what it returns, and what happens next.

---

## 1. Repository layout

```
chat app/
├── server/
│   ├── server.py            # ChatServer (core) + main() entry point
│   ├── server_window.py     # ServerWindow admin GUI
│   └── users.json           # {username: password}
└── client/
    ├── main.py              # entry point (login → controller → window)
    ├── controller.py        # ChatController (state, logic, network glue)
    ├── chat_window.py       # ChatWindow (pure UI) + LoginDialog
    ├── widgets.py           # MessageRow, ImageRow, SystemRow, DMRow, MessageInput
    ├── network.py           # NetworkClient (QThread) + on-disk logging
    ├── utils.py             # constants + pure helpers
    ├── theme.py             # palette, QSS, icon factories (shared by server)
    └── logs/<username>/     # per-user persistent history
        ├── group_general.jsonl
        ├── group_general.txt
        ├── dm_<other>.jsonl
        ├── dm_<other>.txt
        └── images/<ctx>/<stamp>_<sender>.<ext>
```

---

## 2. High-level architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                         CLIENT PROCESS                             │
│                                                                    │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────┐     │
│  │  ChatWindow  │◄─┤ ChatController │◄─┤ NetworkClient       │     │
│  │  (Qt UI)     │  │ (QObject)      │  │ (QThread, sockets)  │     │
│  │              │  │                │  │                     │     │
│  │  - widgets   │  │  - histories   │  │  - recv loop        │     │
│  │  - tray      │  │  - online list │  │  - JSON dispatch    │     │
│  │  - composer  │  │  - DM partners │  │  - JSONL + txt logs │     │
│  └──────┬───────┘  └────────┬───────┘  └──────────┬──────────┘     │
│         │ user input        │ method calls        │ raw bytes      │
│         └───────────────────┴─────────────────────┘                │
└────────────────────────────────────────────────────────────────────┘
                              │ TCP (newline-delimited JSON)
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                          SERVER PROCESS                            │
│                                                                    │
│  ┌──────────────┐  ┌────────────────────────────────────────┐      │
│  │ ServerWindow │  │  ChatServer                            │      │
│  │ (Qt admin)   │◄─┤   - accept loop (thread)               │      │
│  │              │  │   - per-client thread (one per user)   │      │
│  │              │  │   - clients dict {username -> sock}    │      │
│  │              │  │   - DM / broadcast routing             │      │
│  └──────────────┘  └────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────────┘
```

**Threading model**

| Process | Thread | Role |
|---|---|---|
| Client | Qt main thread | UI + `ChatController` (a `QObject` lives wherever its owner does, here the main thread) |
| Client | `NetworkClient` (`QThread`) | Blocking socket I/O; emits queued signals to main thread |
| Server | Qt main thread | `ServerWindow` (admin dashboard) |
| Server | Accept thread | `ChatServer._accept_loop` |
| Server | One thread per client | `ChatServer._handle` |

All cross-thread communication uses **Qt queued signals**, which are
auto-detected by `pyqtSignal.connect`. The UI never touches a socket
directly.

---

## 3. Module reference

### 3.1 `client/utils.py` — pure helpers

| Symbol | Signature | Returns | Purpose |
|---|---|---|---|
| `GROUPS` | `["general"]` | `list[str]` | Hard-coded public groups |
| `DEFAULT_HOST` / `DEFAULT_PORT` | `"127.0.0.1"` / `5050` | — | Login pre-fill |
| `MAX_IMAGE_BYTES` | `7*1024*1024` | int | Encoded image cap |
| `MAX_IMAGE_EDGE` | `1280` | int | Auto-downscale longest edge |
| `IMAGE_EXTS` | `{...}` | `set[str]` | Recognised image extensions |
| `AVATAR_COLORS` | 10-color list | `list[str]` | Deterministic avatar palette |
| `now_ts()` | — | `str` | `"YYYY-MM-DD HH:MM:SS"` |
| `fmt_ts(raw)` | `raw: str` | `str` | 12-hour clock for UI bubbles |
| `avatar_pixmap(name, size=30)` | | `QPixmap` | Solid disc + initial |
| `round_pixmap(pix, radius)` | | `QPixmap` | Rounded-rect clip |
| `set_app_font(app)` | `app: QApplication` | `None` | Install global font |

### 3.2 `client/widgets.py` — small Qt widgets

All accept their data via constructor arguments and own no state beyond
their rendering:

| Class | Constructor | Notes |
|---|---|---|
| `MessageRow` | `(sender, message, is_self, ts)` | Text bubble with optional avatar |
| `ImageRow` | `(sender, path, caption, mime, name, is_self, ts)` | Image bubble; click opens via `QDesktopServices` |
| `SystemRow` | `(text, event_type="", ts="")` | Chapter-divider with colored dot + optional 12h timestamp |
| `DMRow` | `(name, online, unread)` | Sidebar item with red unread badge |
| `MessageInput(QLineEdit)` | `()` | Emits `imagePasted(QImage, mime, name)` on Ctrl+V of an image |

### 3.3 `client/network.py` — `NetworkClient(QThread)`

**Constructor:** `NetworkClient(host, port, username, password)`.
Sets `self.log_dir = user_log_dir(username)`.

**Signals (the controller subscribes to these):**

| Signal | Args | When emitted |
|---|---|---|
| `auth_success` | — | Server responded `auth_status:success` |
| `auth_failed` | `reason: str` | Server responded `auth_status:fail` |
| `connection_error` | `msg: str` | Socket / I/O error |
| `disconnected` | — | Recv loop exits cleanly |
| `user_list_changed` | `users: list[str]` | Inbound `user_list` action |
| `system_event` | `type_: str, user: str` | Inbound `system_event` (join/leave) |
| `dm_received` | `sender, target, message, attachment: dict` | Inbound `dm` |
| `group_received` | `sender, target, message, attachment: dict` | Inbound `group_msg` |

**Methods called from the UI:**

| Method | Signature | Effect |
|---|---|---|
| `start()` | — (inherited from `QThread`) | Spawns the OS thread → `run()` |
| `send_dm(target, message, attachment=None)` | strings + optional dict | Writes JSON to socket |
| `send_group(group, message, attachment=None)` | strings + optional dict | Writes JSON to socket |
| `stop()` | — | Sets stop flag and closes socket |

**Internals:**

- `run()` — connect, send `{"action":"login", ...}`, read auth response,
  then loop reading newline-delimited JSON via `self.rfile.readline()`.
- `_dispatch(msg)` — switches on `msg["action"]` and emits the matching
  signal. For `dm` and `group_msg` it first calls
  `_save_attachment(ctx, sender, att)` (decodes base64, writes the file
  to `logs/<username>/images/<ctx>/<stamp>_<sender>.<ext>`) and then
  `_log_entry(ctx, sender, text, att)` (appends to both `.jsonl` and
  `.txt` log files).
- `_log_entry` writes to `logs/<username>/<ctx>.jsonl` (canonical, used
  for replay) and `logs/<username>/<ctx>.txt` (human-readable mirror).

**Disk helpers (called from the controller and `NetworkClient` itself):**

| Function | Signature | Returns | Purpose |
|---|---|---|---|
| `user_log_dir(username)` | `str` → `str` | `client/logs/<safe>/` |
| `load_history(log_dir, ctx)` | | `list[dict]` | Replay `<ctx>.jsonl` |
| `list_dm_partners(log_dir)` | | `list[str]` | Discover past DM partners |
| `mime_for_path(path)` | `str` → `str` | MIME guess by extension |
| `ext_for_mime(mime)` | `str` → `str` | Extension from MIME |

### 3.4 `client/controller.py` — `ChatController(QObject)`

Owns **all** non-UI state.

**State:**

| Attribute | Type | Meaning |
|---|---|---|
| `histories` | `Dict[str, List[dict]]` | `ctx → list of entry dicts` |
| `online_users` | `List[str]` | Latest user list from server |
| `current_context` | `str` | E.g. `"group_general"` or `"dm_ahmed"` |
| `dm_partners` | `set[str]` | Past + currently online partners |
| `unread_dms` | `Dict[str, int]` | Inbound DM counter per partner |
| `pending_attachment` | `dict \| None` | Staged image awaiting send |

**Entry shapes (history dict):**

```python
# text message
{"kind": "msg",    "sender": str, "text": str, "ts": str}

# image message
{"kind": "image",  "sender": str, "text": str, "ts": str,
 "path": str, "name": str, "mime": str}

# system event (join / leave)
{"kind": "system", "text": str, "ts": str, "event": "join" | "leave"}
```

**Pending-attachment shape:**

```python
{"mime": str, "name": str, "data": bytes, "preview": QImage}
```

**Public methods (window calls these):**

| Method | Args | Returns | Effect |
|---|---|---|---|
| `bootstrap()` | — | `None` | Loads disk history → wires net signals → emits initial `dm_list_changed` + `history_changed(current_context)` |
| `shutdown()` | — | `None` | `net.stop()` + `net.wait(2000)` |
| `set_context(ctx)` | `str` | `None` | Switches active conversation; clears unread for DMs; emits `history_changed` |
| `subtitle_for(ctx)` | `str` | `str` | Header subtitle text |
| `dm_rows()` | — | `list[(name, online, unread)]` | Sorted (unread → online → offline) |
| `send_current(text)` | `str` | `bool` | Sends text + pending attachment to current ctx |
| `stage_qimage(image, mime, name)` | `QImage, str, str` | `bool` | Encode + downscale + store as pending |
| `stage_image_file(path)` | `str` | `bool` | Read disk + delegate to `stage_qimage` if too big |
| `clear_pending()` | — | `None` | Drops staged attachment; emits `pending_changed(None)` |
| `export_chat(ctx, path, title)` | `str, str, str` | `None` | Dump conversation to plain `.txt` |

**Signals (window subscribes):**

| Signal | Args | When |
|---|---|---|
| `history_changed` | `ctx: str` | Whole-context rerender (load / context switch) |
| `entry_appended` | `ctx: str, entry: dict` | Single new row to render if visible |
| `dm_list_changed` | — | Sidebar must rebuild |
| `subtitle_changed` | `text: str` | Header subtitle update |
| `notify_requested` | `sender, body, ctx` | Worth flashing the taskbar / tray |
| `pending_changed` | `pending \| None` | Show / hide attachment chip |
| `warning_requested` | `title, body` | Window shows `QMessageBox.warning` |
| `info_requested` | `title, body` | Window shows `QMessageBox.information` |
| `disconnected` | — | Server closed the socket |

**Network slots (auto-wired by `bootstrap`):**

| Slot | Args | Action |
|---|---|---|
| `on_user_list(users)` | `list[str]` | Updates `online_users`; emits `dm_list_changed`; updates DM subtitle |
| `on_system_event(type_, user)` | `str, str` | Appends a `system` entry to `group_general`; emits `entry_appended` |
| `on_dm(sender, target, message, attachment)` | | Appends entry; bumps unread if off-screen; emits `entry_appended`, `notify_requested`, `dm_list_changed` |
| `on_group(sender, target, message, attachment)` | | Same as `on_dm` for group context |
| `_on_net_error(msg)` | `str` | Emits `warning_requested("Network error", msg)` |

### 3.5 `client/chat_window.py` — `ChatWindow(QMainWindow)`

Pure UI. Holds references to:

- `self.ctrl: ChatController` — the application's brain
- Sidebar widgets: `groups_list`, `people_list`, `me_btn`, `me_menu`
- Header: `header_icon`, `title_lbl`, `subtitle_lbl`
- Body: `scroll`, `msg_container`, `msg_layout`
- Composer: `chip_frame`, `chip_thumb`, `chip_name`, `chip_meta`,
  `input` (a `MessageInput`), `attach_btn`, `send_btn`
- Tray: `tray` (a `QSystemTrayIcon` or `None`)

**Construction sequence (`__init__`):**

1. `_build_sidebar(layout)` — brand, groups list, DMs list, user badge.
2. `_build_chat_area(layout)` — header, scroll area, composer + chip.
3. `_build_tray()` — installs `QSystemTrayIcon` if available.
4. `_connect_controller()` — connects every controller signal to a slot.
5. `self.ctrl.bootstrap()` — controller loads history and wires network.

**Slots that react to controller signals:**

| Slot | Triggered by | Effect |
|---|---|---|
| `_on_history_changed(ctx)` | `history_changed` | Updates header, calls `_clear_messages`, renders every entry, scrolls to bottom |
| `_on_entry_appended(ctx, entry)` | `entry_appended` | Appends one row if `ctx == current_context` |
| `_refresh_dm_list()` | `dm_list_changed` | Rebuilds `people_list` from `ctrl.dm_rows()` |
| `subtitle_lbl.setText` | `subtitle_changed` | Direct slot |
| `_notify(sender, body, ctx)` | `notify_requested` | Beep, flash taskbar, tray popup if window inactive |
| `_on_pending_changed(p)` | `pending_changed` | Show / hide chip with thumb + name + size |
| `_show_warning` / `_show_info` | dialogs from controller | Forwarded to `QMessageBox` |
| `_on_disconnected()` | `disconnected` | Info dialog + close |

**User actions that call into the controller:**

| User action | Slot | Calls |
|---|---|---|
| Click sidebar item | `_on_nav_clicked(item)` | `ctrl.set_context(ctx)` |
| Press Enter in composer | `_send_clicked()` | `ctrl.send_current(text)` |
| Click 📎 attach | `_pick_image()` | `ctrl.stage_image_file(path)` |
| Ctrl+V image | `_on_image_pasted(image, mime, name)` | `ctrl.stage_qimage(...)` |
| Drop image file | `dropEvent(ev)` | `ctrl.stage_image_file` / `ctrl.stage_qimage` |
| Click ✕ on chip | direct connect | `ctrl.clear_pending` |
| Click save 💾 | `_save_chat_dialog()` | `ctrl.export_chat(ctx, path, title)` |
| Close window | `closeEvent(ev)` | `tray.hide()` + `ctrl.shutdown()` |

### 3.6 `server/server.py` — `ChatServer(QObject)`

Pure server logic. No widgets.

| Method / signal | Signature | Purpose |
|---|---|---|
| `start(host, port)` | `str, int → bool` | Bind + listen + spawn accept thread |
| `stop()` | — | Close listener + drop all clients |
| `_accept_loop()` | — | Accepts connections and spawns `_handle` per socket |
| `_handle(csock, addr)` | — | Auth, register in `clients`, read JSON, route via `_route` |
| `_route(sender, msg)` | — | Dispatch on `msg["action"]`: `"dm"` / `"group_msg"` |
| `_send_to(sock, lock, payload)` | — | Lock-guarded JSON write |
| `_broadcast(payload, exclude=None)` | — | Send to every client in `self.clients` |
| `_push_user_list()` | — | Build sorted username list + broadcast `user_list` |
| `_sanitize_attachment(att)` | — | Whitelist allowed attachment keys (`mime, name, data`) |
| `log_message` | `pyqtSignal(str, str)` | Emitted on notable events (`level`, `text`) |
| `users_changed` | `pyqtSignal(list)` | Emitted when online roster changes |
| `state_changed` | `pyqtSignal(bool, str)` | Emitted when listener starts/stops (`listening`, `"host:port"`) |

### 3.7 `server/server_window.py` — `ServerWindow(QMainWindow)`

Admin GUI. Listens to `ChatServer` signals and renders them:

- Status pill (listening / stopped)
- Host / port inputs
- Start / Stop buttons
- **Spawn Client** button — launches a detached `client/main.py` subprocess
- Colored event log (`info` / `warn` / `err`)
- Live connected-users list

No network logic; all routing lives in `ChatServer`.

---

## 4. Wire protocol

All messages are **newline-delimited JSON** over a single TCP socket
per client. Either side can send at any time after auth.

### 4.1 Client → server

```jsonc
// Login (sent immediately after connecting)
{"action": "login", "username": "ahmed", "password": "secret"}

// Direct message
{"action": "dm",
 "target": "lina",
 "message": "hey",
 "attachment": {"mime": "image/png", "name": "x.png", "data": "<base64>"}}  // optional

// Group message
{"action": "group_msg",
 "target": "general",
 "message": "hello room",
 "attachment": {...}}  // optional
```

### 4.2 Server → client

```jsonc
// Auth result (replies the login)
{"action": "auth_status", "status": "success"}
{"action": "auth_status", "status": "fail",
 "reason": "invalid_credentials"}

// Online roster (broadcast on every join/leave)
{"action": "user_list", "users": ["ahmed", "lina", "kareem"]}

// Join / leave notice (broadcast)
{"action": "system_event", "type": "join", "user": "kareem"}
{"action": "system_event", "type": "leave", "user": "kareem"}

// DM (sent to BOTH the recipient AND the sender — see "echo" below)
{"action": "dm",
 "sender": "ahmed", "target": "lina", "message": "hey",
 "attachment": {"mime": ..., "name": ..., "data": "<base64>"}}

// Group message (broadcast to every member including the sender)
{"action": "group_msg",
 "sender": "ahmed", "target": "general", "message": "hello room",
 "attachment": {...}}
```

> **Echo behaviour.** The server echoes a sent message back to its
> author. That keeps logging logic uniform: every appearance in
> `histories` and on disk happens through the inbound path
> (`_dispatch` → `_log_entry`). The sender never directly logs an
> outgoing message.

---

## 5. End-to-end flows

### 5.1 Application startup

```
main()                                                  (client/main.py)
  ├─ QApplication(sys.argv)
  ├─ set_app_font(app)                                  (utils.py)
  ├─ app.setStyleSheet(T.build_qss(T.palette(dark=True)))
  └─ loop:
        ├─ login = LoginDialog()                        (chat_window.py)
        │   └─ user types credentials and accepts
        │       returns (username, password, host, port)
        │
        ├─ net = NetworkClient(host, port, username, password)
        │
        ├─ _connect_and_authenticate(net, username):
        │   ├─ connects buf_users / buf_sys / buf_dm / buf_group slots
        │   ├─ connects auth_success / auth_failed / connection_error
        │   ├─ shows "Connecting…" QDialog
        │   ├─ net.start()       # spawns NetworkClient.run()
        │   ├─ connecting.exec() # blocks UI on the connect dialog
        │   └─ disconnects buffer slots; returns (ok, reason, buffers)
        │
        ├─ if !ok: QMessageBox.warning + retry login
        │
        ├─ ctrl = ChatController(net, username)
        ├─ win  = ChatWindow(ctrl):
        │   ├─ _build_sidebar / _build_chat_area / _build_tray
        │   ├─ _connect_controller     # wire every ctrl signal to a slot
        │   └─ ctrl.bootstrap():
        │       ├─ _load_persisted_history()
        │       │   ├─ load_history(log_dir, "group_general")
        │       │   ├─ list_dm_partners(log_dir)  → set
        │       │   └─ load_history for each partner
        │       ├─ connects net.user_list_changed → on_user_list, etc.
        │       ├─ emits dm_list_changed   → win._refresh_dm_list()
        │       └─ emits history_changed   → win._on_history_changed()
        │
        ├─ _replay_buffer(ctrl, buffers):
        │   replays buf_users → ctrl.on_user_list(...)
        │           buf_sys   → ctrl.on_system_event(...)
        │           buf_dm    → ctrl.on_dm(...)
        │           buf_group → ctrl.on_group(...)
        │
        ├─ win.show()
        └─ return app.exec()
```

### 5.2 Authentication

```
NetworkClient.run()  (worker thread)
  ├─ socket.create_connection((host, port))
  ├─ self._raw_send(json {"action":"login", username, password})
  ├─ line = rfile.readline()
  ├─ msg = json.loads(line)
  ├─ if msg.action != "auth_status" or msg.status != "success":
  │      auth_failed.emit(reason); _close(); return
  └─ auth_success.emit()
            │
            ▼
  main()._on_ok()  →  result["ok"] = True; connecting.accept()
```

### 5.3 Sending a text message

```
User types "hey" and presses Enter
       ▼
ChatWindow._send_clicked()
       ▼
ChatController.send_current(text="hey"):
   text = "hey"
   pending_attachment = None  (no staged image)
   ctx = self.current_context  e.g. "dm_ahmed"
   net.send_dm(target="ahmed", text="hey", attachment=None)
       │
       ▼
NetworkClient.send_dm:
   payload = {"action":"dm", "target":"ahmed", "message":"hey"}
   self._send_json(payload)   # writes payload + "\n" to socket
       │
       ▼
SERVER receives in _handle:
   _route(sender="lina", msg={"action":"dm","target":"ahmed","message":"hey"})
       ├─ _send_to(target_sock, target_lock, payload_with_sender)
       └─ _send_to(sender_sock, sender_lock, payload_with_sender)   # echo
       │
       ▼
NetworkClient._dispatch on each end:
   action == "dm"
   sender = "lina", target = "ahmed", text = "hey"
   other = target if sender == self.username else sender
   ctx   = f"dm_{other}"
   _save_attachment(ctx, sender, msg.get("attachment"))   # None
   _log_entry(ctx, sender, text, None)
     ├─ append_jsonl(log_dir, ctx, {"ts","kind":"msg","sender","text"})
     └─ append_text_log(log_dir, ctx, "lina: hey")
   dm_received.emit(sender, target, text, {})
       │
       ▼
ChatController.on_dm(sender, target, message, attachment={}):
   other = "ahmed"
   ctx   = "dm_ahmed"
   entry = _make_entry(sender, message, {}) → {"kind":"msg",...}
   histories["dm_ahmed"].append(entry)
   entry_appended.emit("dm_ahmed", entry)
       │
       ▼
ChatWindow._on_entry_appended("dm_ahmed", entry):
   if ctx == ctrl.current_context:
      _append_widget(_row_for_entry(entry))
      QTimer.singleShot(0, _scroll_to_bottom)
```

### 5.4 Receiving a DM (window inactive)

```
Server _route → echo to lina  → NetworkClient._dispatch
  emits dm_received("ahmed", "lina", "yo!", {})
       ▼
ChatController.on_dm:
  other = "ahmed"; ctx = "dm_ahmed"
  entry = {"kind":"msg","sender":"ahmed","text":"yo!","ts":...}
  histories["dm_ahmed"].append(entry)
  new_partner = "ahmed" not in dm_partners → maybe True
  dm_partners.add("ahmed")
  entry_appended.emit("dm_ahmed", entry)
  if current_context != "dm_ahmed" and sender != self.username:
     unread_dms["ahmed"] += 1
  notify_requested.emit("ahmed", "yo!", "dm_ahmed")
  dm_list_changed.emit()
       ▼
ChatWindow:
  _on_entry_appended  → noop because ctx isn't visible
  _refresh_dm_list    → rebuilds people_list with badge "1"
  _notify("ahmed", "yo!", "dm_ahmed"):
     QApplication.beep()
     if window inactive:
        QApplication.alert(self, 0)            # taskbar flash
        tray.showMessage("ahmed", "yo!", Information, 4000)
```

### 5.5 Switching conversations

```
User clicks "ahmed" in DIRECT MESSAGES list
       ▼
ChatWindow._on_nav_clicked(item):
  ctx = item.data(UserRole)  → "dm_ahmed"
  groups_list.clearSelection()
  ctrl.set_context("dm_ahmed")
       ▼
ChatController.set_context("dm_ahmed"):
  current_context = "dm_ahmed"
  unread_dms.pop("ahmed", 0)        # clear badge
  dm_list_changed.emit()
  history_changed.emit("dm_ahmed")
       ▼
ChatWindow:
  _refresh_dm_list      → badge gone
  _on_history_changed("dm_ahmed"):
     title_lbl.setText("ahmed")
     header_icon → user pixmap
     subtitle_lbl.setText(ctrl.subtitle_for(ctx))
     _clear_messages()
     for entry in ctrl.histories["dm_ahmed"]:
        _append_widget(_row_for_entry(entry))
     QTimer.singleShot(0, _scroll_to_bottom)
```

### 5.6 Image attachment — staging then sending

```
1. User pastes an image (Ctrl+V) into the composer
       ▼
   MessageInput.keyPressEvent detects image on clipboard
   emits imagePasted(QImage, "image/png", "pasted.png")
       ▼
   ChatWindow._on_image_pasted(image, mime, name):
       ctrl.stage_qimage(image, "image/png", "pasted.png")

2. ChatController.stage_qimage:
       if image bigger than MAX_IMAGE_EDGE: scaled = image.scaled(...)
       fmt  = "JPEG" if mime == "image/jpeg" else "PNG"
       buf  = QBuffer(); image.save(buf, fmt, ...); data = bytes(buf.data())
       if len(data) > MAX_IMAGE_BYTES and fmt == "PNG":
           recurse with mime="image/jpeg"           # try smaller JPEG
       if len(data) > MAX_IMAGE_BYTES:
           warning_requested.emit("Image too large", "...")
           return False
       _set_pending(out_mime, name, data, image):
           pending_attachment = {mime, name, data, preview}
           pending_changed.emit(pending_attachment)
       ▼
   ChatWindow._on_pending_changed(pending):
       chip_thumb.setPixmap(round_pixmap(scaled_preview, RADIUS_SM))
       chip_name.setText(pending["name"])
       chip_meta.setText(f"{mime}  ·  {kb} KB  ·  ready to send")
       chip_frame.setVisible(True)

3. User types optional caption + presses Enter
       ▼
   ChatController.send_current(caption):
       att_payload = {"mime","name","data": base64.b64encode(data).decode()}
       net.send_dm(target, caption, attachment=att_payload)
       clear_pending()             → pending_changed.emit(None) → chip hidden
       ▼
4. Server echoes the dm; NetworkClient._dispatch:
       _save_attachment(ctx, sender, att):
           decode base64 → bytes
           write to logs/<self>/images/<ctx>/<stamp>_<sender>.<ext>
           return {"mime","name","path": absolute_path}
       _log_entry(ctx, sender, caption, local_att)  # writes JSONL+txt
       dm_received.emit(sender, target, caption, local_att)
       ▼
5. ChatController.on_dm appends an "image" entry; ChatWindow renders an
   ImageRow with a clickable thumbnail.
```

### 5.7 Persistence and replay

```
On every inbound dm/group_msg:
   NetworkClient._log_entry(ctx, sender, text, att):
      ts = now_ts()
      if att: entry has kind="image" + path/name/mime (path stored relative)
      else  : entry has kind="msg"
      append_jsonl(log_dir, ctx, entry)        # canonical for replay
      append_text_log(log_dir, ctx, "<sender>: <text or [image]>")

On startup:
   ChatController._load_persisted_history():
      for g in GROUPS:
         histories[f"group_{g}"] = load_history(log_dir, f"group_{g}")
            └─ reads <ctx>.jsonl line by line, JSON-decodes, absolutises
               relative image paths, returns list[dict]
      partners = list_dm_partners(log_dir)
            └─ scans log_dir for files matching dm_*.jsonl
      for each partner: histories[f"dm_<p>"] = load_history(...)
      dm_partners = set(partners)
```

### 5.8 Shutdown

```
User closes the window
       ▼
ChatWindow.closeEvent(ev):
  tray.hide()
  ctrl.shutdown()
       ▼
ChatController.shutdown():
  net.stop()
       ├─ self._stop = True
       └─ socket.shutdown(SHUT_RDWR)   # unblocks recv loop
  net.wait(2000)
       ▼
NetworkClient.run() falls out of recv loop → _close() → disconnected.emit()
```

---

## 6. Server-side flow (one client lifetime)

```
ChatServer.start(host, port):
   socket.socket(AF_INET, SOCK_STREAM)
   sock.bind((host, port))
   sock.listen()
   threading.Thread(target=_accept_loop).start()

_accept_loop:
   while running:
      csock, addr = sock.accept()
      threading.Thread(target=_handle, args=(csock, addr)).start()

_handle(csock, addr):
   1. read first line → expect {"action":"login","username","password"}
   2. validate against _load_users():
        - bad credentials → reply auth_status:fail; close
        - duplicate username → reply auth_status:fail; close
   3. clients[username] = {"sock": csock, "lock": Lock(), "addr": addr}
   4. send auth_status:success
   5. _broadcast({"action":"system_event","type":"join","user":username})
        (includes the joiner so they see their own welcome row)
   6. _push_user_list()
   7. loop: line = rfile.readline(); _route(username, json.loads(line))
   8. on disconnect / error:
        del clients[username]
        _broadcast({"action":"system_event","type":"leave","user":username})
        _push_user_list()

_route(sender, msg):
   action = msg["action"]
   if action == "dm":
        target = msg.get("target")
        att    = _sanitize_attachment(msg.get("attachment"))
        out    = {"action":"dm","sender":sender,"target":target,"message":msg.get("message",""), ...att?}
        if target in clients:
            _send_to(clients[target].sock, .lock, out)
        _send_to(clients[sender].sock, .lock, out)   # echo
   elif action == "group_msg":
        out = {"action":"group_msg","sender":sender,"target":target,"message":msg.get("message",""), ...att?}
        _broadcast(out)
   else:
        log warn "unknown action"

---

## 7. State diagram (controller)

```
                ┌────────────────────────┐
                │     constructed        │
                │  (no signals wired)    │
                └────────────┬───────────┘
                             │ ChatWindow connects signals
                             ▼
                ┌────────────────────────┐
                │   bootstrap()          │
                │   - load disk history  │
                │   - wire net signals   │
                └────────────┬───────────┘
                             │
        ┌────────────────────┴───────────────────┐
        ▼                                         ▼
┌────────────────┐                       ┌────────────────┐
│  idle/viewing  │  set_context(ctx)     │  off-screen    │
│  ctx == cur    │◄────────────────────► │  ctx != cur    │
└──────┬─────────┘                       └──────┬─────────┘
       │ on_dm / on_group / on_system_event     │
       │  → entry_appended                      │ on_dm
       ▼                                         │  → unread_dms[other] += 1
   render row                                    │  → notify_requested
                                                 ▼
                                          render badge
                                          taskbar flash
                                          tray popup
```

---

## 8. Quick call-graph index

| Action | Entry → Path |
|---|---|
| Start app | `main()` → `LoginDialog.exec` → `NetworkClient.run` → `ChatController.bootstrap` → `ChatWindow.show` |
| Send text | `_send_clicked` → `ctrl.send_current` → `net.send_dm/_group` |
| Receive DM | `NetworkClient._dispatch` → `dm_received.emit` → `ctrl.on_dm` → `ctrl.entry_appended.emit` → `win._on_entry_appended` |
| Stage image | `_on_image_pasted` / `_pick_image` → `ctrl.stage_qimage` / `stage_image_file` → `_set_pending` → `pending_changed.emit` → `win._on_pending_changed` |
| Switch conv | `_on_nav_clicked` → `ctrl.set_context` → `history_changed.emit` → `win._on_history_changed` |
| Save chat | `_save_chat_dialog` → `QFileDialog` → `ctrl.export_chat` |
| Disconnect | server closes socket → `NetworkClient.run` exits → `disconnected.emit` → `ctrl.disconnected.emit` → `win._on_disconnected` |
| Quit | `closeEvent` → `tray.hide` → `ctrl.shutdown` → `net.stop` + `wait` |

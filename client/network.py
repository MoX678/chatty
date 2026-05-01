"""Network worker (plain TCP, newline-delimited JSON).

Owns a synchronous `socket.socket` connection inside a QThread so the
PyQt6 UI thread never blocks on I/O. All inbound JSON is parsed here and
surfaced as `pyqtSignal`s; outbound sends are guarded by a mutex.

Auto-save: every inbound chat message is appended to a per-context
`.txt` file under `client/logs/`. Outbound sends are persisted via the
server's echo so each message is written exactly once.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import socket
import threading
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def user_log_dir(username: str) -> str:
    """Per-user log directory (`client/logs/<username>/`)."""
    p = os.path.join(LOG_DIR, _safe(username))
    os.makedirs(p, exist_ok=True)
    os.makedirs(os.path.join(p, "images"), exist_ok=True)
    return p


_MIME_EXT = {
    "image/png":  "png",
    "image/jpeg": "jpg",
    "image/jpg":  "jpg",
    "image/gif":  "gif",
    "image/webp": "webp",
    "image/bmp":  "bmp",
}


def ext_for_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), "bin")


def mime_for_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    rev = {v: k for k, v in _MIME_EXT.items() if k != "image/jpg"}
    rev["jpg"] = "image/jpeg"
    return rev.get(ext, "application/octet-stream")


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("_", "-")).lower() or "unknown"


def _ts_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_text_log(log_dir: str, context: str, line: str) -> None:
    """Append a single human-readable line to <log_dir>/<context>.txt."""
    try:
        with open(os.path.join(log_dir, f"{_safe(context)}.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{_ts_now()}] {line}\n")
    except OSError:
        pass


def append_jsonl(log_dir: str, context: str, entry: dict) -> None:
    """Append a JSON entry to <log_dir>/<context>.jsonl (canonical)."""
    try:
        with open(os.path.join(log_dir, f"{_safe(context)}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_history(log_dir: str, context: str) -> list:
    """Replay <log_dir>/<context>.jsonl into a list of entries.
    Image paths in the JSONL are stored relative to log_dir; absolutized here."""
    path = os.path.join(log_dir, f"{_safe(context)}.jsonl")
    if not os.path.isfile(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if e.get("kind") == "image":
                    rel = e.get("path", "")
                    if rel and not os.path.isabs(rel):
                        e["path"] = os.path.join(log_dir, rel)
                out.append(e)
    except OSError:
        pass
    return out


def list_dm_partners(log_dir: str) -> list:
    """Return all usernames that have a `dm_<name>.jsonl` log present."""
    if not os.path.isdir(log_dir):
        return []
    names = []
    for entry in os.listdir(log_dir):
        if entry.startswith("dm_") and entry.endswith(".jsonl"):
            names.append(entry[len("dm_"): -len(".jsonl")])
    return sorted(names)


# Back-compat shim used by older call sites (kept temporarily; new code
# should use append_text_log / append_jsonl directly).
def append_log(context: str, line: str) -> None:
    append_text_log(LOG_DIR, context, line)


class NetworkClient(QThread):
    # auth + lifecycle
    auth_success      = pyqtSignal()
    auth_failed       = pyqtSignal(str)            # reason
    connection_error  = pyqtSignal(str)
    disconnected      = pyqtSignal()

    # inbound payloads
    user_list_changed = pyqtSignal(list)                 # [usernames]
    system_event      = pyqtSignal(str, str)             # (type, user)
    # message signals carry an attachment dict (empty {} if none).
    # attachment shape on success: {"mime","name","path"}
    dm_received       = pyqtSignal(str, str, str, dict)  # (sender, target, message, attachment)
    group_received    = pyqtSignal(str, str, str, dict)  # (sender, target, message, attachment)

    def __init__(self, host: str, port: int, username: str, password: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sock: Optional[socket.socket] = None
        self.rfile = None
        self._send_lock = threading.Lock()
        self._stop = False
        # Per-user log dir: every user logged in on this machine gets a
        # private folder so DM histories don't bleed between accounts.
        self.log_dir = user_log_dir(username)

    # ---- thread entry point ------------------------------------------------

    def run(self) -> None:
        # connect
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError as e:
            self.connection_error.emit(f"Could not connect to {self.host}:{self.port}: {e}")
            return

        try:
            self.sock.settimeout(None)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        try:
            self.rfile = self.sock.makefile("rb")
        except OSError as e:
            self.connection_error.emit(f"socket setup failed: {e}")
            self._close()
            return

        # authenticate
        try:
            self._raw_send(json.dumps({
                "action":   "login",
                "username": self.username,
                "password": self.password,
            }))
            line = self.rfile.readline()
        except OSError as e:
            self.connection_error.emit(f"Login I/O error: {e}")
            self._close()
            return

        if not line:
            self.connection_error.emit("server closed connection during login")
            self._close()
            return

        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.auth_failed.emit("malformed_response")
            self._close()
            return

        if msg.get("action") != "auth_status" or msg.get("status") != "success":
            self.auth_failed.emit(msg.get("reason", "invalid_credentials"))
            self._close()
            return

        self.auth_success.emit()

        # main recv loop
        while not self._stop:
            try:
                line = self.rfile.readline()
            except OSError:
                break
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            self._dispatch(msg)

        self._close()
        self.disconnected.emit()

    # ---- inbound dispatch --------------------------------------------------

    def _dispatch(self, msg: dict) -> None:
        action = msg.get("action")
        if action == "user_list":
            self.user_list_changed.emit(list(msg.get("users", [])))
        elif action == "system_event":
            self.system_event.emit(msg.get("type", ""), msg.get("user", ""))
        elif action == "dm":
            sender = msg.get("sender", "")
            target = msg.get("target", "")
            text   = msg.get("message", "")
            other  = target if sender == self.username else sender
            ctx    = f"dm_{other}"
            local_att = self._save_attachment(ctx, sender, msg.get("attachment"))
            self._log_entry(ctx, sender, text, local_att)
            self.dm_received.emit(sender, target, text, local_att or {})
        elif action == "group_msg":
            sender = msg.get("sender", "")
            target = msg.get("target", "")
            text   = msg.get("message", "")
            ctx    = f"group_{target}"
            local_att = self._save_attachment(ctx, sender, msg.get("attachment"))
            self._log_entry(ctx, sender, text, local_att)
            self.group_received.emit(sender, target, text, local_att or {})

    def _log_entry(self, ctx: str, sender: str, text: str, att: Optional[dict]) -> None:
        ts = _ts_now()
        if att is not None:
            # store path *relative* to log_dir so the JSONL stays portable
            try:
                rel = os.path.relpath(att["path"], self.log_dir)
            except ValueError:
                rel = att["path"]
            entry = {
                "ts": ts, "kind": "image", "sender": sender, "text": text,
                "name": att.get("name", ""), "mime": att.get("mime", ""),
                "path": rel.replace("\\", "/"),
            }
            tag = f"[image: {att.get('name', '')}]"
            text_line = f"{sender}: {tag}" + (f" {text}" if text else "")
        else:
            entry = {"ts": ts, "kind": "msg", "sender": sender, "text": text}
            text_line = f"{sender}: {text}"
        append_jsonl(self.log_dir, ctx, entry)
        append_text_log(self.log_dir, ctx, text_line)

    def _save_attachment(self, ctx: str, sender: str, att) -> Optional[dict]:
        """Decode a base64 image attachment to disk; return UI-side metadata."""
        if not isinstance(att, dict):
            return None
        mime = att.get("mime", "")
        if not isinstance(mime, str) or not mime.startswith("image/"):
            return None
        b64 = att.get("data", "")
        if not isinstance(b64, str) or not b64:
            return None
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not data:
            return None

        ext = ext_for_mime(mime)
        ctx_dir = os.path.join(self.log_dir, "images", _safe(ctx))
        os.makedirs(ctx_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = f"{stamp}_{_safe(sender)}.{ext}"
        path = os.path.join(ctx_dir, name)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError:
            return None

        display_name = att.get("name") if isinstance(att.get("name"), str) else name
        return {"mime": mime, "name": display_name or name, "path": path}

    # ---- outbound ----------------------------------------------------------

    def send_dm(self, target: str, message: str, attachment: Optional[dict] = None) -> None:
        # Server echoes DMs back to the sender, so logging happens in
        # `_dispatch` for both directions — no double-log.
        payload = {
            "action":  "dm",
            "sender":  self.username,
            "target":  target,
            "message": message,
        }
        if attachment is not None:
            payload["attachment"] = attachment
        self._send_json(payload)

    def send_group(self, group: str, message: str, attachment: Optional[dict] = None) -> None:
        # Server broadcasts group messages to every member including the
        # sender, so the inbound echo path handles auto-save uniformly.
        payload = {
            "action":  "group_msg",
            "sender":  self.username,
            "target":  group,
            "message": message,
        }
        if attachment is not None:
            payload["attachment"] = attachment
        self._send_json(payload)

    def stop(self) -> None:
        self._stop = True
        # shutting down the socket unblocks readline() on the worker thread
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # ---- low level ---------------------------------------------------------

    def _send_json(self, payload: dict) -> None:
        try:
            self._raw_send(json.dumps(payload))
        except OSError as e:
            self.connection_error.emit(f"send error: {e}")

    def _raw_send(self, data: str) -> None:
        if self.sock is None:
            return
        blob = (data + "\n").encode("utf-8")
        with self._send_lock:
            self.sock.sendall(blob)

    def _close(self) -> None:
        if self.rfile is not None:
            try:
                self.rfile.close()
            except OSError:
                pass
            self.rfile = None
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


__all__ = [
    "NetworkClient",
    "LOG_DIR", "user_log_dir",
    "append_log", "append_text_log", "append_jsonl",
    "load_history", "list_dm_partners",
    "ext_for_mime", "mime_for_path",
]

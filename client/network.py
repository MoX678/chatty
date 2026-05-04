"""Network client — plain TCP, threading-based.

No Qt dependency. Uses `threading.Thread` for the recv loop and
callback functions for event dispatch.

Auto-save: every inbound chat message is appended to per-context
`.txt` and `.jsonl` files under `client/logs/`.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import socket
import threading
from datetime import datetime
from typing import Callable, Optional


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
_EXT_MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif",
             "webp": "image/webp", "bmp": "image/bmp"}


def ext_for_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), "bin")


def mime_for_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXT_MIME.get(ext, "application/octet-stream")


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("_", "-")).lower() or "unknown"


def _ts_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_log(log_dir: str, context: str, entry: dict, text_line: str) -> None:
    """Persist one message to both logs for `context`."""
    base = os.path.join(log_dir, _safe(context))
    writes = (
        (base + ".jsonl", json.dumps(entry, ensure_ascii=False) + "\n"),
        (base + ".txt",   f"[{_ts_now()}] {text_line}\n"),
    )
    for path, line in writes:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def load_history(log_dir: str, context: str) -> list:
    """Replay <log_dir>/<context>.jsonl into a list of entries."""
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


class NetworkClient:
    """TCP chat client using plain threading.

    Set callback attributes (on_user_list, on_dm, etc.) before calling
    connect_and_auth() + start_recv_loop().
    """

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sock: Optional[socket.socket] = None
        self.rfile = None
        self._send_lock = threading.Lock()
        self._stop = False
        self.log_dir = user_log_dir(username)

        # Callbacks — assign before starting the recv loop.
        self.on_user_list:    Optional[Callable[[list], None]]             = None
        self.on_system_event: Optional[Callable[[str, str], None]]         = None
        self.on_dm:           Optional[Callable[[str, str, str, dict], None]] = None
        self.on_group:        Optional[Callable[[str, str, str, dict], None]] = None
        self.on_disconnected: Optional[Callable[[], None]]                 = None

    # ---- connect + auth (blocking) ----------------------------------------

    def connect_and_auth(self) -> tuple:
        """Blocking connect + login handshake. Returns (ok, reason)."""
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
            self.sock.settimeout(None)
            try:
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            self.rfile = self.sock.makefile("rb")
            self._send_json({
                "action":   "login",
                "username": self.username,
                "password": self.password,
            })
            line = self.rfile.readline()
        except OSError as e:
            return False, f"Could not connect to {self.host}:{self.port}: {e}"

        if not line:
            return False, "Server closed connection during login"
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, "Malformed response"
        if msg.get("action") != "auth_status" or msg.get("status") != "success":
            return False, msg.get("reason", "invalid_credentials")
        return True, ""

    # ---- recv loop (background thread) ------------------------------------

    def start_recv_loop(self) -> None:
        """Spin up a daemon thread that reads inbound messages."""
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self) -> None:
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
        if self.on_disconnected:
            self.on_disconnected()

    # ---- inbound dispatch --------------------------------------------------

    def _dispatch(self, msg: dict) -> None:
        action = msg.get("action")
        if action == "user_list":
            if self.on_user_list:
                self.on_user_list(list(msg.get("users", [])))
        elif action == "system_event":
            if self.on_system_event:
                self.on_system_event(msg.get("type", ""), msg.get("user", ""))
        elif action in ("dm", "group_msg"):
            sender = msg.get("sender", "")
            target = msg.get("target", "")
            text   = msg.get("message", "")
            if action == "dm":
                other = target if sender == self.username else sender
                ctx = f"dm_{other}"
            else:
                ctx = f"group_{target}"
            local_att = self._save_attachment(ctx, sender, msg.get("attachment"))
            self._log_entry(ctx, sender, text, local_att)
            if action == "dm" and self.on_dm:
                self.on_dm(sender, target, text, local_att or {})
            elif action == "group_msg" and self.on_group:
                self.on_group(sender, target, text, local_att or {})

    def _log_entry(self, ctx: str, sender: str, text: str, att: Optional[dict]) -> None:
        ts = _ts_now()
        if att is not None:
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
        append_log(self.log_dir, ctx, entry, text_line)

    def _save_attachment(self, ctx: str, sender: str, att) -> Optional[dict]:
        """Decode a base64 image attachment to disk; return metadata."""
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

    def send(self, kind: str, target: str, message: str,
             attachment: Optional[dict] = None) -> None:
        """Send a chat message. `kind` is "dm" or "group"."""
        payload = {
            "action":  "dm" if kind == "dm" else "group_msg",
            "sender":  self.username,
            "target":  target,
            "message": message,
        }
        if attachment is not None:
            payload["attachment"] = attachment
        self._send_json(payload)

    def stop(self) -> None:
        self._stop = True
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # ---- low level ---------------------------------------------------------

    def _send_json(self, payload: dict) -> None:
        if self.sock is None:
            return
        blob = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with self._send_lock:
                self.sock.sendall(blob)
        except OSError:
            pass

    def _close(self) -> None:
        for attr in ("rfile", "sock"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except OSError:
                    pass
                setattr(self, attr, None)


__all__ = [
    "NetworkClient",
    "LOG_DIR", "user_log_dir",
    "append_log",
    "load_history", "list_dm_partners",
    "ext_for_mime", "mime_for_path",
]

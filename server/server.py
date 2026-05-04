"""Chatty server — headless TCP chat server.

Listens on a TCP socket, authenticates clients, routes newline-delimited
JSON messages between them.  No GUI — runs as a CLI process with logging
to stdout.

Wire protocol — see `ARCHITECTURE.md` § 4.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import threading
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050
USERS_FILE   = os.path.join(os.path.dirname(__file__), "users.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chatty-server")


def load_users() -> Dict[str, str]:
    """Read `users.json` (`{username: password}`). Empty dict on error."""
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Core server
# ---------------------------------------------------------------------------

class ChatServer:
    """Listens on a TCP socket, authenticates clients, routes messages."""

    def __init__(self) -> None:
        self.sock: Optional[socket.socket] = None
        # username -> {"sock": socket, "lock": Lock}
        self.clients: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.running = False
        self.users = load_users()

    # ---- lifecycle --------------------------------------------------------

    def start(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
        if self.running:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            s.listen()
        except OSError as e:
            log.error("bind failed: %s", e)
            return False

        self.sock = s
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        log.info("listening on %s:%s", host, port)
        return True

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        try:
            if self.sock is not None:
                self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass
        self.sock = None

        with self._lock:
            snapshot = list(self.clients.items())
            self.clients.clear()
        for _, info in snapshot:
            self._safe_close(info["sock"])

        log.info("stopped")

    # ---- accept loop ------------------------------------------------------

    def _accept_loop(self) -> None:
        assert self.sock is not None
        while self.running:
            try:
                csock, addr = self.sock.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(csock, addr), daemon=True,
            ).start()

    # ---- send helpers (thread-safe) --------------------------------------

    @staticmethod
    def _safe_close(sk: socket.socket) -> None:
        try:
            sk.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sk.close()
        except OSError:
            pass

    @staticmethod
    def _send_to(sock: socket.socket, lock: threading.Lock, payload: dict) -> bool:
        data = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with lock:
                sock.sendall(data)
            return True
        except OSError:
            return False

    def _broadcast(self, payload: dict, exclude: Optional[str] = None) -> None:
        with self._lock:
            snapshot = list(self.clients.items())
        for uname, info in snapshot:
            if uname == exclude:
                continue
            self._send_to(info["sock"], info["lock"], payload)

    def _push_user_list(self) -> None:
        with self._lock:
            users = sorted(self.clients.keys())
        self._broadcast({"action": "user_list", "users": users})
        log.info("online: %s", ", ".join(users) if users else "(none)")

    # ---- per-connection thread -------------------------------------------

    def _handle(self, csock: socket.socket, addr) -> None:
        send_lock = threading.Lock()
        username: Optional[str] = None
        rfile = None
        try:
            rfile = csock.makefile("rb")

            # 1. Auth: expect a single login line.
            line = rfile.readline()
            if not line:
                return
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_to(csock, send_lock,
                              {"action": "auth_status", "status": "fail",
                               "reason": "malformed"})
                return

            if msg.get("action") != "login":
                self._send_to(csock, send_lock,
                              {"action": "auth_status", "status": "fail",
                               "reason": "expected_login"})
                return

            uname = str(msg.get("username", "")).strip()
            pw    = str(msg.get("password", ""))
            if not uname or self.users.get(uname) != pw:
                self._send_to(csock, send_lock,
                              {"action": "auth_status", "status": "fail",
                               "reason": "invalid_credentials"})
                return

            # 2. Reject duplicate sessions (same username already online).
            with self._lock:
                if uname in self.clients:
                    self._send_to(csock, send_lock,
                                  {"action": "auth_status", "status": "fail",
                                   "reason": "already_signed_in"})
                    return
                self.clients[uname] = {"sock": csock, "lock": send_lock}
            username = uname

            # 3. Welcome.
            self._send_to(csock, send_lock,
                          {"action": "auth_status", "status": "success"})
            log.info("%s connected from %s", uname, addr[0])
            self._broadcast(
                {"action": "system_event", "type": "join", "user": uname},
            )
            self._push_user_list()

            # 4. Recv loop.
            while self.running:
                line = rfile.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                self._route(uname, msg)

        except OSError:
            pass
        finally:
            # 5. Unregister on disconnect.
            if username is not None:
                with self._lock:
                    self.clients.pop(username, None)
                log.info("%s disconnected", username)
                self._broadcast(
                    {"action": "system_event", "type": "leave", "user": username},
                )
                self._push_user_list()
            try:
                if rfile is not None:
                    rfile.close()
            except OSError:
                pass
            self._safe_close(csock)

    # ---- routing ----------------------------------------------------------

    def _route(self, sender: str, msg: dict) -> None:
        action = msg.get("action")

        if action == "dm":
            target = str(msg.get("target", "")).strip()
            if not target:
                return
            out = {
                "action":  "dm",
                "sender":  sender,
                "target":  target,
                "message": str(msg.get("message", "")),
            }
            att = self._sanitize_attachment(msg.get("attachment"))
            if att is not None:
                out["attachment"] = att

            with self._lock:
                tgt = self.clients.get(target)
                src = self.clients.get(sender)
            if tgt is not None:
                self._send_to(tgt["sock"], tgt["lock"], out)
            if src is not None:
                self._send_to(src["sock"], src["lock"], out)
            log.info("dm %s -> %s", sender, target)

        elif action == "group_msg":
            target = str(msg.get("target", "")).strip()
            if not target:
                return
            out = {
                "action":  "group_msg",
                "sender":  sender,
                "target":  target,
                "message": str(msg.get("message", "")),
            }
            att = self._sanitize_attachment(msg.get("attachment"))
            if att is not None:
                out["attachment"] = att
            self._broadcast(out)
            log.info("group #%s <- %s", target, sender)

        else:
            log.warning("unknown action from %s: %r", sender, action)

    @staticmethod
    def _sanitize_attachment(att) -> Optional[dict]:
        """Whitelist allowed keys so we don't forward arbitrary fields."""
        if not isinstance(att, dict):
            return None
        mime = att.get("mime")
        data = att.get("data")
        if not isinstance(mime, str) or not isinstance(data, str):
            return None
        if not mime.startswith("image/") or not data:
            return None
        out = {"mime": mime, "data": data}
        name = att.get("name")
        if isinstance(name, str) and name:
            out["name"] = name
        return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)

    host = os.environ.get("CHATTY_HOST", DEFAULT_HOST)
    port = int(os.environ.get("CHATTY_PORT", str(DEFAULT_PORT)))

    server = ChatServer()
    if not server.start(host, port):
        return 1

    stop_event = threading.Event()

    def _sig_handler(sig, frame):
        log.info("shutting down...")
        server.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    log.info("press Ctrl+C to stop")
    stop_event.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

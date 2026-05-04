"""Chatty — terminal chat client.

No GUI dependencies. Communicates over plain TCP with the Chatty server.

Commands:
  /dm <user> <message>   Send a direct message
  /switch <context>      Switch context (e.g. group_general, dm_ahmed)
  /users                 Show online users
  /dms                   Show DM partners
  /history               Show recent history for current context
  /attach <path>         Send an image file
  /help                  Show commands
  /quit                  Exit
"""
from __future__ import annotations

import base64
import getpass
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from network import NetworkClient, load_history, list_dm_partners, mime_for_path
from utils import GROUPS, DEFAULT_HOST, DEFAULT_PORT, fmt_ts


class TerminalChat:
    """Interactive terminal chat session."""

    def __init__(self, net: NetworkClient, username: str):
        self.net = net
        self.username = username
        self.current_context = f"group_{GROUPS[0]}"
        self.online_users: list = []
        self.dm_partners: set = set()
        self.unread: dict = {}
        self._print_lock = threading.Lock()

        net.on_user_list = self._on_user_list
        net.on_system_event = self._on_system_event
        net.on_dm = self._on_dm
        net.on_group = self._on_group
        net.on_disconnected = self._on_disconnected

    # ---- callbacks from network thread ------------------------------------

    def _print(self, msg: str) -> None:
        with self._print_lock:
            print(f"\r{msg}")
            print(f"[{self.current_context}] > ", end="", flush=True)

    def _on_user_list(self, users: list) -> None:
        self.online_users = users
        self._print(f"  [online: {', '.join(users)}]")

    def _on_system_event(self, event: str, user: str) -> None:
        verb = "joined" if event == "join" else "left"
        self._print(f"  --- {user} {verb} ---")

    def _on_dm(self, sender: str, target: str, message: str, att: dict) -> None:
        other = sender if sender != self.username else target
        ctx = f"dm_{other}"
        self.dm_partners.add(other)
        tag = f" [image: {att.get('name', '')}]" if att.get("path") else ""
        if ctx == self.current_context:
            self._print(f"  {sender}: {message}{tag}")
        else:
            self.unread[other] = self.unread.get(other, 0) + 1
            self._print(f"  [DM from {sender}]: {message}{tag}")

    def _on_group(self, sender: str, target: str, message: str, att: dict) -> None:
        ctx = f"group_{target}"
        tag = f" [image: {att.get('name', '')}]" if att.get("path") else ""
        if ctx == self.current_context:
            self._print(f"  {sender}: {message}{tag}")
        else:
            self._print(f"  [#{target} {sender}]: {message}{tag}")

    def _on_disconnected(self) -> None:
        self._print("  *** Disconnected from server ***")

    # ---- history / context ------------------------------------------------

    def _load_dm_partners(self) -> None:
        partners = list_dm_partners(self.net.log_dir)
        self.dm_partners = set(partners)

    def show_history(self, count: int = 20) -> None:
        entries = load_history(self.net.log_dir, self.current_context)
        if not entries:
            print("  (no history)")
            return
        for e in entries[-count:]:
            ts = fmt_ts(e.get("ts", ""))
            if e["kind"] == "system":
                print(f"  --- {e['text']} [{ts}] ---")
            elif e["kind"] == "image":
                print(f"  [{ts}] {e['sender']}: [image: {e.get('name', '')}] {e.get('text', '')}")
            else:
                print(f"  [{ts}] {e['sender']}: {e['text']}")

    def switch_context(self, ctx: str) -> None:
        self.current_context = ctx
        if ctx.startswith("dm_"):
            other = ctx[3:]
            self.unread.pop(other, None)
        print(f"  Switched to {ctx}")
        self.show_history()

    # ---- main loop --------------------------------------------------------

    def run(self) -> None:
        self._load_dm_partners()
        print(f"\nLogged in as {self.username}. Context: {self.current_context}")
        print("Type /help for commands.\n")
        self.show_history()

        while True:
            try:
                line = input(f"[{self.current_context}] > ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            line = line.strip()
            if not line:
                continue

            if line == "/quit":
                break
            elif line == "/help":
                print("  /dm <user> <msg>  - Send DM")
                print("  /switch <ctx>     - Switch context (group_general, dm_ahmed)")
                print("  /users            - Show online users")
                print("  /dms              - Show DM partners")
                print("  /history          - Show recent history")
                print("  /attach <path>    - Send an image file")
                print("  /quit             - Exit")
            elif line == "/users":
                print(f"  Online: {', '.join(self.online_users) or '(none)'}")
            elif line == "/dms":
                if not self.dm_partners:
                    print("  (no DM partners yet)")
                for p in sorted(self.dm_partners):
                    unread = self.unread.get(p, 0)
                    status = "online" if p in self.online_users else "offline"
                    badge = f" ({unread} new)" if unread else ""
                    print(f"  {p} [{status}]{badge}")
            elif line == "/history":
                self.show_history()
            elif line.startswith("/switch "):
                ctx = line[8:].strip()
                if not ctx:
                    print("  Usage: /switch <context>")
                else:
                    self.switch_context(ctx)
            elif line.startswith("/dm "):
                parts = line[4:].strip().split(None, 1)
                if len(parts) < 2:
                    print("  Usage: /dm <user> <message>")
                else:
                    target, msg = parts
                    self.net.send("dm", target, msg)
                    self.dm_partners.add(target)
                    if self.current_context != f"dm_{target}":
                        self.switch_context(f"dm_{target}")
            elif line.startswith("/attach "):
                path = line[8:].strip()
                if not os.path.isfile(path):
                    print(f"  File not found: {path}")
                else:
                    try:
                        with open(path, "rb") as f:
                            data = f.read()
                        mime = mime_for_path(path)
                        b64 = base64.b64encode(data).decode("ascii")
                        kind, _, target = self.current_context.partition("_")
                        self.net.send(kind, target, "", attachment={
                            "mime": mime, "name": os.path.basename(path), "data": b64,
                        })
                        print(f"  Sent image: {os.path.basename(path)}")
                    except Exception as e:
                        print(f"  Error: {e}")
            else:
                kind, _, target = self.current_context.partition("_")
                self.net.send(kind, target, line)


def main() -> int:
    print("=== Chatty ===\n")

    username = input("Username: ").strip()
    if not username:
        print("Username required.")
        return 1
    password = getpass.getpass("Password: ")

    host = input(f"Host [{DEFAULT_HOST}]: ").strip() or DEFAULT_HOST
    port_s = input(f"Port [{DEFAULT_PORT}]: ").strip()
    port = int(port_s) if port_s else DEFAULT_PORT

    net = NetworkClient(host, port, username, password)
    ok, reason = net.connect_and_auth()
    if not ok:
        print(f"Login failed: {reason}")
        return 1

    chat = TerminalChat(net, username)
    net.start_recv_loop()
    try:
        chat.run()
    finally:
        net.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

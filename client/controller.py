"""Application logic / state for the chat client.

`ChatController` owns:
  - the in-memory chat history dictionary
  - the online-user list, DM partner set and unread counters
  - the staged "pending attachment" before send
  - the connection to `NetworkClient` (signal wiring + send calls)

It does NOT touch any QWidget. Instead it inherits from `QObject` and
emits signals describing state changes; `ChatWindow` slots those
signals to update its widgets, and calls controller methods in
response to user actions.

This separation makes the logic unit-testable without a Qt event loop
hosting widgets, and keeps `chat_window.py` focused on presentation.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QBuffer, QIODevice, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QImage

from network import (
    NetworkClient, ext_for_mime, mime_for_path,
    load_history, list_dm_partners,
)
from utils import (
    GROUPS, IMAGE_EXTS, MAX_IMAGE_BYTES, MAX_IMAGE_EDGE, now_ts,
)


class ChatController(QObject):
    """State + network glue for the chat client.

    Lifecycle:
        ctrl = ChatController(net, "ahmed")
        # ... ChatWindow connects to ctrl's signals ...
        ctrl.bootstrap()      # loads disk history, wires network slots
        ...
        ctrl.shutdown()       # stops the network thread cleanly
    """

    # --- signals to the UI -------------------------------------------------

    # Whole-context rerender required (history loaded, switched contexts).
    history_changed   = pyqtSignal(str)            # ctx
    # A single new entry was appended to ctx; window may render incrementally.
    entry_appended    = pyqtSignal(str, dict)      # ctx, entry
    # Sidebar DM rows need to rebuild (online list / unread counts changed).
    dm_list_changed   = pyqtSignal()
    # Header subtitle (e.g. "Direct message · online") should update.
    subtitle_changed  = pyqtSignal(str)
    # Inbound message worth flashing the taskbar / native tray for.
    notify_requested  = pyqtSignal(str, str, str)  # sender, body, ctx
    # Pending attachment changed; payload is dict|None with keys:
    #   mime, name, data (bytes), preview (QImage)
    pending_changed   = pyqtSignal(object)
    # User-facing dialogs (window converts to QMessageBox).
    warning_requested = pyqtSignal(str, str)       # title, body
    info_requested    = pyqtSignal(str, str)
    # Network finished cleanly from the server side.
    disconnected      = pyqtSignal()

    # ----------------------------------------------------------------------

    def __init__(self, net: NetworkClient, username: str, parent=None):
        super().__init__(parent)
        self.net = net
        self.username = username

        # context_key -> list[{"kind","sender","text","ts", ...}]
        self.histories: Dict[str, List[dict]] = {f"group_{g}": [] for g in GROUPS}
        self.online_users: List[str] = []
        self.current_context: str = f"group_{GROUPS[0]}"
        self.dm_partners: set = set()
        # raw username -> int (count of unread inbound DMs)
        self.unread_dms: Dict[str, int] = {}
        # one-shot staged attachment awaiting send
        self.pending_attachment: Optional[dict] = None

    # ----------------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Load disk history then start listening to network signals.

        Call this after the window has connected to the controller's
        signals so the initial replays are observed."""
        self._load_persisted_history()

        self.net.user_list_changed.connect(self.on_user_list)
        self.net.system_event.connect(self.on_system_event)
        self.net.dm_received.connect(self.on_dm)
        self.net.group_received.connect(self.on_group)
        self.net.connection_error.connect(self._on_net_error)
        self.net.disconnected.connect(self.disconnected)

        self.dm_list_changed.emit()
        self.history_changed.emit(self.current_context)

    def shutdown(self) -> None:
        """Stop the network thread; safe to call multiple times."""
        try:
            self.net.stop()
            self.net.wait(2000)
        except Exception:
            pass

    def _load_persisted_history(self) -> None:
        log_dir = self.net.log_dir
        for g in GROUPS:
            ctx = f"group_{g}"
            self.histories[ctx] = load_history(log_dir, ctx)
        partners = list_dm_partners(log_dir)
        self.dm_partners = set(partners)
        for p in partners:
            self.histories[f"dm_{p}"] = load_history(log_dir, f"dm_{p}")

    # ----------------------------------------------------------------------
    # Context navigation
    # ----------------------------------------------------------------------

    def set_context(self, ctx: str) -> None:
        """Switch the active conversation. Clears unread counter for DMs."""
        if not ctx:
            return
        self.current_context = ctx
        if ctx.startswith("dm_"):
            other = ctx.split("_", 1)[1]
            if self.unread_dms.pop(other, 0) > 0:
                self.dm_list_changed.emit()
        if ctx not in self.histories:
            self.histories[ctx] = []
        self.history_changed.emit(ctx)

    def subtitle_for(self, ctx: str) -> str:
        """Compute the header subtitle for a given context."""
        if ctx.startswith("group_"):
            return "Public group · all members"
        other = ctx.split("_", 1)[1] if "_" in ctx else ctx
        online = other in self.online_users
        return "Direct message · " + ("online" if online else "offline")

    # ----------------------------------------------------------------------
    # Sidebar data
    # ----------------------------------------------------------------------

    def dm_rows(self) -> List[Tuple[str, bool, int]]:
        """Return ordered (name, is_online, unread) tuples for the sidebar.

        Sort priority: unread > online > offline (alphabetical inside each)."""
        online = {u for u in self.online_users if u != self.username}
        partners = (self.dm_partners | online) - {self.username}

        def bucket(u: str) -> int:
            if self.unread_dms.get(u, 0) > 0:
                return 0
            if u in online:
                return 1
            return 2

        ordered = sorted(partners, key=lambda u: (bucket(u), u.lower()))
        return [(u, u in online, self.unread_dms.get(u, 0)) for u in ordered]

    # ----------------------------------------------------------------------
    # Send
    # ----------------------------------------------------------------------

    def send_current(self, text: str) -> bool:
        """Send `text` (and any pending attachment) to the current context.

        Returns True if anything was sent."""
        text = (text or "").strip()
        att_payload: Optional[dict] = None
        if self.pending_attachment is not None:
            p = self.pending_attachment
            att_payload = {
                "mime": p["mime"],
                "name": p["name"],
                "data": base64.b64encode(p["data"]).decode("ascii"),
            }
        if not text and att_payload is None:
            return False

        ctx = self.current_context
        if ctx.startswith("group_"):
            self.net.send_group(ctx.split("_", 1)[1], text, attachment=att_payload)
        else:
            target = ctx.split("_", 1)[1]
            self.net.send_dm(target, text, attachment=att_payload)

        if att_payload is not None:
            self.clear_pending()
        return True

    # ----------------------------------------------------------------------
    # Image staging (paste / drop / pick)
    # ----------------------------------------------------------------------

    def stage_qimage(self, image: QImage, mime: str, name: str) -> bool:
        """Encode + downscale a QImage and stash it as the pending attachment."""
        if image.isNull():
            return False
        if image.width() > MAX_IMAGE_EDGE or image.height() > MAX_IMAGE_EDGE:
            image = image.scaled(
                MAX_IMAGE_EDGE, MAX_IMAGE_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        fmt = "JPEG" if mime == "image/jpeg" else "PNG"
        out_mime = "image/jpeg" if fmt == "JPEG" else "image/png"
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = image.save(buf, fmt, 88 if fmt == "JPEG" else -1)
        buf.close()
        if not ok:
            self.warning_requested.emit("Encode failed", f"Could not encode image as {fmt}")
            return False
        data = bytes(buf.data())
        if len(data) > MAX_IMAGE_BYTES and fmt == "PNG":
            return self.stage_qimage(image, "image/jpeg", name)
        if len(data) > MAX_IMAGE_BYTES:
            self.warning_requested.emit(
                "Image too large",
                f"Encoded payload is {len(data)//1024} KB; max is "
                f"{MAX_IMAGE_BYTES // 1024} KB.")
            return False
        return self._set_pending(out_mime,
                                 name or f"image.{ext_for_mime(out_mime)}",
                                 data, image)

    def stage_image_file(self, path: str) -> bool:
        """Read a file from disk and stage it as the pending attachment."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            self.warning_requested.emit("Read failed", str(e))
            return False
        if not data:
            return False
        if os.path.splitext(path)[1].lower() not in IMAGE_EXTS:
            self.warning_requested.emit(
                "Unsupported file",
                f"Only image files can be attached.\n{os.path.basename(path)}",
            )
            return False
        mime = mime_for_path(path)
        name = os.path.basename(path)

        if len(data) > MAX_IMAGE_BYTES:
            img = QImage(path)
            if img.isNull():
                self.warning_requested.emit(
                    "Image too large",
                    f"{name} is over {MAX_IMAGE_BYTES // (1024 * 1024)} MB "
                    "and could not be re-encoded.")
                return False
            return self.stage_qimage(img, "image/jpeg", name)

        return self._set_pending(mime, name, data, QImage(path))

    def _set_pending(self, mime: str, name: str, data: bytes,
                     preview: QImage) -> bool:
        self.pending_attachment = {
            "mime": mime, "name": name, "data": data, "preview": preview,
        }
        self.pending_changed.emit(self.pending_attachment)
        return True

    def clear_pending(self) -> None:
        if self.pending_attachment is None:
            return
        self.pending_attachment = None
        self.pending_changed.emit(None)

    # ----------------------------------------------------------------------
    # Network signal handlers
    # ----------------------------------------------------------------------

    def on_user_list(self, users: list) -> None:
        self.online_users = list(users)
        self.dm_list_changed.emit()
        if self.current_context.startswith("dm_"):
            self.subtitle_changed.emit(self.subtitle_for(self.current_context))

    def on_system_event(self, type_: str, user: str) -> None:
        verb = "joined the workspace" if type_ == "join" else "left the workspace"
        text = f"{user} {verb}"
        ctx = "group_general"
        entry = {
            "kind": "system", "text": text, "ts": now_ts(), "event": type_,
        }
        self.histories.setdefault(ctx, []).append(entry)
        self.entry_appended.emit(ctx, entry)

    def on_dm(self, sender: str, target: str, message: str, attachment: dict) -> None:
        other = target if sender == self.username else sender
        ctx = f"dm_{other}"
        entry = self._make_entry(sender, message, attachment)
        self.histories.setdefault(ctx, []).append(entry)

        new_partner = other not in self.dm_partners
        self.dm_partners.add(other)

        # Emit append regardless; the window decides if it's the active ctx.
        self.entry_appended.emit(ctx, entry)

        # Off-screen → bump unread.
        if self.current_context != ctx and sender != self.username:
            self.unread_dms[other] = self.unread_dms.get(other, 0) + 1

        if sender != self.username:
            self.notify_requested.emit(sender, message or "[image]", ctx)

        if new_partner or sender != self.username:
            self.dm_list_changed.emit()

    def on_group(self, sender: str, target: str, message: str, attachment: dict) -> None:
        ctx = f"group_{target}"
        entry = self._make_entry(sender, message, attachment)
        self.histories.setdefault(ctx, []).append(entry)
        self.entry_appended.emit(ctx, entry)
        if self.current_context != ctx and sender != self.username:
            self.notify_requested.emit(
                f"#{target}  ·  {sender}", message or "[image]", ctx,
            )

    def _on_net_error(self, msg: str) -> None:
        self.warning_requested.emit("Network error", msg)

    @staticmethod
    def _make_entry(sender: str, message: str, attachment: dict) -> dict:
        if attachment:
            return {
                "kind":   "image",
                "sender": sender,
                "text":   message,
                "ts":     now_ts(),
                "path":   attachment.get("path", ""),
                "name":   attachment.get("name", ""),
                "mime":   attachment.get("mime", ""),
            }
        return {"kind": "msg", "sender": sender, "text": message, "ts": now_ts()}

    # ----------------------------------------------------------------------
    # Export
    # ----------------------------------------------------------------------

    def export_chat(self, ctx: str, path: str, title: str) -> None:
        """Write a plain-text export of `ctx` to `path`. Emits warning on error."""
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    f"# {title} — exported "
                    f"{datetime.now():%Y-%m-%d %H:%M}\n\n"
                )
                for e in self.histories.get(ctx, []):
                    if e["kind"] == "system":
                        f.write(f"-- {e['text']} --\n")
                    elif e["kind"] == "image":
                        cap = f"  {e['text']}" if e.get("text") else ""
                        f.write(f"[{e['ts']}] {e['sender']}: "
                                f"[image: {e.get('name','')}]{cap}\n")
                    else:
                        f.write(f"[{e['ts']}] {e['sender']}: {e['text']}\n")
        except OSError as ex:
            self.warning_requested.emit("Save failed", str(ex))


__all__ = ["ChatController"]

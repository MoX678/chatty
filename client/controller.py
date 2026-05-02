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
        partners = list_dm_partners(log_dir)
        self.dm_partners = set(partners)
        contexts = [f"group_{g}" for g in GROUPS] + [f"dm_{p}" for p in partners]
        for ctx in contexts:
            self.histories[ctx] = load_history(log_dir, ctx)

    # ----------------------------------------------------------------------
    # Context navigation
    # ----------------------------------------------------------------------

    @staticmethod
    def _ctx_parts(ctx: str) -> Tuple[str, str]:
        """Return ('group'|'dm', target) for a context key."""
        kind, _, target = ctx.partition("_")
        return kind, target

    def set_context(self, ctx: str) -> None:
        """Switch the active conversation. Clears unread counter for DMs."""
        if not ctx:
            return
        self.current_context = ctx
        kind, other = self._ctx_parts(ctx)
        if kind == "dm" and self.unread_dms.pop(other, 0) > 0:
            self.dm_list_changed.emit()
        if ctx not in self.histories:
            self.histories[ctx] = []
        self.history_changed.emit(ctx)

    def subtitle_for(self, ctx: str) -> str:
        """Compute the header subtitle for a given context."""
        kind, other = self._ctx_parts(ctx)
        if kind == "group":
            return "Public group · all members"
        return "Direct message · " + ("online" if other in self.online_users else "offline")

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
        att = self._pending_send_payload()
        if not text and att is None:
            return False

        kind, target = self._ctx_parts(self.current_context)
        self.net.send(kind, target, text, attachment=att)

        if att is not None:
            self.clear_pending()
        return True

    def _pending_send_payload(self) -> Optional[dict]:
        """Serialize `self.pending_attachment` for the wire (base64 data)."""
        p = self.pending_attachment
        if p is None:
            return None
        return {"mime": p["mime"], "name": p["name"],
                "data": base64.b64encode(p["data"]).decode("ascii")}

    # ----------------------------------------------------------------------
    # Image staging (paste / drop / pick)
    # ----------------------------------------------------------------------

    _FMT_MIME = {"JPEG": "image/jpeg", "PNG": "image/png"}

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

        # Try the requested format; fall back to JPEG if a PNG is too big.
        formats = ["JPEG"] if mime == "image/jpeg" else ["PNG", "JPEG"]
        data = b""
        for fmt in formats:
            data = self._encode_image(image, fmt)
            if data is None:
                self.warning_requested.emit("Encode failed", f"Could not encode image as {fmt}")
                return False
            if len(data) <= MAX_IMAGE_BYTES:
                out_mime = self._FMT_MIME[fmt]
                return self._set_pending(
                    out_mime, name or f"image.{ext_for_mime(out_mime)}", data, image)

        self.warning_requested.emit(
            "Image too large",
            f"Encoded payload is {len(data)//1024} KB; max is "
            f"{MAX_IMAGE_BYTES // 1024} KB.")
        return False

    @staticmethod
    def _encode_image(image: QImage, fmt: str) -> Optional[bytes]:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = image.save(buf, fmt, 88 if fmt == "JPEG" else -1)
        buf.close()
        return bytes(buf.data()) if ok else None

    def stage_image_file(self, path: str) -> bool:
        """Read a file from disk and stage it as the pending attachment."""
        name = os.path.basename(path)
        if os.path.splitext(path)[1].lower() not in IMAGE_EXTS:
            self.warning_requested.emit(
                "Unsupported file",
                f"Only image files can be attached.\n{name}",
            )
            return False
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            self.warning_requested.emit("Read failed", str(e))
            return False
        if not data:
            return False

        # Oversized: re-encode via QImage (drops to JPEG + downscales).
        if len(data) > MAX_IMAGE_BYTES:
            img = QImage(path)
            if img.isNull():
                self.warning_requested.emit(
                    "Image too large",
                    f"{name} is over {MAX_IMAGE_BYTES // (1024 * 1024)} MB "
                    "and could not be re-encoded.")
                return False
            return self.stage_qimage(img, "image/jpeg", name)

        return self._set_pending(mime_for_path(path), name, data, QImage(path))

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
        if self._ctx_parts(self.current_context)[0] == "dm":
            self.subtitle_changed.emit(self.subtitle_for(self.current_context))

    def on_system_event(self, event: str, user: str) -> None:
        verb = "joined the workspace" if event == "join" else "left the workspace"
        entry = {"kind": "system", "text": f"{user} {verb}",
                 "ts": now_ts(), "event": event}
        self._append_entry("group_general", entry)

    def on_dm(self, sender: str, target: str, message: str, attachment: dict) -> None:
        from_other = sender != self.username
        other = sender if from_other else target
        ctx = f"dm_{other}"
        new_partner = other not in self.dm_partners
        self.dm_partners.add(other)

        self._receive(ctx, sender, message, attachment, notify_title=sender)

        # DM-only: unread counter + sidebar refresh.
        if from_other and self.current_context != ctx:
            self.unread_dms[other] = self.unread_dms.get(other, 0) + 1
        if from_other or new_partner:
            self.dm_list_changed.emit()

    def on_group(self, sender: str, target: str, message: str, attachment: dict) -> None:
        self._receive(f"group_{target}", sender, message, attachment,
                      notify_title=f"#{target}  ·  {sender}")

    def _receive(self, ctx: str, sender: str, message: str,
                 attachment: dict, notify_title: str) -> None:
        """Append an inbound message to `ctx` and fire notifications."""
        entry = self._make_entry(sender, message, attachment)
        self._append_entry(ctx, entry)
        if sender != self.username and self.current_context != ctx:
            self.notify_requested.emit(notify_title, message or "[image]", ctx)

    def _append_entry(self, ctx: str, entry: dict) -> None:
        self.histories.setdefault(ctx, []).append(entry)
        self.entry_appended.emit(ctx, entry)

    def _on_net_error(self, msg: str) -> None:
        self.warning_requested.emit("Network error", msg)

    @staticmethod
    def _make_entry(sender: str, message: str, attachment: dict) -> dict:
        entry = {"kind": "msg", "sender": sender, "text": message, "ts": now_ts()}
        if attachment:
            entry["kind"] = "image"
            for k in ("path", "name", "mime"):
                entry[k] = attachment.get(k, "")
        return entry

    # ----------------------------------------------------------------------
    # Export
    # ----------------------------------------------------------------------

    def export_chat(self, ctx: str, path: str, title: str) -> None:
        """Write a plain-text export of `ctx` to `path`. Emits warning on error."""
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title} — exported {datetime.now():%Y-%m-%d %H:%M}\n\n")
                for e in self.histories.get(ctx, []):
                    f.write(self._format_export_line(e) + "\n")
        except OSError as ex:
            self.warning_requested.emit("Save failed", str(ex))

    @staticmethod
    def _format_export_line(e: dict) -> str:
        if e["kind"] == "system":
            return f"-- {e['text']} --"
        if e["kind"] == "image":
            cap = f"  {e['text']}" if e.get("text") else ""
            return f"[{e['ts']}] {e['sender']}: [image: {e.get('name','')}]{cap}"
        return f"[{e['ts']}] {e['sender']}: {e['text']}"


__all__ = ["ChatController"]

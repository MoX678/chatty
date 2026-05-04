"""Chatty — minimal tkinter chat client.

Lightweight GUI: sidebar with groups + DM contacts, chat display,
message input, image attachment, and chat export.

Requires: python3-tk  (``sudo apt install python3-tk`` on Debian/Ubuntu)
"""
from __future__ import annotations

import base64
import os
import sys
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from network import NetworkClient, load_history, list_dm_partners, mime_for_path
from utils import GROUPS, DEFAULT_HOST, DEFAULT_PORT, fmt_ts, IMAGE_EXTS, now_ts

# ── Colours ──────────────────────────────────────────────────────────────
BG       = "#1e1e2e"
BG_SIDE  = "#181825"
BG_INPUT = "#313244"
FG       = "#cdd6f4"
FG_DIM   = "#6c7086"
ACCENT   = "#89b4fa"
GREEN    = "#a6e3a1"
YELLOW   = "#f9e2af"
RED      = "#f38ba8"
BORDER   = "#45475a"

FONT      = ("Helvetica", 11)
FONT_SM   = ("Helvetica", 9)
FONT_BOLD = ("Helvetica", 11, "bold")
FONT_HEAD = ("Helvetica", 14, "bold")
FONT_MONO = ("Courier", 10)


# ── Login Dialog ─────────────────────────────────────────────────────────

class LoginDialog(tk.Toplevel):
    """Credential entry dialog. Sets ``self.result = (net, username)``."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Chatty — Sign In")
        self.geometry("360x340")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self.result = None

        tk.Label(self, text="Chatty", font=FONT_HEAD,
                 bg=BG, fg=FG).pack(pady=(24, 2))
        tk.Label(self, text="Sign in to your workspace", font=FONT_SM,
                 bg=BG, fg=FG_DIM).pack(pady=(0, 16))

        f = tk.Frame(self, bg=BG)
        f.pack(padx=32, fill="x")

        self.user_var = tk.StringVar()
        self.pw_var   = tk.StringVar()
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))

        for label, var, show in [
            ("USERNAME",  self.user_var, ""),
            ("PASSWORD",  self.pw_var,   "\u2022"),
            ("HOST",      self.host_var, ""),
            ("PORT",      self.port_var, ""),
        ]:
            tk.Label(f, text=label, font=("Helvetica", 8), bg=BG,
                     fg=FG_DIM).pack(anchor="w", pady=(8, 2))
            e = tk.Entry(f, textvariable=var, bg=BG_INPUT, fg=FG,
                         insertbackground=FG, relief="flat", font=FONT)
            if show:
                e.configure(show=show)
            e.pack(fill="x", ipady=3)
            e.bind("<Return>", lambda _ev: self._submit())

        self.err = tk.Label(f, text="", font=FONT_SM, bg=BG, fg=RED)
        self.err.pack(anchor="w", pady=(8, 0))

        tk.Button(f, text="Sign In", bg=ACCENT, fg=BG,
                  activebackground="#b4d8fa", relief="flat",
                  font=FONT_BOLD, cursor="hand2",
                  command=self._submit).pack(fill="x", pady=(12, 0), ipady=4)

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _submit(self):
        u = self.user_var.get().strip()
        p = self.pw_var.get()
        h = self.host_var.get().strip() or DEFAULT_HOST
        try:
            port = int(self.port_var.get().strip() or DEFAULT_PORT)
        except ValueError:
            port = DEFAULT_PORT

        if not u or not p:
            self.err.config(text="Username and password required.")
            return

        self.err.config(text="Connecting\u2026")
        self.update()

        net = NetworkClient(h, port, u, p)
        ok, reason = net.connect_and_auth()
        if not ok:
            self.err.config(text=reason)
            return

        self.result = (net, u)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ── Main Chat Window ─────────────────────────────────────────────────────

class ChatApp:
    """Minimal chat UI: sidebar, chat display, composer."""

    def __init__(self, root: tk.Tk, net: NetworkClient, username: str):
        self.root = root
        self.net = net
        self.username = username
        self.current_context = f"group_{GROUPS[0]}"
        self.online_users: list = []
        self.dm_partners: set = set()
        self.unread: dict = {}
        self.histories: dict = {}
        self.pending: dict | None = None
        self.group_ctxs: list = []
        self.dm_ctxs: list = []

        root.title(f"Chatty \u2014 {username}")
        root.geometry("960x640")
        root.minsize(760, 500)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self._quit)

        self._build_menu()
        self._build_ui()
        self._wire_network()
        self._load_initial()
        net.start_recv_loop()

    # ── Menu ──────────────────────────────────────────────────────────

    def _build_menu(self):
        bar = tk.Menu(self.root, bg=BG_SIDE, fg=FG,
                      activebackground=ACCENT, activeforeground=BG,
                      relief="flat")
        fm = tk.Menu(bar, tearoff=0, bg=BG_SIDE, fg=FG,
                     activebackground=ACCENT, activeforeground=BG)
        fm.add_command(label="Export Chat\u2026", command=self._export_chat)
        fm.add_separator()
        fm.add_command(label="Quit", command=self._quit)
        bar.add_cascade(label="File", menu=fm)
        self.root.config(menu=bar)

    # ── Layout ────────────────────────────────────────────────────────

    def _build_ui(self):
        pw = tk.PanedWindow(self.root, orient="horizontal", bg=BORDER,
                            sashwidth=2, sashrelief="flat")
        pw.pack(fill="both", expand=True)

        # ---- sidebar ----
        side = tk.Frame(pw, bg=BG_SIDE)
        pw.add(side, minsize=180, width=220)

        tk.Label(side, text="GROUPS", font=("Helvetica", 8, "bold"),
                 bg=BG_SIDE, fg=FG_DIM).pack(anchor="w", padx=14, pady=(14, 4))

        self.group_lb = tk.Listbox(
            side, bg=BG_SIDE, fg=FG, selectbackground=ACCENT,
            selectforeground=BG, relief="flat", font=FONT,
            activestyle="none", highlightthickness=0, bd=0,
            exportselection=False,
        )
        self.group_lb.pack(fill="x", padx=8)
        self.group_lb.bind("<<ListboxSelect>>", self._on_group_sel)
        for g in GROUPS:
            self.group_lb.insert("end", f"  # {g}")
            self.group_ctxs.append(f"group_{g}")
        self.group_lb.configure(height=len(GROUPS))
        self.group_lb.selection_set(0)

        dm_hdr = tk.Frame(side, bg=BG_SIDE)
        dm_hdr.pack(fill="x", padx=14, pady=(16, 4))
        tk.Label(dm_hdr, text="DIRECT MESSAGES", font=("Helvetica", 8, "bold"),
                 bg=BG_SIDE, fg=FG_DIM).pack(side="left")
        tk.Button(dm_hdr, text="+", bg=BG_SIDE, fg=FG_DIM, relief="flat",
                  font=("Helvetica", 10, "bold"), cursor="hand2",
                  activebackground=BG_INPUT, activeforeground=FG,
                  command=self._new_dm).pack(side="right")

        self.dm_lb = tk.Listbox(
            side, bg=BG_SIDE, fg=FG, selectbackground=ACCENT,
            selectforeground=BG, relief="flat", font=FONT,
            activestyle="none", highlightthickness=0, bd=0,
            exportselection=False,
        )
        self.dm_lb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.dm_lb.bind("<<ListboxSelect>>", self._on_dm_sel)

        self.online_lbl = tk.Label(side, text="", font=FONT_SM, bg=BG_SIDE,
                                   fg=FG_DIM, anchor="w", wraplength=200)
        self.online_lbl.pack(fill="x", padx=14, pady=(0, 10))

        # ---- chat area ----
        chat = tk.Frame(pw, bg=BG)
        pw.add(chat, minsize=400)

        hdr = tk.Frame(chat, bg=BG)
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        self.header_lbl = tk.Label(hdr, text="# general", font=FONT_HEAD,
                                   bg=BG, fg=FG, anchor="w")
        self.header_lbl.pack(fill="x")
        self.subtitle_lbl = tk.Label(hdr, text="Public group \u00b7 all members",
                                     font=FONT_SM, bg=BG, fg=FG_DIM, anchor="w")
        self.subtitle_lbl.pack(fill="x")

        tk.Frame(chat, bg=BORDER, height=1).pack(fill="x", padx=14, pady=6)

        # scrollbar + text
        txt_frame = tk.Frame(chat, bg=BG)
        txt_frame.pack(fill="both", expand=True)
        sb = tk.Scrollbar(txt_frame, bg=BG_SIDE, troughcolor=BG,
                          activebackground=FG_DIM)
        sb.pack(side="right", fill="y")
        self.chat_txt = tk.Text(
            txt_frame, bg=BG, fg=FG, wrap="word", relief="flat",
            font=FONT_MONO, state="disabled", cursor="arrow",
            padx=14, pady=8, selectbackground=ACCENT,
            selectforeground=BG, highlightthickness=0, bd=0,
            yscrollcommand=sb.set,
        )
        self.chat_txt.pack(fill="both", expand=True)
        sb.config(command=self.chat_txt.yview)

        self.chat_txt.tag_config("self_name",  foreground=GREEN,  font=("Helvetica", 10, "bold"))
        self.chat_txt.tag_config("other_name", foreground=ACCENT, font=("Helvetica", 10, "bold"))
        self.chat_txt.tag_config("system",     foreground=FG_DIM, font=("Helvetica", 9, "italic"))
        self.chat_txt.tag_config("ts",         foreground=FG_DIM, font=("Courier", 8))
        self.chat_txt.tag_config("img",        foreground=YELLOW)
        self.chat_txt.tag_config("msg",        foreground=FG,     font=FONT_MONO)

        # pending-attachment bar (hidden by default)
        self.attach_bar = tk.Frame(chat, bg=BG_INPUT)
        self.attach_info = tk.Label(self.attach_bar, text="", bg=BG_INPUT,
                                    fg=YELLOW, font=FONT_SM, anchor="w")
        self.attach_info.pack(side="left", padx=8)
        tk.Button(self.attach_bar, text="\u2715", bg=BG_INPUT, fg=RED,
                  relief="flat", font=FONT_SM, cursor="hand2",
                  command=self._clear_pending).pack(side="right", padx=4)

        tk.Frame(chat, bg=BORDER, height=1).pack(fill="x")

        # composer
        comp = tk.Frame(chat, bg=BG_SIDE)
        comp.pack(fill="x")

        tk.Button(comp, text="\U0001f4ce", bg=BG_SIDE, fg=FG, relief="flat",
                  font=("Helvetica", 13), cursor="hand2",
                  activebackground=BG_INPUT,
                  command=self._pick_image).pack(side="left", padx=(10, 2), pady=8)

        self.entry = tk.Entry(comp, bg=BG_INPUT, fg=FG, insertbackground=FG,
                              relief="flat", font=FONT)
        self.entry.pack(side="left", fill="both", expand=True,
                        padx=4, pady=8, ipady=4)
        self.entry.bind("<Return>", lambda _ev: self._send())
        self.entry.focus_set()

        tk.Button(comp, text="Send", bg=ACCENT, fg=BG, relief="flat",
                  font=("Helvetica", 10, "bold"), cursor="hand2",
                  activebackground="#b4d8fa",
                  command=self._send).pack(side="right", padx=(2, 10),
                                           pady=8, ipadx=14)

    # ── Network wiring (thread-safe via root.after) ───────────────────

    def _wire_network(self):
        n = self.net
        n.on_user_list    = lambda u:          self.root.after(0, self._h_users, u)
        n.on_system_event = lambda ev, u:      self.root.after(0, self._h_sys, ev, u)
        n.on_dm           = lambda s, t, m, a: self.root.after(0, self._h_dm, s, t, m, a)
        n.on_group        = lambda s, t, m, a: self.root.after(0, self._h_group, s, t, m, a)
        n.on_disconnected = lambda:            self.root.after(0, self._h_disconn)

    def _h_users(self, users):
        self.online_users = users
        self.online_lbl.config(text=f"Online: {', '.join(users)}")
        self._refresh_dm_list()
        self._update_subtitle()

    def _h_sys(self, event, user):
        verb = "joined" if event == "join" else "left"
        entry = {"kind": "system", "text": f"{user} {verb}",
                 "ts": now_ts(), "event": event}
        self.histories.setdefault("group_general", []).append(entry)
        if self.current_context == "group_general":
            self._append_entry(entry)

    def _h_dm(self, sender, target, message, att):
        other = sender if sender != self.username else target
        ctx = f"dm_{other}"
        entry = _make_entry(sender, message, att)
        self.histories.setdefault(ctx, []).append(entry)
        self.dm_partners.add(other)
        if ctx == self.current_context:
            self._append_entry(entry)
        elif sender != self.username:
            self.unread[other] = self.unread.get(other, 0) + 1
        self._refresh_dm_list()

    def _h_group(self, sender, target, message, att):
        ctx = f"group_{target}"
        entry = _make_entry(sender, message, att)
        self.histories.setdefault(ctx, []).append(entry)
        if ctx == self.current_context:
            self._append_entry(entry)

    def _h_disconn(self):
        messagebox.showinfo("Disconnected", "Connection to server was closed.")
        self._quit()

    # ── Initial load ──────────────────────────────────────────────────

    def _load_initial(self):
        log_dir = self.net.log_dir
        self.dm_partners = set(list_dm_partners(log_dir))
        for g in GROUPS:
            ctx = f"group_{g}"
            self.histories[ctx] = load_history(log_dir, ctx)
        for p in self.dm_partners:
            ctx = f"dm_{p}"
            self.histories[ctx] = load_history(log_dir, ctx)
        self._refresh_dm_list()
        self._render_chat()

    # ── Context switching ─────────────────────────────────────────────

    def _on_group_sel(self, _ev):
        sel = self.group_lb.curselection()
        if not sel:
            return
        self.dm_lb.selection_clear(0, "end")
        self._switch(self.group_ctxs[sel[0]])

    def _on_dm_sel(self, _ev):
        sel = self.dm_lb.curselection()
        if not sel:
            return
        ctx = self.dm_ctxs[sel[0]]
        self.group_lb.selection_clear(0, "end")
        other = ctx[3:]
        self.unread.pop(other, None)
        self._switch(ctx)
        self._refresh_dm_list()

    def _switch(self, ctx):
        if ctx == self.current_context:
            return
        self.current_context = ctx
        self.histories.setdefault(ctx, [])
        self._update_header()
        self._update_subtitle()
        self._render_chat()

    def _update_header(self):
        ctx = self.current_context
        if ctx.startswith("group_"):
            self.header_lbl.config(text=f"# {ctx.split('_', 1)[1]}")
        else:
            self.header_lbl.config(text=ctx.split("_", 1)[1])

    def _update_subtitle(self):
        ctx = self.current_context
        if ctx.startswith("group_"):
            self.subtitle_lbl.config(text="Public group \u00b7 all members")
        else:
            other = ctx.split("_", 1)[1]
            st = "online" if other in self.online_users else "offline"
            self.subtitle_lbl.config(text=f"Direct message \u00b7 {st}")

    # ── Chat rendering ────────────────────────────────────────────────

    def _render_chat(self):
        self.chat_txt.config(state="normal")
        self.chat_txt.delete("1.0", "end")
        for e in self.histories.get(self.current_context, []):
            self._insert(e)
        self.chat_txt.config(state="disabled")
        self.chat_txt.see("end")

    def _append_entry(self, entry):
        self.chat_txt.config(state="normal")
        self._insert(entry)
        self.chat_txt.config(state="disabled")
        self.chat_txt.see("end")

    def _insert(self, e):
        if e["kind"] == "system":
            self.chat_txt.insert("end", f"  --- {e['text']} ---\n", "system")
            return
        ts = fmt_ts(e.get("ts", ""))
        sender = e["sender"]
        tag = "self_name" if sender == self.username else "other_name"
        self.chat_txt.insert("end", f"[{ts}] ", "ts")
        self.chat_txt.insert("end", sender, tag)
        self.chat_txt.insert("end", ": ", "msg")
        if e["kind"] == "image":
            self.chat_txt.insert("end", f"[\U0001f4f7 {e.get('name', 'image')}] ", "img")
        self.chat_txt.insert("end", f"{e.get('text', '')}\n", "msg")

    # ── Sidebar DM list ───────────────────────────────────────────────

    def _refresh_dm_list(self):
        prev = None
        sel = self.dm_lb.curselection()
        if sel and sel[0] < len(self.dm_ctxs):
            prev = self.dm_ctxs[sel[0]]

        self.dm_lb.delete(0, "end")
        self.dm_ctxs.clear()
        online = set(self.online_users)
        partners = (self.dm_partners | online) - {self.username}

        def key(u):
            return (-self.unread.get(u, 0), u not in online, u.lower())

        for u in sorted(partners, key=key):
            badge = f" ({self.unread[u]})" if self.unread.get(u) else ""
            dot = "\u25cf " if u in online else "\u25cb "
            self.dm_lb.insert("end", f"  {dot}{u}{badge}")
            self.dm_ctxs.append(f"dm_{u}")

        if prev and prev in self.dm_ctxs:
            self.dm_lb.selection_set(self.dm_ctxs.index(prev))

    # ── Send ──────────────────────────────────────────────────────────

    def _send(self):
        text = self.entry.get().strip()
        att = None
        if self.pending:
            att = {
                "mime": self.pending["mime"],
                "name": self.pending["name"],
                "data": base64.b64encode(self.pending["data"]).decode("ascii"),
            }
        if not text and att is None:
            return
        kind, _, target = self.current_context.partition("_")
        self.net.send(kind, target, text, attachment=att)
        self.entry.delete(0, "end")
        if att:
            self._clear_pending()

    # ── Image attachment ──────────────────────────────────────────────

    def _pick_image(self):
        exts = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
        path = filedialog.askopenfilename(
            title="Attach an image",
            filetypes=[("Images", exts), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            messagebox.showerror("Error", str(e))
            return
        self.pending = {"mime": mime_for_path(path),
                        "name": os.path.basename(path), "data": data}
        self.attach_info.config(text=f"\U0001f4ce {self.pending['name']}")
        self.attach_bar.pack(fill="x", padx=14, pady=(4, 0))

    def _clear_pending(self):
        self.pending = None
        self.attach_bar.pack_forget()

    # ── New DM ────────────────────────────────────────────────────────

    def _new_dm(self):
        user = simpledialog.askstring("New DM", "Enter username:",
                                      parent=self.root)
        if not user or not user.strip():
            return
        user = user.strip()
        self.dm_partners.add(user)
        ctx = f"dm_{user}"
        self.histories.setdefault(ctx, [])
        self._refresh_dm_list()
        self.group_lb.selection_clear(0, "end")
        if ctx in self.dm_ctxs:
            self.dm_lb.selection_set(self.dm_ctxs.index(ctx))
        self._switch(ctx)

    # ── Export ────────────────────────────────────────────────────────

    def _export_chat(self):
        ctx = self.current_context
        path = filedialog.asksaveasfilename(
            title="Export Chat", defaultextension=".txt",
            initialfile=f"{ctx}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                name = ctx.replace("_", " ", 1)
                f.write(f"# {name} \u2014 exported "
                        f"{datetime.now():%Y-%m-%d %H:%M}\n\n")
                for e in self.histories.get(ctx, []):
                    if e["kind"] == "system":
                        f.write(f"-- {e['text']} --\n")
                    elif e["kind"] == "image":
                        cap = f"  {e['text']}" if e.get("text") else ""
                        f.write(f"[{e['ts']}] {e['sender']}: "
                                f"[image: {e.get('name', '')}]{cap}\n")
                    else:
                        f.write(f"[{e['ts']}] {e['sender']}: {e['text']}\n")
            messagebox.showinfo("Exported", f"Saved to {path}")
        except OSError as e:
            messagebox.showerror("Export failed", str(e))

    # ── Quit ──────────────────────────────────────────────────────────

    def _quit(self):
        try:
            self.net.stop()
        except Exception:
            pass
        self.root.destroy()


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_entry(sender, message, att):
    entry = {"kind": "msg", "sender": sender, "text": message, "ts": now_ts()}
    if att:
        entry["kind"] = "image"
        for k in ("path", "name", "mime"):
            entry[k] = att.get(k, "")
    return entry


# ── Entry point ──────────────────────────────────────────────────────────

def main() -> int:
    root = tk.Tk()
    root.withdraw()

    login = LoginDialog(root)
    root.wait_window(login)

    if login.result is None:
        root.destroy()
        return 0

    net, username = login.result
    ChatApp(root, net, username)
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

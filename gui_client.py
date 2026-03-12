#!/usr/bin/env python3
"""
GUI Chat Client — dark/neon-purple tkinter interface.

Wraps ChatClient with a fully featured GUI:
  - Login / Register screen
  - User & group sidebar
  - Per-conversation chat log (colour-coded by message type)
  - UDP file-transfer button
  - Thread-safe updates via a queue drained on the main thread

Run:
    python3 gui_client.py [--host localhost] [--port 8888]
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import threading
import queue
import time
import argparse
import os
from typing import Optional, Callable

from client import ChatClient
from models import Message, MessageCategory, MessageType

# ─────────────────────────── colour palette ────────────────────────────────
BG        = "#0d0d1a"   # near-black canvas
BG2       = "#13102b"   # sidebar / panel
BG3       = "#1a1640"   # input-bar / header strip
ACCENT    = "#b026ff"   # neon purple — primary buttons
ACCENT2   = "#7b2fff"   # deeper purple — pressed / hover
HIGHLIGHT = "#e040fb"   # hot-pink-purple — own messages
TEXT      = "#e8e8ff"   # main text
TEXT_DIM  = "#7a7aaa"   # timestamps / dim labels
SUCCESS   = "#39ff14"   # neon green — group messages / ok status
ERROR_COL = "#ff3366"   # neon red — errors
BORDER    = "#2a1f5a"   # subtle borders
SEL_BG    = "#2d1b5e"   # listbox selection

# ─────────────────────────── fonts ─────────────────────────────────────────
FONT_TITLE  = ("Arial", 16, "bold")
FONT_LABEL  = ("Arial", 11, "bold")
FONT_SMALL  = ("Arial", 9)
FONT_CHAT   = ("Courier New", 10)
FONT_INPUT  = ("Arial", 11)
FONT_BTN    = ("Arial", 10, "bold")

# ─────────────────────────── helpers ───────────────────────────────────────

def _styled_button(parent, text, command, bg=ACCENT, fg=TEXT, **kwargs):
    """Return a flat tk.Button styled for the dark theme."""
    btn = tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=ACCENT2, activeforeground=TEXT,
        relief="flat", bd=0, padx=12, pady=6,
        font=FONT_BTN, cursor="hand2",
        **kwargs
    )
    btn.bind("<Enter>", lambda e: btn.config(bg=ACCENT2))
    btn.bind("<Leave>", lambda e: btn.config(bg=bg))
    return btn


def _styled_entry(parent, show=None, **kwargs):
    """Return a dark-themed (tk.Entry, border_frame) pair."""
    e = tk.Entry(
        parent, bg=BG3, fg=TEXT, insertbackground=ACCENT,
        relief="flat", bd=0, font=FONT_INPUT,
        selectbackground=SEL_BG, selectforeground=TEXT,
        show=show, **kwargs
    )
    border = tk.Frame(parent, bg=BORDER, height=1)
    return e, border


# ═══════════════════════════════════════════════════════════════════════════
#  GUIChatClient — ChatClient with thread-safe GUI callbacks
# ═══════════════════════════════════════════════════════════════════════════

class GUIChatClient(ChatClient):
    """
    Extends ChatClient so that incoming server pushes are routed to the GUI
    via a thread-safe schedule() callback instead of printing to stdout.

    Args:
        schedule: Callable that accepts a zero-argument callable and arranges
                  for it to be run on the GUI main thread.
        host:     Server hostname.
        port:     Server TCP port.
    """

    def __init__(self, schedule: Callable, host: str = "localhost", port: int = 8888):
        super().__init__(host=host, port=port)
        self._schedule = schedule

        # Registered by MainFrame once it is built
        self._on_pm:    Optional[Callable] = None
        self._on_group: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

    # Override so incoming DATA/TEXT messages reach the GUI, not stdout
    def _handle_incoming_message(self, message: Message) -> None:
        if message.category == MessageCategory.DATA and message.type == MessageType.TEXT:
            from_user = message.get_header("From")
            group     = message.get_header("Group")
            text      = message.get_body_text()
            if group and self._on_group:
                self._schedule(lambda g=group, f=from_user, t=text: self._on_group(g, f, t))
            elif not group and self._on_pm:
                self._schedule(lambda f=from_user, t=text: self._on_pm(f, t))
        elif message.category == MessageCategory.CTRL and message.type == MessageType.ERROR:
            code   = message.get_header("Code")
            reason = message.get_header("Reason")
            if self._on_error:
                self._schedule(lambda c=code, r=reason: self._on_error(c, r))


# ═══════════════════════════════════════════════════════════════════════════
#  LoginFrame
# ═══════════════════════════════════════════════════════════════════════════

class LoginFrame(tk.Frame):
    """Register / Login screen shown before authentication."""

    def __init__(self, parent, app: "ChatApp"):
        super().__init__(parent, bg=BG)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        # ── centre card ──────────────────────────────────────────────────
        card = tk.Frame(self, bg=BG2, padx=40, pady=40)
        card.place(relx=0.5, rely=0.5, anchor="center")

        # Logo / title
        tk.Label(card, text="◆ CHAT", font=("Arial", 28, "bold"),
                 bg=BG2, fg=ACCENT).pack(pady=(0, 4))
        tk.Label(card, text="Socket Programming Project",
                 font=FONT_SMALL, bg=BG2, fg=TEXT_DIM).pack(pady=(0, 20))

        # Tab selector
        tab_frame = tk.Frame(card, bg=BG2)
        tab_frame.pack(fill="x", pady=(0, 16))
        self._tab_var = tk.StringVar(value="login")
        for val, label in [("login", "Login"), ("register", "Register")]:
            rb = tk.Radiobutton(
                tab_frame, text=label, variable=self._tab_var, value=val,
                command=self._switch_tab,
                bg=BG2, fg=TEXT, selectcolor=BG2,
                activebackground=BG2, activeforeground=ACCENT,
                indicatoron=False, relief="flat", bd=0,
                font=FONT_BTN, padx=14, pady=6,
            )
            rb.pack(side="left", padx=2)

        # Username
        tk.Label(card, text="Username", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(anchor="w")
        self._user_entry, _ub = _styled_entry(card)
        self._user_entry.pack(fill="x", ipady=6)
        _ub.pack(fill="x", pady=(0, 10))

        # Password
        tk.Label(card, text="Password", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(anchor="w")
        self._pass_entry, _pb = _styled_entry(card, show="●")
        self._pass_entry.pack(fill="x", ipady=6)
        _pb.pack(fill="x", pady=(0, 20))
        self._pass_entry.bind("<Return>", lambda e: self._on_action())

        # Action button
        self._btn = _styled_button(card, "Login", self._on_action, width=20)
        self._btn.pack(fill="x", ipady=4)

        # Status label
        self._status = tk.Label(card, text="", font=FONT_SMALL,
                                bg=BG2, fg=ERROR_COL, wraplength=280)
        self._status.pack(pady=(10, 0))

    # ── internal helpers ──────────────────────────────────────────────────

    def _switch_tab(self):
        mode = self._tab_var.get()
        self._btn.config(text="Login" if mode == "login" else "Register")
        self._status.config(text="", fg=ERROR_COL)

    def _on_action(self):
        username = self._user_entry.get().strip()
        password = self._pass_entry.get().strip()
        if not username or not password:
            self._status.config(text="Username and password required.", fg=ERROR_COL)
            return

        mode = self._tab_var.get()
        self._btn.config(state="disabled")
        self._status.config(text="Connecting…", fg=TEXT_DIM)

        def _task():
            client = self.app.client
            if not client.socket:
                if not client.connect():
                    self.app.schedule(lambda: self._set_status("Cannot connect to server.", ERROR_COL))
                    self.app.schedule(lambda: self._btn.config(state="normal"))
                    return

            if mode == "register":
                ok, msg = client.register(username, password)
                if ok:
                    self.app.schedule(lambda: self._set_status("Registered! Logging in…", SUCCESS))
                    time.sleep(0.3)
                else:
                    self.app.schedule(lambda m=msg: self._set_status(m, ERROR_COL))
                    self.app.schedule(lambda: self._btn.config(state="normal"))
                    return

            # Login
            ok, msg, _ = client.login(username, password)
            if ok:
                client.start_listening()
                self.app.schedule(self.app._show_main)
            else:
                self.app.schedule(lambda m=msg: self._set_status(m, ERROR_COL))
                self.app.schedule(lambda: self._btn.config(state="normal"))

        threading.Thread(target=_task, daemon=True).start()

    def _set_status(self, text, color=ERROR_COL):
        self._status.config(text=text, fg=color)


# ═══════════════════════════════════════════════════════════════════════════
#  MainFrame
# ═══════════════════════════════════════════════════════════════════════════

class MainFrame(tk.Frame):
    """Main chat window shown after login."""

    def __init__(self, parent, app: "ChatApp"):
        super().__init__(parent, bg=BG)
        self.app = app
        self._target: Optional[str]    = None   # username or group name
        self._target_type: str         = "pm"   # "pm" | "group"
        self._refresh_job              = None
        self._build_ui()
        self._register_callbacks()

    # ── layout ────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_topbar()
        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True)
        self._build_sidebar(content)
        self._build_chat_pane(content)

    def _build_topbar(self):
        bar = tk.Frame(self, bg=BG3, pady=8)
        bar.pack(fill="x")

        tk.Label(bar, text="◆ CHAT", font=FONT_TITLE,
                 bg=BG3, fg=ACCENT).pack(side="left", padx=16)

        # Right side buttons
        self._logout_btn = _styled_button(bar, "Logout", self._on_logout,
                                          bg=BG3, fg=TEXT_DIM)
        self._logout_btn.pack(side="right", padx=8)

        self._file_btn = _styled_button(bar, "📎 Send File", self._on_send_file,
                                        bg=BG3, fg=ACCENT)
        self._file_btn.pack(side="right", padx=4)

        self._user_label = tk.Label(bar, text="", font=FONT_LABEL,
                                    bg=BG3, fg=HIGHLIGHT)
        self._user_label.pack(side="right", padx=16)

    def _build_sidebar(self, parent):
        sidebar = tk.Frame(parent, bg=BG2, width=180)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # ── Users ──────────────────────────────────────────────────────
        tk.Label(sidebar, text="USERS", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(anchor="w", padx=10, pady=(12, 2))

        user_frame = tk.Frame(sidebar, bg=BG2)
        user_frame.pack(fill="x", padx=6)

        user_scroll = tk.Scrollbar(user_frame, bg=BG2, troughcolor=BG2,
                                   relief="flat")
        self._user_lb = tk.Listbox(
            user_frame, bg=BG2, fg=TEXT, selectbackground=SEL_BG,
            selectforeground=ACCENT, relief="flat", bd=0,
            font=FONT_CHAT, height=8, activestyle="none",
            yscrollcommand=user_scroll.set,
        )
        user_scroll.config(command=self._user_lb.yview)
        self._user_lb.pack(side="left", fill="x", expand=True)
        user_scroll.pack(side="right", fill="y")
        self._user_lb.bind("<<ListboxSelect>>", self._on_user_select)

        # ── Groups ─────────────────────────────────────────────────────
        tk.Label(sidebar, text="GROUPS", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(anchor="w", padx=10, pady=(16, 2))

        grp_frame = tk.Frame(sidebar, bg=BG2)
        grp_frame.pack(fill="x", padx=6)

        grp_scroll = tk.Scrollbar(grp_frame, bg=BG2, troughcolor=BG2,
                                  relief="flat")
        self._grp_lb = tk.Listbox(
            grp_frame, bg=BG2, fg=TEXT, selectbackground=SEL_BG,
            selectforeground=SUCCESS, relief="flat", bd=0,
            font=FONT_CHAT, height=8, activestyle="none",
            yscrollcommand=grp_scroll.set,
        )
        grp_scroll.config(command=self._grp_lb.yview)
        self._grp_lb.pack(side="left", fill="x", expand=True)
        grp_scroll.pack(side="right", fill="y")
        self._grp_lb.bind("<<ListboxSelect>>", self._on_group_select)

        # ── Group action buttons ────────────────────────────────────────
        btn_row = tk.Frame(sidebar, bg=BG2)
        btn_row.pack(fill="x", padx=6, pady=6)
        _styled_button(btn_row, "+ New", self._on_new_group, bg=ACCENT).pack(
            side="left", fill="x", expand=True, padx=2)
        _styled_button(btn_row, "Join", self._on_join_group, bg=BG3).pack(
            side="left", fill="x", expand=True, padx=2)
        _styled_button(btn_row, "Leave", self._on_leave_group, bg=BG3).pack(
            side="left", fill="x", expand=True, padx=2)

        # Separator
        tk.Frame(sidebar, bg=BORDER, height=1).pack(fill="x", padx=6, pady=4)

        # Refresh button
        _styled_button(sidebar, "⟳ Refresh", self._manual_refresh,
                       bg=BG2, fg=TEXT_DIM).pack(anchor="w", padx=6, pady=4)

    def _build_chat_pane(self, parent):
        pane = tk.Frame(parent, bg=BG)
        pane.pack(side="left", fill="both", expand=True)

        # ── Header ─────────────────────────────────────────────────────
        self._chat_header = tk.Label(
            pane, text="← Select a user or group to start chatting",
            font=FONT_LABEL, bg=BG3, fg=TEXT_DIM, anchor="w", padx=14, pady=8
        )
        self._chat_header.pack(fill="x")
        tk.Frame(pane, bg=ACCENT, height=1).pack(fill="x")

        # ── Message log ────────────────────────────────────────────────
        log_frame = tk.Frame(pane, bg=BG)
        log_frame.pack(fill="both", expand=True)

        log_scroll = tk.Scrollbar(log_frame, bg=BG, troughcolor=BG, relief="flat")
        log_scroll.pack(side="right", fill="y")

        self._log = tk.Text(
            log_frame, bg=BG, fg=TEXT, font=FONT_CHAT,
            relief="flat", bd=0, padx=12, pady=8,
            state="disabled", wrap="word",
            yscrollcommand=log_scroll.set,
            selectbackground=SEL_BG,
        )
        self._log.pack(side="left", fill="both", expand=True)
        log_scroll.config(command=self._log.yview)

        # Configure text tags
        self._log.tag_configure("own",    foreground=HIGHLIGHT, justify="right")
        self._log.tag_configure("pm",     foreground=ACCENT)
        self._log.tag_configure("group",  foreground=SUCCESS)
        self._log.tag_configure("system", foreground=TEXT_DIM, font=("Arial", 9, "italic"))
        self._log.tag_configure("error",  foreground=ERROR_COL)
        self._log.tag_configure("ts",     foreground=TEXT_DIM, font=("Arial", 8))

        # ── Input bar ──────────────────────────────────────────────────
        input_bar = tk.Frame(pane, bg=BG3, pady=8, padx=8)
        input_bar.pack(fill="x")

        self._input = tk.Entry(
            input_bar, bg=BG, fg=TEXT, insertbackground=ACCENT,
            relief="flat", bd=0, font=FONT_INPUT,
            selectbackground=SEL_BG,
        )
        self._input.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self._input.bind("<Return>",    lambda e: self._on_send())
        self._input.bind("<KP_Enter>",  lambda e: self._on_send())

        _styled_button(input_bar, "Send ↵", self._on_send).pack(side="right", ipady=4)

        # ── Status strip ───────────────────────────────────────────────
        self._status_lbl = tk.Label(
            pane, text="", font=FONT_SMALL,
            bg=BG, fg=TEXT_DIM, anchor="w", padx=14
        )
        self._status_lbl.pack(fill="x")

    # ── Callbacks registration ─────────────────────────────────────────────

    def _register_callbacks(self):
        c = self.app.client
        c._on_pm    = self._on_incoming_pm
        c._on_group = self._on_incoming_group
        c._on_error = self._on_incoming_error

    # ── Refresh logic ──────────────────────────────────────────────────────

    def start_refresh(self):
        """Kick off the first refresh and schedule subsequent ones."""
        self._user_label.config(text=f"● {self.app.client.username}")
        self._do_refresh()

    def _do_refresh(self):
        def _task():
            client = self.app.client
            if not client.session_token:
                return
            ok_u, users  = client.list_users()
            ok_g, groups = client.list_groups()
            self.app.schedule(lambda: self._update_users(users  if ok_u else []))
            self.app.schedule(lambda: self._update_groups(groups if ok_g else []))

        threading.Thread(target=_task, daemon=True).start()
        self._refresh_job = self.app.root.after(5000, self._do_refresh)

    def _manual_refresh(self):
        if self._refresh_job:
            self.app.root.after_cancel(self._refresh_job)
        self._do_refresh()

    def stop_refresh(self):
        if self._refresh_job:
            self.app.root.after_cancel(self._refresh_job)
            self._refresh_job = None

    def _update_users(self, users):
        self._user_lb.delete(0, "end")
        for u in users:
            prefix = "●" if u == self.app.client.username else "○"
            self._user_lb.insert("end", f" {prefix} {u}")

    def _update_groups(self, groups):
        self._grp_lb.delete(0, "end")
        for g in groups:
            self._grp_lb.insert("end", f"  {g}")

    # ── Selection handlers ─────────────────────────────────────────────────

    def _on_user_select(self, event):
        sel = self._user_lb.curselection()
        if not sel:
            return
        raw  = self._user_lb.get(sel[0]).strip()
        name = raw.lstrip("●○").strip()
        self._target      = name
        self._target_type = "pm"
        self._chat_header.config(text=f"[PM] {name}", fg=ACCENT)
        self._grp_lb.selection_clear(0, "end")

    def _on_group_select(self, event):
        sel = self._grp_lb.curselection()
        if not sel:
            return
        name = self._grp_lb.get(sel[0]).strip()
        self._target      = name
        self._target_type = "group"
        self._chat_header.config(text=f"[Group: {name}]", fg=SUCCESS)
        self._user_lb.selection_clear(0, "end")

    # ── Send ───────────────────────────────────────────────────────────────

    def _on_send(self):
        text = self._input.get().strip()
        if not text:
            return
        if not self._target:
            self._set_status("Select a user or group first.", ERROR_COL)
            return

        self._input.delete(0, "end")
        target      = self._target
        target_type = self._target_type

        def _task():
            client = self.app.client
            if target_type == "pm":
                ok, msg = client.send_text(target, text)
                if ok:
                    ts = time.strftime("%H:%M")
                    self.app.schedule(lambda: self.append_message(
                        "own", f"[{ts}] You → {target}: {text}\n"))
                else:
                    self.app.schedule(lambda m=msg: self._set_status(m, ERROR_COL))
            else:
                ok, msg = client.send_group_text(target, text)
                if ok:
                    ts = time.strftime("%H:%M")
                    self.app.schedule(lambda: self.append_message(
                        "own", f"[{ts}] You → [{target}]: {text}\n"))
                else:
                    self.app.schedule(lambda m=msg: self._set_status(m, ERROR_COL))

        threading.Thread(target=_task, daemon=True).start()

    # ── Group management ───────────────────────────────────────────────────

    def _on_new_group(self):
        name = simpledialog.askstring("New Group", "Group name:", parent=self.app.root)
        if not name:
            return

        def _task():
            ok, msg = self.app.client.create_group(name.strip())
            color = SUCCESS if ok else ERROR_COL
            self.app.schedule(lambda: self._set_status(msg, color))
            if ok:
                self.app.schedule(self._manual_refresh)

        threading.Thread(target=_task, daemon=True).start()

    def _on_join_group(self):
        name = simpledialog.askstring("Join Group", "Group name:", parent=self.app.root)
        if not name:
            return

        def _task():
            ok, msg = self.app.client.join_group(name.strip())
            color = SUCCESS if ok else ERROR_COL
            self.app.schedule(lambda: self._set_status(msg, color))
            if ok:
                self.app.schedule(self._manual_refresh)

        threading.Thread(target=_task, daemon=True).start()

    def _on_leave_group(self):
        if self._target_type != "group" or not self._target:
            self._set_status("Select a group first.", ERROR_COL)
            return
        name = self._target

        def _task():
            ok, msg = self.app.client.leave_group(name)
            color = SUCCESS if ok else ERROR_COL
            self.app.schedule(lambda: self._set_status(msg, color))
            if ok:
                self.app.schedule(self._manual_refresh)

        threading.Thread(target=_task, daemon=True).start()

    # ── File transfer ──────────────────────────────────────────────────────

    def _on_send_file(self):
        if self._target_type != "pm" or not self._target:
            self._set_status("Select a user (not a group) for file transfer.", ERROR_COL)
            return

        path = filedialog.askopenfilename(title="Select file to send", parent=self.app.root)
        if not path:
            return

        recipient = self._target
        self._set_status(f"Sending {os.path.basename(path)} to {recipient}…", TEXT_DIM)

        def _task():
            ok = self.app.client.send_file(recipient, path)
            if ok:
                msg = f"File '{os.path.basename(path)}' sent to {recipient}."
                self.app.schedule(lambda: self._set_status(msg, SUCCESS))
                self.app.schedule(lambda: self.append_message(
                    "system", f"[File sent] {os.path.basename(path)} → {recipient}\n"))
            else:
                self.app.schedule(lambda: self._set_status(
                    "File send failed — check logs.", ERROR_COL))

        threading.Thread(target=_task, daemon=True).start()

    # ── Logout ─────────────────────────────────────────────────────────────

    def _on_logout(self):
        self.stop_refresh()

        def _task():
            self.app.client.logout()
            self.app.schedule(self.app._show_login)

        threading.Thread(target=_task, daemon=True).start()

    # ── Incoming message callbacks (always called on main thread) ──────────

    def _on_incoming_pm(self, from_user: str, text: str):
        ts = time.strftime("%H:%M")
        self.append_message("pm", f"[{ts}] [PM] {from_user}: {text}\n")

    def _on_incoming_group(self, group: str, from_user: str, text: str):
        ts = time.strftime("%H:%M")
        self.append_message("group", f"[{ts}] [{group}] {from_user}: {text}\n")

    def _on_incoming_error(self, code: str, reason: str):
        self.append_message("error", f"[Error {code}] {reason}\n")

    # ── Thread-safe message append ─────────────────────────────────────────

    def append_message(self, tag: str, text: str):
        """Append coloured text to the chat log. Must be called on the main thread."""
        self._log.config(state="normal")
        self._log.insert("end", text, tag)
        self._log.see("end")
        self._log.config(state="disabled")

    def _set_status(self, text: str, color: str = TEXT_DIM):
        self._status_lbl.config(text=text, fg=color)


# ═══════════════════════════════════════════════════════════════════════════
#  ChatApp — top-level controller
# ═══════════════════════════════════════════════════════════════════════════

class ChatApp:
    """Owns the Tk root window and orchestrates LoginFrame ↔ MainFrame swaps."""

    def __init__(self, host: str = "localhost", port: int = 8888):
        self.root = tk.Tk()
        self.root.title("◆ Chat App")
        self.root.geometry("900x620")
        self.root.minsize(720, 480)
        self.root.configure(bg=BG)

        # Apply ttk theme baseline
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=BG3)
        style.configure("TScrollbar", background=BG2, troughcolor=BG2,
                        bordercolor=BG2, arrowcolor=TEXT_DIM)

        # Thread-safe callback queue: background threads put lambdas here;
        # _pump() drains it on the main thread every 20 ms.
        self._cb_queue: queue.Queue = queue.Queue()
        self._pump()

        self.client = GUIChatClient(self.schedule, host=host, port=port)

        self._login_frame: Optional[LoginFrame] = None
        self._main_frame:  Optional[MainFrame]  = None

        self._show_login()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Thread-safe scheduler ──────────────────────────────────────────────

    def schedule(self, cb: Callable) -> None:
        """Put cb onto the queue for execution on the main thread. Safe from any thread."""
        self._cb_queue.put(cb)

    def _pump(self) -> None:
        """Drain the callback queue — runs on the main thread every 20 ms."""
        while True:
            try:
                cb = self._cb_queue.get_nowait()
                cb()
            except queue.Empty:
                break
        self.root.after(20, self._pump)

    # ── Frame management ───────────────────────────────────────────────────

    def _show_login(self):
        if self._main_frame:
            self._main_frame.destroy()
            self._main_frame = None
        if self._login_frame:
            self._login_frame.destroy()

        # Fresh client for each login session (cleans up sockets)
        host, port = self.client.host, self.client.port
        self.client.disconnect()
        self.client = GUIChatClient(self.schedule, host=host, port=port)

        self._login_frame = LoginFrame(self.root, self)
        self._login_frame.pack(fill="both", expand=True)

    def _show_main(self):
        if self._login_frame:
            self._login_frame.destroy()
            self._login_frame = None

        self._main_frame = MainFrame(self.root, self)
        self._main_frame.pack(fill="both", expand=True)
        self._main_frame.start_refresh()
        self._main_frame.append_message(
            "system",
            f"Welcome, {self.client.username}! "
            "Select a user or group from the sidebar to start chatting.\n"
        )

    def _on_close(self):
        try:
            if self._main_frame:
                self._main_frame.stop_refresh()
            if self.client.session_token:
                self.client.logout()
            self.client.disconnect()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Chat GUI Client")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=8888, help="Server port")
    args = parser.parse_args()

    app = ChatApp(host=args.host, port=args.port)
    app.run()


if __name__ == "__main__":
    main()

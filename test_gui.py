#!/usr/bin/env python3
"""
Headless GUI test suite for gui_client.py.

Tests run without a real server using MockChatClient.
tkinter is driven via root.update() — no interactive display required
as long as a virtual display (Xvfb / $DISPLAY) is available.

Run:
    python3 -m unittest test_gui -v
"""

import threading
import time
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

# ── Import the modules under test ──────────────────────────────────────────
from gui_client import (
    BG, BG2, BG3, ACCENT, SUCCESS, ERROR_COL, TEXT_DIM,
    ChatApp, LoginFrame, MainFrame, GUIChatClient,
    _styled_button, _styled_entry,
)
from models import Message, MessageCategory, MessageType


# ═══════════════════════════════════════════════════════════════════════════
#  Test helpers
# ═══════════════════════════════════════════════════════════════════════════

def _flush(root, pending=None, n=20):
    """Process pending tkinter events and drain the schedule queue."""
    for _ in range(n):
        if pending:
            while pending:
                pending.pop(0)()
        root.update_idletasks()
        root.update()


def _make_root():
    root = tk.Tk()
    root.withdraw()
    return root


class MockChatClient:
    """Minimal stub that mimics the ChatClient public API."""

    def __init__(self):
        self.socket        = None
        self.session_token = None
        self.username      = None
        self.running       = False
        self.udp_socket    = None
        self.udp_port      = None
        self.udp_running   = False
        self.host          = "localhost"
        self.port          = 8888
        self._on_pm        = None
        self._on_group     = None
        self._on_error     = None

        # Configurable return values
        self.connect_ok      = True
        self.register_result = (True,  "Registration successful")
        self.login_result    = (True,  "Login successful", "fake-token")
        self.logout_result   = True
        self.list_users_res  = (True,  ["alice", "bob"])
        self.list_groups_res = (True,  ["adventurers"])
        self.send_text_res   = (True,  "Message sent to bob")
        self.send_group_res  = (True,  "Message sent to group 'adventurers'")
        self.create_grp_res  = (True,  "Group 'newgroup' created")
        self.join_grp_res    = (True,  "Joined group 'adventurers'")
        self.leave_grp_res   = (True,  "Left group 'adventurers'")
        self.send_file_res   = True

        # Call tracking
        self.calls = []

    def connect(self):
        self.calls.append("connect")
        if self.connect_ok:
            self.socket = MagicMock()
        return self.connect_ok

    def disconnect(self):
        self.calls.append("disconnect")
        self.socket = self.session_token = self.username = None

    def register(self, u, p):
        self.calls.append(("register", u, p))
        return self.register_result

    def login(self, u, p):
        self.calls.append(("login", u, p))
        ok, msg, tok = self.login_result
        if ok:
            self.session_token = tok
            self.username      = u
            self.socket        = MagicMock()
        return ok, msg, tok

    def logout(self):
        self.calls.append("logout")
        self.session_token = self.username = None
        return self.logout_result

    def start_listening(self):
        self.calls.append("start_listening")
        self.running = True

    def list_users(self):
        self.calls.append("list_users")
        return self.list_users_res

    def list_groups(self):
        self.calls.append("list_groups")
        return self.list_groups_res

    def send_text(self, recipient, text):
        self.calls.append(("send_text", recipient, text))
        return self.send_text_res

    def send_group_text(self, group, text):
        self.calls.append(("send_group_text", group, text))
        return self.send_group_res

    def create_group(self, name):
        self.calls.append(("create_group", name))
        return self.create_grp_res

    def join_group(self, name):
        self.calls.append(("join_group", name))
        return self.join_grp_res

    def leave_group(self, name):
        self.calls.append(("leave_group", name))
        return self.leave_grp_res

    def send_file(self, recipient, path):
        self.calls.append(("send_file", recipient, path))
        return self.send_file_res

    def _init_udp(self):
        return True


class _AppStub:
    """Minimal ChatApp stub for frame tests.

    Provides a schedule() method that collects callbacks into a list so
    that _flush() can drain them synchronously.
    """
    def __init__(self, root, mock_client):
        self.root    = root
        self.client  = mock_client
        self.shown   = None
        self._sched  = []        # pending callbacks from background threads

    def schedule(self, cb):
        self._sched.append(cb)

    def _flush_pending(self):
        while self._sched:
            self._sched.pop(0)()

    def _show_main(self):
        self.shown = "main"

    def _show_login(self):
        self.shown = "login"


def _wait_for(root, app_stub, condition, timeout=4.0, msg="Condition not met in time"):
    """Poll until condition() is True, draining the schedule queue each tick."""
    deadline = time.time() + timeout
    while not condition() and time.time() < deadline:
        app_stub._flush_pending()
        root.update_idletasks()
        root.update()
        time.sleep(0.02)
    return condition()


# ═══════════════════════════════════════════════════════════════════════════
#  TestColors — verify the colour constants meet dark/purple criteria
# ═══════════════════════════════════════════════════════════════════════════

class TestColors(unittest.TestCase):

    def _hex_to_rgb(self, hex_color):
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def test_bg_is_dark(self):
        r, g, b = self._hex_to_rgb(BG)
        self.assertLess((r + g + b) / 3, 50, "BG should be a dark colour")

    def test_accent_is_purple_range(self):
        r, g, b = self._hex_to_rgb(ACCENT)
        self.assertGreater(r, 100, "ACCENT red channel should be bright")
        self.assertGreater(b, 100, "ACCENT blue channel should be bright")
        self.assertLess(g, 80,    "ACCENT green should be low (purple)")

    def test_error_is_red_dominant(self):
        r, g, b = self._hex_to_rgb(ERROR_COL)
        self.assertGreater(r, g)
        self.assertGreater(r, b)

    def test_success_is_green_dominant(self):
        r, g, b = self._hex_to_rgb(SUCCESS)
        self.assertGreater(g, r)
        self.assertGreater(g, b)

    def test_bg2_darker_than_bg3(self):
        r2, g2, b2 = self._hex_to_rgb(BG2)
        r3, g3, b3 = self._hex_to_rgb(BG3)
        self.assertLess((r2+g2+b2)/3, (r3+g3+b3)/3,
                        "BG2 (sidebar) should be darker than BG3 (input strip)")


# ═══════════════════════════════════════════════════════════════════════════
#  TestHelpers — widget factory functions
# ═══════════════════════════════════════════════════════════════════════════

class TestHelpers(unittest.TestCase):

    def setUp(self):
        self.root = _make_root()

    def tearDown(self):
        self.root.destroy()

    def test_styled_button_has_correct_bg(self):
        btn = _styled_button(self.root, "Test", lambda: None, bg=ACCENT)
        self.assertEqual(btn.cget("bg"), ACCENT)

    def test_styled_button_relief_flat(self):
        btn = _styled_button(self.root, "Test", lambda: None)
        self.assertEqual(str(btn.cget("relief")), "flat")

    def test_styled_entry_returns_tuple(self):
        result = _styled_entry(self.root)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        entry, border = result
        self.assertIsInstance(entry, tk.Entry)
        self.assertIsInstance(border, tk.Frame)

    def test_styled_entry_bg_is_bg3(self):
        entry, _ = _styled_entry(self.root)
        self.assertEqual(entry.cget("bg"), BG3)

    def test_styled_entry_password_show(self):
        entry, _ = _styled_entry(self.root, show="●")
        self.assertEqual(entry.cget("show"), "●")


# ═══════════════════════════════════════════════════════════════════════════
#  TestLoginFrame
# ═══════════════════════════════════════════════════════════════════════════

class TestLoginFrame(unittest.TestCase):

    def setUp(self):
        self.root  = _make_root()
        self.mock  = MockChatClient()
        self.app   = _AppStub(self.root, self.mock)
        self.frame = LoginFrame(self.root, self.app)
        self.frame.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def _set_credentials(self, user, password):
        self.frame._user_entry.delete(0, "end")
        self.frame._user_entry.insert(0, user)
        self.frame._pass_entry.delete(0, "end")
        self.frame._pass_entry.insert(0, password)

    def _wait(self, condition, timeout=4.0, msg="Condition not met"):
        ok = _wait_for(self.root, self.app, condition, timeout=timeout, msg=msg)
        self.assertTrue(ok, msg)

    def test_initial_tab_is_login(self):
        self.assertEqual(self.frame._tab_var.get(), "login")

    def test_button_text_login(self):
        self.assertEqual(self.frame._btn.cget("text"), "Login")

    def test_button_text_switches_to_register(self):
        self.frame._tab_var.set("register")
        self.frame._switch_tab()
        self.assertEqual(self.frame._btn.cget("text"), "Register")

    def test_empty_credentials_shows_error(self):
        self.frame._on_action()
        self.root.update()
        self.assertIn("required", self.frame._status.cget("text").lower())

    def test_login_success_calls_show_main(self):
        self._set_credentials("alice", "wonderland")
        self.frame._on_action()
        self._wait(lambda: self.app.shown == "main")

    def test_login_failure_stays_on_login(self):
        self.mock.login_result = (False, "Invalid credentials", None)
        self._set_credentials("alice", "wrongpass")
        self.frame._on_action()
        self._wait(lambda: "invalid" in self.frame._status.cget("text").lower())
        self.assertNotEqual(self.app.shown, "main")

    def test_register_success_then_login(self):
        self.frame._tab_var.set("register")
        self.frame._switch_tab()
        self._set_credentials("newuser", "pass")
        self.frame._on_action()
        self._wait(lambda: self.app.shown == "main")
        calls = [c[0] if isinstance(c, tuple) else c for c in self.mock.calls]
        self.assertIn("register", calls)
        self.assertIn("login",    calls)

    def test_register_failure_shows_error(self):
        self.mock.register_result = (False, "Username already exists")
        self.frame._tab_var.set("register")
        self.frame._switch_tab()
        self._set_credentials("alice", "pass")
        self.frame._on_action()
        self._wait(lambda: "already" in self.frame._status.cget("text").lower())

    def test_connect_failure_shows_error(self):
        self.mock.connect_ok = False
        self._set_credentials("alice", "wonderland")
        self.frame._on_action()
        self._wait(lambda: "connect" in self.frame._status.cget("text").lower())


# ═══════════════════════════════════════════════════════════════════════════
#  TestMainFrame
# ═══════════════════════════════════════════════════════════════════════════

class TestMainFrame(unittest.TestCase):

    def setUp(self):
        self.root  = _make_root()
        self.mock  = MockChatClient()
        self.mock.session_token = "tok"
        self.mock.username      = "alice"
        self.mock.socket        = MagicMock()
        self.app   = _AppStub(self.root, self.mock)
        self.frame = MainFrame(self.root, self.app)
        self.frame.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        self.frame.stop_refresh()
        self.root.destroy()

    def _wait(self, condition, timeout=4.0, msg="Condition not met"):
        ok = _wait_for(self.root, self.app, condition, timeout=timeout, msg=msg)
        self.assertTrue(ok, msg)

    # ── Sidebar ────────────────────────────────────────────────────────────

    def test_update_users_populates_listbox(self):
        self.frame._update_users(["alice", "bob"])
        self.root.update()
        items = [self.frame._user_lb.get(i) for i in range(self.frame._user_lb.size())]
        combined = " ".join(items)
        self.assertIn("alice", combined)
        self.assertIn("bob", combined)

    def test_own_user_gets_filled_circle(self):
        self.frame._update_users(["alice", "bob"])
        self.root.update()
        alice_item = self.frame._user_lb.get(0)
        self.assertIn("●", alice_item)

    def test_other_user_gets_hollow_circle(self):
        self.frame._update_users(["alice", "bob"])
        self.root.update()
        bob_item = self.frame._user_lb.get(1)
        self.assertIn("○", bob_item)

    def test_update_groups_populates_listbox(self):
        self.frame._update_groups(["adventurers", "builders"])
        self.root.update()
        items = [self.frame._grp_lb.get(i) for i in range(self.frame._grp_lb.size())]
        combined = " ".join(items)
        self.assertIn("adventurers", combined)
        self.assertIn("builders", combined)

    def test_refresh_calls_list_users_and_groups(self):
        self.frame._do_refresh()
        self._wait(lambda: "list_users" in self.mock.calls)
        self._wait(lambda: "list_groups" in self.mock.calls)

    # ── Sending ────────────────────────────────────────────────────────────

    def test_send_without_target_shows_error(self):
        self.frame._input.insert(0, "hello")
        self.frame._on_send()
        self.root.update()
        self.assertIn("select", self.frame._status_lbl.cget("text").lower())

    def test_send_pm_calls_send_text(self):
        self.frame._target      = "bob"
        self.frame._target_type = "pm"
        self.frame._input.insert(0, "Hello Bob!")
        self.frame._on_send()
        self._wait(lambda: any(
            isinstance(c, tuple) and c[0] == "send_text" for c in self.mock.calls))
        call = next(c for c in self.mock.calls
                    if isinstance(c, tuple) and c[0] == "send_text")
        self.assertEqual(call[1], "bob")
        self.assertEqual(call[2], "Hello Bob!")

    def test_send_group_msg_calls_send_group_text(self):
        self.frame._target      = "adventurers"
        self.frame._target_type = "group"
        self.frame._input.insert(0, "Hello group!")
        self.frame._on_send()
        self._wait(lambda: any(
            isinstance(c, tuple) and c[0] == "send_group_text" for c in self.mock.calls))
        call = next(c for c in self.mock.calls
                    if isinstance(c, tuple) and c[0] == "send_group_text")
        self.assertEqual(call[1], "adventurers")
        self.assertEqual(call[2], "Hello group!")

    def test_send_clears_input(self):
        self.frame._target      = "bob"
        self.frame._target_type = "pm"
        self.frame._input.insert(0, "text")
        self.frame._on_send()
        self.root.update()
        self.assertEqual(self.frame._input.get(), "")

    def test_send_failure_shows_status(self):
        self.mock.send_text_res = (False, "User not found")
        self.frame._target      = "ghost"
        self.frame._target_type = "pm"
        self.frame._input.insert(0, "hello")
        self.frame._on_send()
        self._wait(lambda: "user not found" in
                   self.frame._status_lbl.cget("text").lower())

    # ── append_message ─────────────────────────────────────────────────────

    def test_append_message_adds_text(self):
        self.frame.append_message("pm", "Test message\n")
        self.root.update()
        content = self.frame._log.get("1.0", "end")
        self.assertIn("Test message", content)

    def test_append_message_log_disabled_after(self):
        self.frame.append_message("pm", "Hello\n")
        self.root.update()
        self.assertEqual(str(self.frame._log.cget("state")), "disabled")

    def test_append_message_via_schedule(self):
        """Simulate background thread using app.schedule → append_message."""
        self.app.schedule(lambda: self.frame.append_message("pm", "Thread msg\n"))
        self._wait(lambda: "Thread msg" in self.frame._log.get("1.0", "end"))

    # ── Incoming message callbacks (called on main thread) ─────────────────

    def test_incoming_pm_appears_in_log(self):
        self.frame._on_incoming_pm("bob", "Hey!")
        self.root.update()
        content = self.frame._log.get("1.0", "end")
        self.assertIn("[PM] bob: Hey!", content)

    def test_incoming_group_msg_appears_in_log(self):
        self.frame._on_incoming_group("adventurers", "bob", "Group hello!")
        self.root.update()
        content = self.frame._log.get("1.0", "end")
        self.assertIn("[adventurers] bob: Group hello!", content)

    def test_incoming_error_appears_in_log(self):
        self.frame._on_incoming_error("403", "Not a member")
        self.root.update()
        content = self.frame._log.get("1.0", "end")
        self.assertIn("Error 403", content)
        self.assertIn("Not a member", content)

    # ── Group management dialogs (patched) ─────────────────────────────────

    def test_new_group_calls_create_group(self):
        with patch("gui_client.simpledialog.askstring", return_value="newgroup"):
            self.frame._on_new_group()
        self._wait(lambda: any(
            isinstance(c, tuple) and c[0] == "create_group" for c in self.mock.calls))

    def test_join_group_calls_join_group(self):
        with patch("gui_client.simpledialog.askstring", return_value="adventurers"):
            self.frame._on_join_group()
        self._wait(lambda: any(
            isinstance(c, tuple) and c[0] == "join_group" for c in self.mock.calls))

    def test_leave_group_calls_leave_group(self):
        self.frame._target      = "adventurers"
        self.frame._target_type = "group"
        self.frame._on_leave_group()
        self._wait(lambda: any(
            isinstance(c, tuple) and c[0] == "leave_group" for c in self.mock.calls))

    def test_leave_group_without_selection_shows_error(self):
        self.frame._target      = None
        self.frame._target_type = "pm"
        self.frame._on_leave_group()
        self.root.update()
        self.assertIn("select", self.frame._status_lbl.cget("text").lower())

    # ── Selection ──────────────────────────────────────────────────────────

    def test_user_select_sets_target(self):
        self.frame._update_users(["alice", "bob"])
        self.root.update()
        self.frame._user_lb.selection_set(1)
        self.frame._on_user_select(None)
        self.assertEqual(self.frame._target, "bob")
        self.assertEqual(self.frame._target_type, "pm")

    def test_group_select_sets_target(self):
        self.frame._update_groups(["adventurers"])
        self.root.update()
        self.frame._grp_lb.selection_set(0)
        self.frame._on_group_select(None)
        self.assertEqual(self.frame._target, "adventurers")
        self.assertEqual(self.frame._target_type, "group")

    def test_chat_header_updates_on_user_select(self):
        self.frame._update_users(["alice", "bob"])
        self.root.update()
        self.frame._user_lb.selection_set(1)
        self.frame._on_user_select(None)
        self.assertIn("bob", self.frame._chat_header.cget("text"))

    def test_chat_header_updates_on_group_select(self):
        self.frame._update_groups(["adventurers"])
        self.root.update()
        self.frame._grp_lb.selection_set(0)
        self.frame._on_group_select(None)
        self.assertIn("adventurers", self.frame._chat_header.cget("text"))

    # ── Logout ─────────────────────────────────────────────────────────────

    def test_logout_calls_logout(self):
        self.frame._on_logout()
        self._wait(lambda: "logout" in self.mock.calls)

    def test_logout_navigates_to_login(self):
        self.frame._on_logout()
        self._wait(lambda: self.app.shown == "login")


# ═══════════════════════════════════════════════════════════════════════════
#  TestGUIChatClient — callback routing
# ═══════════════════════════════════════════════════════════════════════════

class TestGUIChatClient(unittest.TestCase):

    def setUp(self):
        self.pending = []
        def _sched(cb):
            self.pending.append(cb)

        self.root   = _make_root()
        self.client = GUIChatClient(_sched, host="localhost", port=8888)

    def _drain(self):
        while self.pending:
            self.pending.pop(0)()
        self.root.update_idletasks()
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def _make_text_message(self, from_user, text, group=None):
        msg = Message(category=MessageCategory.DATA, type=MessageType.TEXT)
        msg.set_header("From", from_user)
        if group:
            msg.set_header("Group", group)
        msg.body = text.encode("utf-8")
        return msg

    def _make_error_message(self, code, reason):
        msg = Message(category=MessageCategory.CTRL, type=MessageType.ERROR)
        msg.set_header("Code", str(code))
        msg.set_header("Reason", reason)
        return msg

    def test_incoming_pm_routes_to_on_pm(self):
        received = []
        self.client._on_pm = lambda f, t: received.append((f, t))
        msg = self._make_text_message("bob", "Hello!")
        self.client._handle_incoming_message(msg)
        self._drain()
        self.assertEqual(received, [("bob", "Hello!")])

    def test_incoming_group_msg_routes_to_on_group(self):
        received = []
        self.client._on_group = lambda g, f, t: received.append((g, f, t))
        msg = self._make_text_message("bob", "Group msg", group="adventurers")
        self.client._handle_incoming_message(msg)
        self._drain()
        self.assertEqual(received, [("adventurers", "bob", "Group msg")])

    def test_incoming_error_routes_to_on_error(self):
        received = []
        self.client._on_error = lambda c, r: received.append((c, r))
        msg = self._make_error_message(403, "Not a member")
        self.client._handle_incoming_message(msg)
        self._drain()
        self.assertEqual(received, [("403", "Not a member")])

    def test_no_crash_when_callbacks_none(self):
        """_handle_incoming_message must not raise when callbacks aren't registered."""
        msg = self._make_text_message("bob", "Hi")
        try:
            self.client._handle_incoming_message(msg)
            self._drain()
        except Exception as e:
            self.fail(f"Unexpected exception: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  TestChatApp — top-level controller
# ═══════════════════════════════════════════════════════════════════════════

class TestChatApp(unittest.TestCase):
    """
    Tests for ChatApp._show_login / _show_main.

    We bypass __init__ (which calls mainloop-dependent code) and wire up
    only what we need.
    """

    def _make_app(self):
        """Create a minimal ChatApp-like object without starting mainloop."""
        import queue as _q
        root = tk.Tk()
        root.withdraw()

        app = ChatApp.__new__(ChatApp)
        app.root        = root
        app._cb_queue   = _q.Queue()
        app._login_frame = None
        app._main_frame  = None
        app.client       = GUIChatClient(app.schedule, host="localhost", port=8888)
        return app, root

    def _flush_app(self, app, root, n=20):
        for _ in range(n):
            while True:
                try:
                    app._cb_queue.get_nowait()()
                except Exception:
                    break
            root.update_idletasks()
            root.update()

    def test_show_login_creates_login_frame(self):
        app, root = self._make_app()
        try:
            app._show_login()
            self._flush_app(app, root)
            self.assertIsInstance(app._login_frame, LoginFrame)
        finally:
            root.destroy()

    def test_show_main_creates_main_frame(self):
        app, root = self._make_app()
        try:
            app.client.session_token = "tok"
            app.client.username      = "alice"
            app.client.socket        = MagicMock()
            app.client.running       = True
            app._show_main()
            self._flush_app(app, root)
            self.assertIsInstance(app._main_frame, MainFrame)
        finally:
            if app._main_frame:
                app._main_frame.stop_refresh()
            root.destroy()

    def test_show_main_clears_login_frame(self):
        app, root = self._make_app()
        try:
            app._show_login()
            self._flush_app(app, root)
            self.assertIsNotNone(app._login_frame)

            app.client.session_token = "tok"
            app.client.username      = "alice"
            app.client.socket        = MagicMock()
            app.client.running       = True
            app._show_main()
            self._flush_app(app, root)
            self.assertIsNone(app._login_frame)
        finally:
            if app._main_frame:
                app._main_frame.stop_refresh()
            root.destroy()

    def test_show_login_clears_main_frame(self):
        app, root = self._make_app()
        try:
            app.client.session_token = "tok"
            app.client.username      = "alice"
            app.client.socket        = MagicMock()
            app.client.running       = True
            app._show_main()
            self._flush_app(app, root)
            app._main_frame.stop_refresh()
            app._show_login()
            self._flush_app(app, root)
            self.assertIsNone(app._main_frame)
        finally:
            root.destroy()

    def test_schedule_runs_callback_on_drain(self):
        app, root = self._make_app()
        try:
            results = []
            app.schedule(lambda: results.append(1))
            app.schedule(lambda: results.append(2))
            self._flush_app(app, root)
            self.assertEqual(results, [1, 2])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)

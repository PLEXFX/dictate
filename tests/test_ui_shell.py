"""The bar and tray stand-ins (ui_shell.py) and how main.App starts, restarts and gives up on the WinUI program."""

import os
import subprocess
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config
import ui_shell


class FakeServer:
    def __init__(self, has_client=True):
        self.has_client = has_client
        self.sent = []
        self.name = "dictate-ui-test"
        self.closed = 0
        self.flushed = 0

    def send(self, message):
        self.sent.append(message)

    def flush(self):
        self.flushed += 1

    def close(self):
        self.closed += 1


def channel(connected=True):
    ch = ui_shell.ShellChannel()
    ch.server = FakeServer(has_client=connected)
    return ch


def sent(ch):
    return ch.server.sent


class ShellChannelTests(unittest.TestCase):
    def test_messages_go_out_only_while_a_window_is_connected(self):
        ch = channel(connected=False)
        ch.send({"type": "x"})
        self.assertEqual(sent(ch), [])
        ch.server.has_client = True
        ch.send({"type": "y"})
        self.assertEqual(sent(ch), [{"type": "y"}])

    def test_no_server_yet_is_quiet(self):
        ch = ui_shell.ShellChannel()
        self.assertFalse(ch.connected)
        ch.send({"type": "x"})  # must not raise


class RemoteBarTests(unittest.TestCase):
    def bar(self, connected=True):
        ch = channel(connected)
        return ui_shell.RemoteBar(config.Settings(), ch), ch

    def test_state_changes_are_forwarded(self):
        bar, ch = self.bar()
        bar.set_state("loading", "Downloading Small", 0.4)
        self.assertEqual(sent(ch), [{"type": "bar_state", "state": "loading", "detail": "Downloading Small", "progress": 0.4}])

    def test_levels_are_reduced_to_one_loudness(self):
        bar, ch = self.bar()
        bar.set_levels([0.0, 0.6, 0.8, 0.0])
        # root of the mean of squares: sqrt((0 + 0.36 + 0.64 + 0) / 4) = 0.5
        self.assertEqual(sent(ch), [{"type": "bar_level", "v": 0.5}])

    def test_empty_or_odd_levels_send_nothing(self):
        bar, ch = self.bar()
        bar.set_levels([])
        bar.set_levels(None)
        self.assertEqual(sent(ch), [])

    def test_levels_are_clamped_to_the_unit_range(self):
        bar, ch = self.bar()
        bar.set_levels([5.0, 5.0])
        self.assertEqual(sent(ch)[0]["v"], 1.0)

    def test_a_notice_remembers_its_click_and_tells_the_window_it_is_clickable(self):
        bar, ch = self.bar()
        calls = []
        bar.notify("Update available", tone="info", on_click=lambda: calls.append("open"), duration_ms=9000)
        self.assertEqual(
            sent(ch), [{"type": "bar_notice", "text": "Update available", "tone": "info", "clickable": True, "duration_ms": 9000}]
        )
        bar.handle_click()
        bar.handle_click()  # one use only
        self.assertEqual(calls, ["open"])

    def test_a_plain_notice_is_not_clickable(self):
        bar, ch = self.bar()
        bar.notify("Ready.", tone="success")
        self.assertFalse(sent(ch)[0]["clickable"])

    def test_a_new_state_withdraws_the_notices_click(self):
        bar, _ch = self.bar()
        calls = []
        bar.notify("Update available", on_click=lambda: calls.append(1))
        bar.set_state("listening")
        bar.handle_click()
        self.assertEqual(calls, [])

    def test_a_notice_may_follow_a_notice_state(self):
        bar, _ch = self.bar()
        calls = []
        bar.notify("Update available", on_click=lambda: calls.append(1))
        bar.set_state("notice", "Update available")  # the bar's own notice state keeps the click
        bar.handle_click()
        self.assertEqual(calls, [1])

    def test_a_click_finishes_a_locked_recording_only_when_armed(self):
        bar, _ch = self.bar()
        raised = []
        bar.clicked.connect(lambda: raised.append(1))
        bar.handle_click()
        self.assertEqual(raised, [])
        bar.set_clickable(True)
        bar.handle_click()
        self.assertEqual(raised, [1])
        bar.set_clickable(False)
        bar.handle_click()
        self.assertEqual(raised, [1])

    def test_theme_show_and_hide_are_forwarded_and_the_theme_is_remembered(self):
        bar, ch = self.bar()
        bar.set_theme(False)
        bar.show_bar()
        bar.hide()
        self.assertEqual([m["type"] for m in sent(ch)], ["bar_theme", "bar_show", "bar_hide"])
        self.assertEqual(bar.snapshot(), {"dark": False})

    def test_nothing_is_sent_without_a_window_but_state_is_kept_for_the_next_one(self):
        bar, ch = self.bar(connected=False)
        bar.set_theme(False)
        bar.set_state("idle")
        self.assertEqual(sent(ch), [])
        self.assertEqual(bar.snapshot(), {"dark": False})

    def test_previews_and_live_text_do_nothing(self):
        bar, ch = self.bar()
        bar.preview_width(200)
        bar.preview_margin(10)
        bar.set_live_text("hello")
        bar.update_settings(config.Settings())
        self.assertEqual(sent(ch), [])


class RemoteTrayTests(unittest.TestCase):
    def tray(self, connected=True):
        ch = channel(connected)
        tray = ui_shell.RemoteTray(ch, "tip")
        return tray, ch

    def test_snapshot_lists_the_state_the_window_needs(self):
        tray, _ch = self.tray()
        tray.add_action("settings", "Settings\tCtrl + Alt + D")
        tray.add_action("copy_last", "Copy last dictation", enabled=False)
        tray.add_action("undo", "Undo last dictation", enabled=False)
        tray.add_action("check_update", "Check for updates", enabled=True)
        self.assertEqual(
            tray.snapshot(),
            {"tooltip": "tip", "settings_label": "Settings\tCtrl + Alt + D", "copy_last": False, "undo": False, "check_update": True},
        )

    def test_enabling_an_entry_or_changing_the_tooltip_tells_the_window(self):
        tray, ch = self.tray()
        undo = tray.add_action("undo", "Undo last dictation", enabled=False)
        undo.setEnabled(True)
        tray.setToolTip("new tip")
        self.assertEqual([m["type"] for m in sent(ch)], ["tray", "tray"])
        self.assertTrue(sent(ch)[0]["undo"])
        self.assertEqual(sent(ch)[1]["tooltip"], "new tip")

    def test_setting_the_same_value_again_sends_nothing(self):
        tray, ch = self.tray()
        undo = tray.add_action("undo", "Undo", enabled=False)
        undo.setEnabled(False)
        undo.setText("Undo")
        tray.setToolTip("tip")
        self.assertEqual(sent(ch), [])

    def test_the_hotkey_label_follows_a_rebind(self):
        tray, ch = self.tray()
        action = tray.add_action("settings", "Settings\tCtrl + Alt + D")
        action.setText("Settings\tCtrl + F2")
        self.assertEqual(sent(ch)[-1]["settings_label"], "Settings\tCtrl + F2")

    def test_choosing_an_entry_raises_its_signal(self):
        tray, _ch = self.tray()
        quit_action = tray.add_action("quit", "Quit")
        raised = []
        quit_action.triggered.connect(lambda: raised.append("quit"))
        tray.trigger("quit")
        tray.trigger("nope")  # unknown ids are ignored
        self.assertEqual(raised, ["quit"])

    def test_a_disabled_entry_cannot_be_chosen_but_settings_always_can(self):
        tray, _ch = self.tray()
        undo = tray.add_action("undo", "Undo", enabled=False)
        raised = []
        undo.triggered.connect(lambda: raised.append("undo"))
        tray.trigger("undo")
        self.assertEqual(raised, [])

    def test_a_balloon_carries_its_tone(self):
        from PySide6.QtWidgets import QSystemTrayIcon

        tray, ch = self.tray()
        tray.showMessage("Dictate", "Update ready", QSystemTrayIcon.MessageIcon.Information, 5000)
        tray.showMessage("Dictate", "It broke", QSystemTrayIcon.MessageIcon.Warning, 4000)
        self.assertEqual(
            sent(ch),
            [
                {"type": "balloon", "text": "Update ready", "tone": "info", "duration_ms": 5000},
                {"type": "balloon", "text": "It broke", "tone": "error", "duration_ms": 4000},
            ],
        )


class ShellAppTests(unittest.TestCase):
    """main.App's side, on a bare App with the real methods and fake parts."""

    @staticmethod
    def _app(ui_mode=True, connected=True):
        import main

        app = main.App.__new__(main.App)
        app.settings = config.Settings(onboarding_complete=True)
        app.engine = Mock()
        app.updater = Mock()
        app.hotkeys = Mock()
        app.qt = Mock()
        app._whats_new_notes = "notes"
        app._ui_mode = ui_mode
        app._shell = channel(connected)
        app._ui_server = app._shell.server
        app._ui_proc = None
        app._ui_starts = []
        app._quitting = False
        app._settings_pending = False
        return app, main

    def test_settings_opens_in_the_window_when_it_is_connected(self):
        app, _main = self._app()
        app._show_settings()
        self.assertEqual(sent(app._shell), [{"type": "show_settings"}])

    def test_settings_asked_for_while_the_window_is_away_is_sent_when_it_returns(self):
        app, _main = self._app(connected=False)
        app._show_settings()
        self.assertEqual(sent(app._shell), [])
        self.assertTrue(app._settings_pending)
        app._shell.server.has_client = True
        app._on_ui_connected()
        self.assertEqual(sent(app._shell), [{"type": "show_settings"}])
        self.assertFalse(app._settings_pending)

    def test_remote_tray_entries_run_the_same_handlers_as_the_built_in_menu(self):
        app, _main = self._app()
        app._show_settings = Mock()
        app._copy_last_dictation = Mock()
        app._undo_last_paste = Mock()
        app._check_for_updates = Mock()
        app._quit = Mock()
        app._build_remote_tray()
        app.act_undo.setEnabled(True)
        app.act_copy_last.setEnabled(True)
        for action_id in ("settings", "load", "unload", "copy_last", "undo", "check_update", "quit"):
            app.tray.trigger(action_id)
        app._show_settings.assert_called_once()
        app.engine.preload.assert_called_once()
        app.engine.unload.assert_called_once()
        app._copy_last_dictation.assert_called_once()
        app._undo_last_paste.assert_called_once()
        app._check_for_updates.assert_called_once()
        app._quit.assert_called_once()

    def test_remote_tray_starts_with_the_right_menu_state(self):
        app, _main = self._app()
        app.settings = config.Settings(auto_update_enabled=False)
        app._build_remote_tray()
        snap = app.tray.snapshot()
        self.assertFalse(snap["copy_last"])
        self.assertFalse(snap["undo"])
        self.assertFalse(snap["check_update"])
        self.assertTrue(snap["settings_label"].startswith("Settings\t"))
        self.assertIn("hold", snap["tooltip"])

    def test_the_window_is_not_restarted_while_it_is_connected_or_starting_or_after_quit(self):
        app, main = self._app()
        with patch.object(main.ui_bridge, "launch_ui") as launch:
            app._ensure_ui()  # connected
            app._shell.server.has_client = False
            alive = Mock()
            alive.poll.return_value = None
            app._ui_proc = alive
            app._ensure_ui()  # still starting
            app._ui_proc = None
            app._quitting = True
            app._ensure_ui()  # quitting
        launch.assert_not_called()

    def test_a_window_that_closed_is_started_again(self):
        app, main = self._app(connected=False)
        dead = Mock()
        dead.poll.return_value = 1
        app._ui_proc = dead
        fresh = Mock()
        with patch.object(main.ui_bridge, "launch_ui", return_value=fresh) as launch, patch.object(main, "QTimer"):
            app._ensure_ui()
        launch.assert_called_once_with("dictate-ui-test")
        self.assertIs(app._ui_proc, fresh)
        self.assertEqual(len(app._ui_starts), 1)

    def test_a_window_that_keeps_closing_hands_over_to_the_built_in_one(self):
        app, main = self._app(connected=False)
        dead = Mock()
        dead.poll.return_value = 1
        app._ui_proc = dead
        import time

        app._ui_starts = [time.monotonic()] * main.UI_MAX_RESTARTS
        app._fallback_to_builtin_ui = Mock()
        with patch.object(main.ui_bridge, "launch_ui") as launch:
            app._ensure_ui()
        launch.assert_not_called()
        app._fallback_to_builtin_ui.assert_called_once()

    def test_old_restarts_do_not_count_against_the_limit(self):
        app, main = self._app(connected=False)
        dead = Mock()
        dead.poll.return_value = 1
        app._ui_proc = dead
        import time

        app._ui_starts = [time.monotonic() - main.UI_RESTART_WINDOW_SECONDS - 5] * 10
        app._fallback_to_builtin_ui = Mock()
        with patch.object(main.ui_bridge, "launch_ui", return_value=Mock()), patch.object(main, "QTimer"):
            app._ensure_ui()
        app._fallback_to_builtin_ui.assert_not_called()
        self.assertEqual(len(app._ui_starts), 1)

    def test_a_window_that_cannot_be_started_falls_back(self):
        app, main = self._app(connected=False)
        app._fallback_to_builtin_ui = Mock()
        with patch.object(main.ui_bridge, "launch_ui", return_value=None):
            app._ensure_ui()
        app._fallback_to_builtin_ui.assert_called_once()

    def test_start_falls_back_when_the_pipe_will_not_open(self):
        app, main = self._app()
        app._ui_host = Mock()
        app._fallback_to_builtin_ui = Mock()
        server = Mock()
        server.start.return_value = False
        with patch.object(main.ui_bridge, "make_pipe_server", return_value=server), patch.object(
            main.ui_bridge, "UiBridge"
        ), patch.object(main.ui_bridge, "launch_ui") as launch:
            app._start_ui_shell()
        app._fallback_to_builtin_ui.assert_called_once()
        launch.assert_not_called()

    def test_start_falls_back_when_the_program_cannot_be_launched(self):
        app, main = self._app()
        app._ui_host = Mock()
        app._fallback_to_builtin_ui = Mock()
        server = Mock()
        server.start.return_value = True
        with patch.object(main.ui_bridge, "make_pipe_server", return_value=server), patch.object(
            main.ui_bridge, "UiBridge"
        ), patch.object(main.ui_bridge, "launch_ui", return_value=None):
            app._start_ui_shell()
        app._fallback_to_builtin_ui.assert_called_once()

    def test_start_falls_back_when_the_program_never_connects(self):
        app, main = self._app()
        app._ui_host = Mock()
        app._fallback_to_builtin_ui = Mock()
        app._wait_for_ui = Mock(return_value=False)
        server = Mock()
        server.start.return_value = True
        with patch.object(main.ui_bridge, "make_pipe_server", return_value=server), patch.object(
            main.ui_bridge, "UiBridge"
        ), patch.object(main.ui_bridge, "launch_ui", return_value=Mock()):
            app._start_ui_shell()
        app._wait_for_ui.assert_called_once_with(main.UI_START_SECONDS)
        app._fallback_to_builtin_ui.assert_called_once()

    def test_start_keeps_the_window_when_it_connects(self):
        app, main = self._app()
        app._ui_host = Mock()
        app._fallback_to_builtin_ui = Mock()
        app._wait_for_ui = Mock(return_value=True)
        server = Mock()
        server.start.return_value = True
        with patch.object(main.ui_bridge, "make_pipe_server", return_value=server), patch.object(
            main.ui_bridge, "UiBridge"
        ), patch.object(main.ui_bridge, "launch_ui", return_value=Mock()):
            app._start_ui_shell()
        app._fallback_to_builtin_ui.assert_not_called()
        self.assertIs(app._shell.server, server)

    def test_the_fallback_builds_the_built_in_bar_and_tray(self):
        app, main = self._app()
        app.bar = ui_shell.RemoteBar(app.settings, app._shell)
        app._apply_appearance = Mock()
        app._build_tray = Mock()
        app._refresh_tray_update_indicator = Mock()
        proc = Mock()
        proc.poll.return_value = None
        app._ui_proc = proc
        server = app._ui_server
        with patch.object(main, "Bar") as bar_cls:
            app._fallback_to_builtin_ui()
        self.assertFalse(app._ui_mode)
        self.assertIs(app.bar, bar_cls.return_value)
        app._build_tray.assert_called_once()
        app._apply_appearance.assert_called_once()
        self.assertEqual(server.closed, 1)
        proc.terminate.assert_called_once()
        self.assertIsNone(app._shell.server)

    def test_the_fallback_shows_an_always_visible_bar(self):
        app, main = self._app()
        app.settings = config.Settings(always_visible=True)
        app._apply_appearance = Mock()
        app._build_tray = Mock()
        app._refresh_tray_update_indicator = Mock()
        with patch.object(main, "Bar") as bar_cls:
            app._fallback_to_builtin_ui()
        bar_cls.return_value.show_bar.assert_called_once()

    def test_quitting_tells_the_window_then_closes_the_pipe(self):
        app, _main = self._app()
        proc = Mock()
        proc.poll.return_value = None
        app._ui_proc = proc
        server = app._ui_server
        app._close_winui()
        self.assertEqual(server.sent, [{"type": "quit"}])
        self.assertEqual(server.flushed, 1)
        self.assertEqual(server.closed, 1)
        proc.terminate.assert_called_once()  # it was still running after the wait
        self.assertTrue(app._quitting)

    def test_a_window_that_exits_on_its_own_is_not_terminated(self):
        app, _main = self._app()
        proc = Mock()
        proc.poll.return_value = 0
        app._ui_proc = proc
        app._close_winui()
        proc.terminate.assert_not_called()

    def test_a_slow_window_is_waited_for_only_briefly(self):
        app, _main = self._app()
        proc = Mock()
        proc.wait.side_effect = subprocess.TimeoutExpired("DictateUI", 1.5)
        proc.poll.return_value = None
        app._ui_proc = proc
        app._close_winui()
        proc.terminate.assert_called_once()

    def test_an_update_marks_the_app_as_quitting_before_anything_else_so_the_program_is_not_restarted(self):
        app, _main = self._app()
        app.bar = Mock()
        app.settings_window = None
        seen = []
        app._launch_update_splash = lambda pid: seen.append(("splash", app._quitting))
        app._quit = lambda: seen.append(("quit", app._quitting))
        app._on_update_installing("9.9.9", 123)
        self.assertEqual(seen, [("splash", True), ("quit", True)])
        # and a window the installer closed at that moment is not started again
        app._ui_proc = Mock(poll=Mock(return_value=1))
        app._shell.server.has_client = False
        with patch.object(_main.ui_bridge, "launch_ui") as launch:
            app._ensure_ui()
        launch.assert_not_called()

    def test_the_snapshot_joins_tray_and_bar(self):
        app, _main = self._app()
        app.bar = ui_shell.RemoteBar(app.settings, app._shell)
        app._build_remote_tray()
        snap = app._shell_snapshot()
        self.assertEqual(set(snap), {"tray", "bar"})
        self.assertEqual(snap["bar"], {"dark": True})


if __name__ == "__main__":
    unittest.main()

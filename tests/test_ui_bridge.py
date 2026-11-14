"""The link between the engine and the WinUI Settings window (ui_bridge.py).

The message logic runs against fakes. One test carries real lines over a real
QLocalServer pipe, the transport the Settings window uses.
"""

import json
import os
import time
import unittest
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config
import ui_bridge


class FakeEngine:
    def __init__(self):
        self.last_status = ("ready", "Small · CPU ready", None)
        self.gpu_status = (False, None)
        self.gpu_error = ""
        self.gpu_started = 0

    def start_gpu_download(self):
        self.gpu_started += 1
        return True


class FakeUpdater:
    def __init__(self):
        self.last_status = ("idle", "", None)
        self.last_checked_at = None
        self.calls = []

    def check_now(self, *, silent=True):
        self.calls.append(("check", silent))
        return True

    def start_update(self):
        self.calls.append(("start",))
        return True

    def restart_to_install(self):
        self.calls.append(("restart",))
        return True


class Rig:
    """A UiHost made of fakes, plus a record of what the bridge asked of it."""

    def __init__(self, *, updater=True, accept_startup=True):
        self.settings = config.Settings(onboarding_complete=True)
        self.engine = FakeEngine()
        self.updater = FakeUpdater() if updater else None
        self.saved = []
        self.capture = []
        self.widths = []
        self.margins = []
        self.cuda_calls = 0
        self.hardware_calls = 0
        self.accept_startup = accept_startup
        self.needs_gpu = False
        self.tray_actions = []
        self.bar_clicks = 0
        self.host = ui_bridge.UiHost(
            version="9.9.9",
            notes="### Changed\n- A thing.",
            model_dir=lambda: r"C:\models",
            get_settings=lambda: self.settings,
            save_settings=self._save,
            engine=self.engine,
            updater=self.updater,
            cuda_available=self._cuda,
            gpu_needs_download=lambda: self.needs_gpu,
            hardware=self._hardware,
            input_devices=lambda: [SimpleNamespace(key="mme|Yeti", label="Yeti", index=1)],
            set_capture_active=self.capture.append,
            preview_width=self.widths.append,
            preview_margin=self.margins.append,
            shell_snapshot=lambda: {"tray": {"tooltip": "tip"}, "bar": {"dark": True}},
            tray_action=self.tray_actions.append,
            bar_clicked=lambda: setattr(self, "bar_clicks", self.bar_clicks + 1),
        )
        self.bridge = ui_bridge.UiBridge(self.host)

    def _cuda(self):
        self.cuda_calls += 1
        return True

    def _hardware(self):
        self.hardware_calls += 1
        return SimpleNamespace(cpu_name="Ryzen", gpu_name="RTX", vram_mb=12282)

    def _save(self, new):
        self.saved.append(new)
        if not self.accept_startup and new.start_with_windows != self.settings.start_with_windows:
            new = replace(new, start_with_windows=self.settings.start_with_windows)
        self.settings = new

    def send(self, msg):
        return self.bridge.handle_line(json.dumps(msg))


class SettingsFromDictTests(unittest.TestCase):
    def test_unknown_keys_are_ignored_and_values_are_clamped(self):
        current = config.Settings(onboarding_complete=True)
        new = ui_bridge.settings_from_dict(
            {"bar_width": 9999, "bar_margin": -5, "theme_mode": "neon", "nonsense": 1}, current
        )
        self.assertEqual(new.bar_width, 280)
        self.assertEqual(new.bar_margin, 0)
        self.assertEqual(new.theme_mode, "system")
        self.assertFalse(hasattr(new, "nonsense"))

    def test_missing_fields_keep_the_engines_values(self):
        current = config.Settings(ptt_key="f10", model_size="medium.en")
        new = ui_bridge.settings_from_dict({"sound_cues": False}, current)
        self.assertEqual(new.ptt_key, "f10")
        self.assertEqual(new.model_size, "medium.en")
        self.assertFalse(new.sound_cues)

    def test_the_window_cannot_change_the_first_run_flag(self):
        current = config.Settings(onboarding_complete=False)
        new = ui_bridge.settings_from_dict({"onboarding_complete": True}, current)
        self.assertFalse(new.onboarding_complete)

    def test_wrong_types_fall_back_instead_of_raising(self):
        current = config.Settings()
        new = ui_bridge.settings_from_dict({"bar_width": "wide", "sleep_after_minutes": None, "vocabulary": 7}, current)
        self.assertEqual(new.bar_width, config.Settings().bar_width)
        self.assertEqual(new.vocabulary, [])

    def test_not_an_object_is_refused(self):
        self.assertIsNone(ui_bridge.settings_from_dict([1, 2], config.Settings()))
        self.assertIsNone(ui_bridge.settings_from_dict("x", config.Settings()))


class StatusTests(unittest.TestCase):
    def test_hello_carries_everything_the_window_needs(self):
        rig = Rig()
        hello = rig.bridge.hello()
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["version"], "9.9.9")
        self.assertEqual(hello["model_dir"], r"C:\models")
        self.assertEqual(hello["settings"], asdict(rig.settings))
        self.assertEqual(hello["status"]["hardware"], {"cpu": "Ryzen", "gpu": "RTX", "vram_mb": 12282})

    def test_status_maps_engine_gpu_and_updater(self):
        rig = Rig()
        rig.engine.last_status = ("loading", "Downloading Small", 0.4)
        rig.engine.gpu_status = (True, 0.25)
        rig.engine.gpu_error = "nope"
        rig.updater.last_status = ("downloading", "Dictate-Setup.exe", 0.5)
        rig.updater.last_checked_at = 1730000000.0
        rig.needs_gpu = True
        status = rig.bridge.status()
        self.assertEqual(status["model"], {"state": "loading", "detail": "Downloading Small", "progress": 0.4})
        self.assertEqual(
            status["gpu"],
            {"has_cuda": True, "ready": False, "downloading": True, "progress": 0.25, "error": "nope"},
        )
        self.assertEqual(
            status["update"],
            {"state": "downloading", "detail": "Dictate-Setup.exe", "progress": 0.5, "checked_at": 1730000000.0},
        )

    def test_gpu_is_ready_only_when_cuda_exists_and_nothing_is_missing(self):
        rig = Rig()
        self.assertTrue(rig.bridge.status()["gpu"]["ready"])
        rig.needs_gpu = True
        self.assertFalse(rig.bridge.status()["gpu"]["ready"])

    def test_hardware_and_cuda_are_probed_once(self):
        rig = Rig()
        for _ in range(5):
            rig.bridge.status()
        self.assertEqual(rig.cuda_calls, 1)
        self.assertEqual(rig.hardware_calls, 1)

    def test_a_failing_probe_does_not_break_status(self):
        rig = Rig()
        rig.host.hardware = lambda: (_ for _ in ()).throw(OSError("no nvidia-smi"))
        rig.host.cuda_available = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        status = rig.bridge.status()
        self.assertEqual(status["hardware"], {"cpu": "", "gpu": "", "vram_mb": 0})
        self.assertFalse(status["gpu"]["has_cuda"])

    def test_no_updater_reads_as_idle(self):
        rig = Rig(updater=False)
        self.assertEqual(rig.bridge.status()["update"]["state"], "idle")

    def test_status_is_json_safe(self):
        rig = Rig()
        json.dumps(rig.bridge.hello())


class PollTests(unittest.TestCase):
    def test_nothing_is_sent_when_nothing_changed(self):
        rig = Rig()
        rig.bridge.hello()
        self.assertEqual(rig.bridge.poll(), [])
        self.assertEqual(rig.bridge.poll(), [])

    def test_a_status_change_is_sent_once(self):
        rig = Rig()
        rig.bridge.hello()
        rig.updater.last_status = ("available", "Version 9.9.9 is available", None)
        out = rig.bridge.poll()
        self.assertEqual([m["type"] for m in out], ["status"])
        self.assertEqual(out[0]["update"]["state"], "available")
        self.assertEqual(rig.bridge.poll(), [])

    def test_a_settings_change_from_the_engine_is_sent(self):
        rig = Rig()
        rig.bridge.hello()
        rig.settings = replace(rig.settings, ptt_key="f10")
        out = rig.bridge.poll()
        self.assertEqual([m["type"] for m in out], ["settings"])
        self.assertEqual(out[0]["settings"]["ptt_key"], "f10")

    def test_reset_makes_a_new_window_see_everything_again(self):
        rig = Rig()
        rig.bridge.hello()
        rig.bridge.reset()
        self.assertEqual({m["type"] for m in rig.bridge.poll()}, {"status", "settings"})


class HandleTests(unittest.TestCase):
    def test_bad_lines_are_ignored(self):
        rig = Rig()
        for line in (b"", b"not json", b"[1,2]", b"\xff\xfe", b'"text"', b'{"type":"nonsense"}', b"{}"):
            self.assertEqual(rig.bridge.handle_line(line), [])
        self.assertEqual(rig.saved, [])

    def test_a_failing_handler_does_not_escape(self):
        rig = Rig()
        rig.host.preview_width = lambda w: (_ for _ in ()).throw(RuntimeError("bar is gone"))
        self.assertEqual(rig.send({"type": "preview", "width": 200}), [])

    def test_save_settings_applies_clamped_values(self):
        rig = Rig()
        out = rig.send({"type": "save_settings", "settings": {"bar_width": 500, "ptt_key": "f10"}})
        self.assertEqual(out, [])  # accepted as sent: no echo
        self.assertEqual(rig.saved[-1].bar_width, 280)
        self.assertEqual(rig.settings.ptt_key, "f10")

    def test_an_accepted_save_is_not_echoed_back_by_the_next_poll(self):
        rig = Rig()
        rig.bridge.hello()
        rig.send({"type": "save_settings", "settings": {"sound_cues": False}})
        self.assertEqual([m for m in rig.bridge.poll() if m["type"] == "settings"], [])

    def test_a_refused_part_of_a_save_is_reported_back(self):
        rig = Rig(accept_startup=False)
        out = rig.send({"type": "save_settings", "settings": {"start_with_windows": True, "sound_cues": False}})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "settings")
        self.assertFalse(out[0]["settings"]["start_with_windows"])
        self.assertFalse(out[0]["settings"]["sound_cues"])

    def test_a_non_object_settings_value_is_refused(self):
        rig = Rig()
        self.assertEqual(rig.send({"type": "save_settings", "settings": "x"}), [])
        self.assertEqual(rig.saved, [])

    def test_update_commands_reach_the_updater(self):
        rig = Rig()
        rig.send({"type": "check_updates"})
        rig.send({"type": "start_update"})
        rig.send({"type": "restart_to_install"})
        self.assertEqual(rig.updater.calls, [("check", False), ("start",), ("restart",)])

    def test_update_commands_without_an_updater_do_nothing(self):
        rig = Rig(updater=False)
        for kind in ("check_updates", "start_update", "restart_to_install"):
            self.assertEqual(rig.send({"type": kind}), [])

    def test_gpu_download_starts(self):
        rig = Rig()
        rig.send({"type": "start_gpu_download"})
        self.assertEqual(rig.engine.gpu_started, 1)

    def test_capture_toggles_the_hotkey_listener(self):
        rig = Rig()
        rig.send({"type": "capture", "active": True})
        rig.send({"type": "capture", "active": False})
        self.assertEqual(rig.capture, [True, False])

    def test_previews_are_clamped_and_typed(self):
        rig = Rig()
        rig.send({"type": "preview", "width": 10})
        rig.send({"type": "preview", "margin": 9999})
        rig.send({"type": "preview", "width": "wide", "margin": 1.5})
        self.assertEqual(rig.widths, [180])
        self.assertEqual(rig.margins, [400])

    def test_hello_carries_the_tray_and_bar_state(self):
        rig = Rig()
        self.assertEqual(rig.bridge.hello()["shell"], {"tray": {"tooltip": "tip"}, "bar": {"dark": True}})

    def test_tray_choices_and_bar_clicks_reach_the_app(self):
        rig = Rig()
        rig.send({"type": "tray_action", "id": "undo"})
        rig.send({"type": "tray_action", "id": 7})  # not a string: ignored
        rig.send({"type": "bar_clicked"})
        self.assertEqual(rig.tray_actions, ["undo"])
        self.assertEqual(rig.bar_clicks, 1)

    def test_devices_are_listed_by_key_and_label(self):
        rig = Rig()
        out = rig.send({"type": "get_devices"})
        self.assertEqual(out, [{"type": "devices", "devices": [{"key": "mme|Yeti", "label": "Yeti"}]}])


class LineBufferTests(unittest.TestCase):
    def test_partial_and_multiple_lines(self):
        buf = ui_bridge.LineBuffer()
        self.assertEqual(buf.feed(b'{"a":'), [])
        self.assertEqual(buf.feed(b'1}\n{"b":2}\n{"c"'), [b'{"a":1}', b'{"b":2}'])
        self.assertEqual(buf.feed(b":3}\n"), [b'{"c":3}'])

    def test_a_line_that_never_ends_is_refused(self):
        buf = ui_bridge.LineBuffer(limit=10)
        with self.assertRaises(ValueError):
            buf.feed(b"x" * 11)
        self.assertEqual(buf.feed(b"ok\n"), [b"ok"])

    def test_encode_is_one_utf8_line(self):
        data = ui_bridge.encode({"type": "x", "text": "caf\u00e9"})
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(data.count(b"\n"), 1)
        self.assertEqual(json.loads(data)["text"], "caf\u00e9")


class PipeTests(unittest.TestCase):
    """Real lines over a real pipe, with the Qt event loop pumped by hand."""

    def setUp(self):
        from PySide6.QtCore import QCoreApplication

        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.rig = Rig()
        self.server = ui_bridge.make_pipe_server(self.rig.bridge)
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.close)

    def pump(self, until, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if until():
                return True
            time.sleep(0.005)
        return False

    def connect(self):
        from PySide6.QtNetwork import QLocalSocket

        sock = QLocalSocket()
        sock.connectToServer(self.server.name)
        self.assertTrue(sock.waitForConnected(2000))
        return sock

    def read_messages(self, sock, count):
        got, buf = [], bytearray()

        def ready():
            buf.extend(bytes(sock.readAll()))
            while b"\n" in buf:
                end = buf.index(b"\n")
                got.append(json.loads(bytes(buf[:end])))
                del buf[: end + 1]
            return len(got) >= count

        self.assertTrue(self.pump(ready), f"only got {got}")
        return got

    def test_hello_arrives_first_then_commands_round_trip(self):
        sock = self.connect()
        self.addCleanup(sock.abort)
        hello = self.read_messages(sock, 1)[0]
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["version"], "9.9.9")

        sock.write(ui_bridge.encode({"type": "save_settings", "settings": {"sound_cues": False}}))
        sock.write(ui_bridge.encode({"type": "get_devices"}))
        sock.flush()
        devices = self.read_messages(sock, 1)[0]
        self.assertEqual(devices["type"], "devices")
        self.assertFalse(self.rig.settings.sound_cues)

    def test_a_status_change_is_pushed_without_asking(self):
        sock = self.connect()
        self.addCleanup(sock.abort)
        self.read_messages(sock, 1)
        self.rig.updater.last_status = ("available", "Version 9.9.9 is available", None)
        pushed = self.read_messages(sock, 1)[0]
        self.assertEqual(pushed["type"], "status")
        self.assertEqual(pushed["update"]["state"], "available")

    def test_losing_the_window_is_noticed_and_a_new_one_is_welcome(self):
        first = self.connect()
        self.read_messages(first, 1)
        self.assertTrue(self.server.has_client)
        first.abort()
        self.assertTrue(self.pump(lambda: not self.server.has_client))
        second = self.connect()
        self.addCleanup(second.abort)
        self.assertEqual(self.read_messages(second, 1)[0]["type"], "hello")

    def test_a_newer_window_replaces_the_older_one(self):
        first = self.connect()
        self.addCleanup(first.abort)
        self.read_messages(first, 1)
        second = self.connect()
        self.addCleanup(second.abort)
        self.assertEqual(self.read_messages(second, 1)[0]["type"], "hello")
        from PySide6.QtNetwork import QLocalSocket

        connected = QLocalSocket.LocalSocketState.ConnectedState
        self.assertTrue(self.pump(lambda: first.state() != connected, 2.0))

    def test_an_endless_line_drops_the_window(self):
        sock = self.connect()
        self.addCleanup(sock.abort)
        self.read_messages(sock, 1)
        sock.write(b"x" * (ui_bridge.MAX_LINE_BYTES + 10))
        sock.flush()
        self.assertTrue(self.pump(lambda: not self.server.has_client))


class WinuiLaunchTests(unittest.TestCase):
    """main.App's side: when the WinUI window opens, when it falls back, how a save from it is applied."""

    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app.settings = config.Settings(onboarding_complete=True)
        app.engine = Mock()
        app.updater = Mock()
        app.hotkeys = Mock()
        app.bar = Mock()
        app.qt = Mock()
        app._whats_new_notes = "notes"
        app._ui_server = None
        app._ui_proc = None
        app._winui_failed = False
        app._apply_settings = Mock()
        return app, main

    @staticmethod
    def _server(start=True, has_client=False):
        server = Mock()
        server.start.return_value = start
        server.name = "dictate-ui-test"
        server.has_client = has_client
        return server

    def test_a_save_from_the_window_is_applied_like_the_built_in_windows(self):
        app, main = self._app()
        new = replace(app.settings, sound_cues=False)
        with patch.object(main.config, "save") as save, patch.object(main.startup, "set_enabled") as startup:
            app._save_settings_from_ui(new)
        save.assert_called_once_with(new)
        app._apply_settings.assert_called_once_with(new)
        app.engine.preload.assert_not_called()
        startup.assert_not_called()

    def test_an_unchanged_save_does_nothing(self):
        app, main = self._app()
        with patch.object(main.config, "save") as save:
            app._save_settings_from_ui(replace(app.settings))
        save.assert_not_called()
        app._apply_settings.assert_not_called()

    def test_changing_the_model_or_device_reloads_the_engine(self):
        app, main = self._app()
        with patch.object(main.config, "save"):
            app._save_settings_from_ui(replace(app.settings, model_size="medium.en"))
        app.engine.preload.assert_called_once()

    def test_windows_startup_toggles_through_the_registry_helper(self):
        app, main = self._app()
        with patch.object(main.config, "save"), patch.object(main.startup, "set_enabled") as startup:
            app._save_settings_from_ui(replace(app.settings, start_with_windows=True))
        startup.assert_called_once_with(True)

    def test_a_refused_startup_change_keeps_the_old_value_but_saves_the_rest(self):
        app, main = self._app()
        new = replace(app.settings, start_with_windows=True, sound_cues=False)
        with patch.object(main.config, "save") as save, patch.object(
            main.startup, "set_enabled", side_effect=OSError("denied")
        ):
            app._save_settings_from_ui(new)
        saved = save.call_args.args[0]
        self.assertFalse(saved.start_with_windows)
        self.assertFalse(saved.sound_cues)

    def test_a_refused_startup_change_alone_saves_nothing(self):
        app, main = self._app()
        with patch.object(main.config, "save") as save, patch.object(
            main.startup, "set_enabled", side_effect=OSError("denied")
        ):
            app._save_settings_from_ui(replace(app.settings, start_with_windows=True))
        save.assert_not_called()

    def test_quitting_before_the_window_was_ever_opened_is_fine(self):
        import main

        bare = main.App.__new__(main.App)  # no attributes at all, like a test's partial app
        bare._close_winui()


class FindUiTests(unittest.TestCase):
    def test_interface_setting_defaults_and_rejects_invalid_values(self):
        self.assertEqual(config.Settings().interface, "new")
        self.assertEqual(config.Settings(interface="old").clamped().interface, "old")
        self.assertEqual(config.Settings(interface="invalid").clamped().interface, "new")

    def test_restart_starts_successor_without_interface_overrides(self):
        import main
        import sys
        from unittest.mock import Mock, patch
        app = main.App.__new__(main.App)
        app._quit = Mock()
        with patch.object(sys, "argv", ["main.py", "--winui"]), \
             patch.dict(os.environ, {"DICTATE_WINUI": "1"}), \
             patch.object(main.subprocess, "Popen") as launch:
            app._restart_for_interface()
        command = launch.call_args.args[0]
        self.assertNotIn("--winui", command)
        self.assertIn("--restart-parent", command)
        self.assertNotIn("DICTATE_WINUI", launch.call_args.kwargs["env"])
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        app._quit.assert_called_once()

    def test_the_override_wins_and_a_missing_file_is_none(self):
        old = os.environ.get("DICTATE_UI_EXE")
        try:
            os.environ["DICTATE_UI_EXE"] = r"C:\definitely\not\here\DictateUI.exe"
            found = ui_bridge.find_ui_exe()
            self.assertTrue(found is None or found.name == "DictateUI.exe")
            self.assertNotEqual(str(found), os.environ["DICTATE_UI_EXE"])
        finally:
            if old is None:
                os.environ.pop("DICTATE_UI_EXE", None)
            else:
                os.environ["DICTATE_UI_EXE"] = old

    def test_the_window_defaults_to_new_and_allows_overrides(self):
        old = os.environ.pop("DICTATE_WINUI", None)
        try:
            self.assertTrue(ui_bridge.winui_requested())
            self.assertFalse(ui_bridge.winui_requested([], interface="old"))
            os.environ["DICTATE_WINUI"] = "1"
            self.assertTrue(ui_bridge.winui_requested())
            os.environ["DICTATE_WINUI"] = "0"
            self.assertFalse(ui_bridge.winui_requested())
        finally:
            os.environ.pop("DICTATE_WINUI", None)
            if old is not None:
                os.environ["DICTATE_WINUI"] = old


if __name__ == "__main__":
    unittest.main()

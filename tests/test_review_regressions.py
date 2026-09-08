"""Behavior regressions from the read-only Dictate review.

No live recording, keyboard injection, clipboard writes or network downloads.
"""
from __future__ import annotations

import os
import sys
import threading
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import audio
import config
import engine
import hotkeys
import inject
import updater


class ReviewRegressions(unittest.TestCase):
    def test_locked_recording_ignores_other_key_and_mouse_releases(self):
        events = []
        listener = hotkeys.Hotkeys(
            config.Settings(), lambda: events.append("start"),
            lambda _: events.append("end"), lambda: None,
            lambda: events.append("lock"), lambda: events.append("cancel"),
        )
        with patch.object(hotkeys.time, "monotonic", return_value=10):
            listener._press("f9")
            listener._release("f9")
        with patch.object(hotkeys.time, "monotonic", return_value=11):
            for key in ("a", "mouse1", "shift"):
                listener._press(key)
                listener._release(key)
            self.assertEqual(events, ["start", "lock"])
            self.assertTrue(listener.is_locked())
            listener._press("f9")
            listener._release("f9")
        self.assertEqual(events, ["start", "lock", "end"])
        self.assertFalse(listener.is_locked())

    def test_locked_recording_still_cancels_with_escape(self):
        cancel = Mock()
        listener = hotkeys.Hotkeys(config.Settings(), Mock(), Mock(), Mock(), on_talk_cancel=cancel)
        with patch.object(hotkeys.time, "monotonic", return_value=10):
            listener._press("f9")
            listener._release("f9")
            listener._press("esc")
        cancel.assert_called_once()
        self.assertFalse(listener.is_locked())

    def test_microphone_prefers_compatible_route_and_keeps_old_binding(self):
        devices = [
            {"name": "Yeti Classic", "hostapi": 0, "max_input_channels": 2},
            {"name": "Yeti Classic", "hostapi": 1, "max_input_channels": 2},
        ]
        apis = [{"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}]
        def check(**kwargs):
            if kwargs["device"] == 0:
                raise RuntimeError("Invalid sample rate")
        with patch.object(audio.sd, "query_devices", return_value=devices), patch.object(audio.sd, "query_hostapis", return_value=apis), patch.object(audio.sd, "check_input_settings", side_effect=check), patch.object(audio.sd, "InputStream") as stream:
            listed = audio.input_devices()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].index, 1)
            self.assertEqual(audio.MicCapture("Windows WASAPI|Yeti Classic|0")._device_index(), 1)
            stream.assert_not_called()

    def test_microphone_keeps_wasapi_when_format_is_supported(self):
        devices = [{"name": "Mic", "hostapi": n, "max_input_channels": 1} for n in (0, 1)]
        with patch.object(audio.sd, "query_devices", return_value=devices), patch.object(audio.sd, "query_hostapis", return_value=[{"name": "MME"}, {"name": "Windows WASAPI"}]), patch.object(audio.sd, "check_input_settings"):
            self.assertEqual(audio.input_devices()[0].index, 1)

    def test_paste_preserves_a_new_user_copy_during_settle(self):
        clipboard = {"text": "before", "sequence": 10}
        def copy(text):
            clipboard["text"] = text
            clipboard["sequence"] += 1
        def sleep(seconds):
            if seconds == inject.PASTE_SETTLE:
                copy("new user copy")
        controller = Mock()
        controller.pressed.return_value = nullcontext()
        with patch.object(inject, "clipboard_is_borrowable", return_value=True), patch.object(inject, "_clipboard_sequence", side_effect=lambda: clipboard["sequence"]), patch.object(inject.pyperclip, "paste", side_effect=lambda: clipboard["text"]), patch.object(inject.pyperclip, "copy", side_effect=copy), patch.object(inject.time, "sleep", side_effect=sleep), patch.object(inject, "_controller", controller):
            inject.send("dictation ")
        self.assertEqual(clipboard["text"], "new user copy")

    def test_rich_clipboard_is_not_borrowed_even_when_it_includes_text(self):
        win = Mock()
        win.OpenClipboard.return_value = True
        win.EnumClipboardFormats.side_effect = [13, 49300, 0]
        with patch.object(inject, "_user32", return_value=win), patch.object(inject.pyperclip, "copy") as copy:
            with self.assertRaises(inject.ClipboardUnavailable):
                inject.send("dictation")
        copy.assert_not_called()
        win.CloseClipboard.assert_called_once()

    def test_busy_clipboard_fails_closed(self):
        win = Mock()
        win.OpenClipboard.return_value = False
        with patch.object(inject, "_user32", return_value=win):
            self.assertFalse(inject.clipboard_is_borrowable())
        win.CloseClipboard.assert_not_called()

    def test_quit_does_not_wait_for_model_download_lock(self):
        subject = engine.Engine(config.Settings())
        acquired, release, done = threading.Event(), threading.Event(), threading.Event()
        def loader():
            with subject._lock:
                acquired.set()
                release.wait(3)
        worker = threading.Thread(target=loader)
        quitter = threading.Thread(target=lambda: (subject.shutdown(), done.set()))
        worker.start()
        self.assertTrue(acquired.wait(1))
        try:
            quitter.start()
            self.assertTrue(done.wait(.5), "Quit blocked behind the download")
        finally:
            release.set()
            worker.join(2)
            quitter.join(2)

    def test_gpu_failure_reports_error_instead_of_ready(self):
        import main
        app = main.App.__new__(main.App)
        app.settings_window = None
        app._dictation_active = False
        app._ptt_preload_pending = False
        app._gpu_download_showing = True
        app.bar = Mock()
        subject = engine.Engine(config.Settings(), on_gpu_status=app._on_gpu_status)
        app.engine = subject
        try:
            with patch.object(engine.gpu_runtime, "download_and_install", return_value=False), patch.object(subject, "_invalidate_for_gpu_ready") as invalidate:
                subject._run_gpu_download()
            self.assertIn("failed", subject.gpu_error)
            self.assertEqual(app.bar.set_state.call_args.args[0], "error")
            invalidate.assert_not_called()
        finally:
            subject.shutdown()

    def test_gpu_success_clears_previous_error(self):
        subject = engine.Engine(config.Settings())
        subject._gpu_error = "Earlier failure"
        try:
            with patch.object(engine.gpu_runtime, "download_and_install", return_value=True), patch.object(subject, "_invalidate_for_gpu_ready") as invalidate:
                subject._run_gpu_download()
            self.assertEqual(subject.gpu_error, "")
            invalidate.assert_called_once()
        finally:
            subject.shutdown()

    def test_immediate_gpu_failure_is_announced_after_start(self):
        states = []
        subject = engine.Engine(config.Settings(), on_gpu_status=lambda downloading, _: states.append(downloading))
        try:
            with patch.object(engine.gpu_runtime, "download_and_install", return_value=False):
                subject._ensure_gpu_download_started()
                subject._gpu_download_thread.join(2)
            self.assertEqual(states, [True, False])
        finally:
            subject.shutdown()

    def test_gpu_fallback_reuses_cpu_and_explicit_unload_allows_retry(self):
        attempts = []
        def factory(*args, **kwargs):
            attempts.append(kwargs["device"])
            if kwargs["device"] == "cuda":
                raise RuntimeError("Not enough GPU memory")
            return Mock()
        subject = engine.Engine(config.Settings(device="cuda"))
        try:
            with patch.object(engine, "resolve_device", return_value="cuda"), patch.object(engine, "_register_cuda_dlls"), patch.object(subject, "_download_with_progress", return_value=None), patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=factory)}):
                first = subject.ensure_loaded()
                self.assertIs(subject.ensure_loaded(), first)
                self.assertEqual(attempts, ["cuda", "cpu"])
                subject.unload()
                subject.ensure_loaded()
                self.assertEqual(attempts, ["cuda", "cpu", "cuda", "cpu"])
        finally:
            subject.shutdown()

    def test_offline_manual_check_reports_error_and_keeps_last_success_time(self):
        with patch.object(updater.Updater, "_loop"), patch.object(updater, "_load_pending_update", return_value=None), patch.object(updater, "_fetch_latest_release", return_value=None):
            current, error = Mock(), Mock()
            subject = updater.Updater(enabled=False, on_up_to_date=current, on_error=error)
            subject._last_checked_at = 123
            subject._busy.acquire()
            try:
                subject._check(False)
                self.assertEqual(subject.last_status[0], updater.ERROR)
                self.assertEqual(subject.last_checked_at, 123)
                current.assert_not_called()
                error.assert_called_once()
            finally:
                subject.shutdown()

    def test_offline_background_check_stays_quiet(self):
        with patch.object(updater.Updater, "_loop"), patch.object(updater, "_load_pending_update", return_value=None), patch.object(updater, "_fetch_latest_release", return_value=None):
            current, error = Mock(), Mock()
            subject = updater.Updater(enabled=False, on_up_to_date=current, on_error=error)
            subject._busy.acquire()
            try:
                subject._check(True)
                current.assert_not_called()
                error.assert_not_called()
                self.assertIsNone(subject.last_checked_at)
            finally:
                subject.shutdown()


class BarMotionRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_rapid_state_change_uses_current_shape_without_waiting(self):
        import bar
        b = bar.Bar(config.Settings())
        self.addCleanup(b.close)
        b.set_state("listening")
        b._drawn[:] = 0.35
        b.set_state("transcribing")
        self.assertEqual(b._state, "transcribing")
        self.assertIsNone(b._pending)
        self.assertTrue((b._morph_from == b._drawn).all())
        b.set_state("done")
        self.assertEqual(b._width_target, 145)
        b.set_state("listening")
        self.assertEqual(b._state, "listening")
        self.assertEqual(b._width_target, b._settings.bar_width)

    def test_hidden_receipt_cannot_leak_into_next_recording(self):
        import bar
        b = bar.Bar(config.Settings())
        self.addCleanup(b.close)
        b.set_state("done")
        b._content = {"wave": 0.0, "done": 1.0, "error": 0.0}
        b._reveal_anim.stop()
        b._reveal = 0.0
        b._finish_hide()
        self.assertFalse(b._timer.isActive())
        self.assertFalse(b._width_timer.isActive())
        self.assertEqual(b._content["done"], 0.0)
        self.assertEqual(b._pill_width, b._settings.bar_width)

    def test_reduced_motion_has_static_working_signal_and_direct_resize(self):
        import bar
        with patch.object(bar, "_animations_enabled", return_value=False):
            b = bar.Bar(config.Settings())
            self.addCleanup(b.close)
            b.set_state("transcribing")
            clock = b._clock
            b._last_tick -= 0.016
            b._tick()
            self.assertEqual(b._clock, clock)
            self.assertGreater(float(b._drawn.max()), 0)
            b.set_state("done")
            self.assertEqual(b._pill_width, 145)
            self.assertFalse(b._width_timer.isActive())

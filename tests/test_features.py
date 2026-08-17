from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import audio
import config
import gpu_runtime
import hotkeys
import inject
import startup
import theme
import updater

# Every test in this module is redirected away from the real settings file.
#
# This is not hygiene, it is a bug fix. SettingsWindow saves on a Qt timer, and
# a window built inside a test outlives the `patch.object(config, "save")` that
# wrapped its construction -- so a timer firing afterwards reaches the *real*
# config.save and rewrites the user's actual settings. A hung test process kept
# doing exactly that, silently reverting a hand-corrected talk-key binding three
# times before the cause was found. Redirecting at module scope means even a
# stray timer in a wedged process can only ever touch a temporary file.
_SANDBOX = None
_REDIRECTS: list = []


def setUpModule() -> None:
    global _SANDBOX
    _SANDBOX = tempfile.TemporaryDirectory()
    folder = Path(_SANDBOX.name)
    _REDIRECTS.extend(
        [
            patch.object(config, "CONFIG_DIR", folder),
            patch.object(config, "CONFIG_PATH", folder / "settings.json"),
        ]
    )
    for redirect in _REDIRECTS:
        redirect.start()


def tearDownModule() -> None:
    for redirect in _REDIRECTS:
        redirect.stop()
    _REDIRECTS.clear()
    if _SANDBOX is not None:
        _SANDBOX.cleanup()


class _Controller:
    def __init__(self):
        self.pressed_keys: list[str] = []
        self.released_keys: list[str] = []

    def pressed(self, _key):
        return nullcontext()

    def press(self, key):
        self.pressed_keys.append(key)

    def release(self, key):
        self.released_keys.append(key)

    def type(self, _text):
        raise AssertionError("paste mode should not type characters")


class ClipboardTests(unittest.TestCase):
    def test_empty_clipboard_is_restored_after_paste(self):
        copied: list[str] = []
        controller = _Controller()
        with (
            patch.object(inject.pyperclip, "paste", return_value=""),
            patch.object(inject.pyperclip, "copy", side_effect=copied.append),
            patch.object(inject.time, "sleep"),
            patch.object(inject, "_controller", controller),
        ):
            inject.send("hello", "paste")

        self.assertEqual(copied, ["hello", ""])
        self.assertEqual(controller.pressed_keys, ["v"])
        self.assertEqual(controller.released_keys, ["v"])

    def test_failed_clipboard_read_does_not_overwrite_with_fake_backup(self):
        copied: list[str] = []
        with (
            patch.object(inject.pyperclip, "paste", side_effect=RuntimeError("busy")),
            patch.object(inject.pyperclip, "copy", side_effect=copied.append),
            patch.object(inject.time, "sleep"),
            patch.object(inject, "_controller", _Controller()),
        ):
            inject.send("hello", "paste")

        self.assertEqual(copied, ["hello"])


class MicrophoneTests(unittest.TestCase):
    def test_device_key_survives_numeric_index_changes(self):
        devices = [
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
            {"name": "Desk Mic", "hostapi": 0, "max_input_channels": 2},
        ]
        host_apis = [{"name": "Windows WASAPI"}]
        with (
            patch.object(audio.sd, "query_devices", return_value=devices),
            patch.object(audio.sd, "query_hostapis", return_value=host_apis),
        ):
            listed = audio.input_devices()
            capture = audio.MicCapture(listed[0].key)
            self.assertEqual(capture._device_index(), 1)

        self.assertEqual(listed[0].label, "Desk Mic")
        self.assertIn("Windows WASAPI", listed[0].key)

    def test_missing_selected_microphone_fails_clearly(self):
        with patch.object(audio, "input_devices", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "not available"):
                audio.MicCapture("missing")._device_index()

    def test_rms_level_of_silence_is_below_threshold(self):
        silent = np.zeros(4000, dtype=np.float32)
        self.assertLess(audio.rms_level(silent), audio.SILENCE_RMS_THRESHOLD)

    def test_rms_level_of_speech_is_above_threshold(self):
        speech = np.linspace(-0.4, 0.4, 4000).astype(np.float32)
        self.assertGreater(audio.rms_level(speech), audio.SILENCE_RMS_THRESHOLD)

    def test_rms_level_of_empty_clip_is_zero(self):
        self.assertEqual(audio.rms_level(np.zeros(0, dtype=np.float32)), 0.0)


class EngineProgressTests(unittest.TestCase):
    def test_repo_id_resolves_known_model_size(self):
        import engine

        self.assertEqual(
            engine._resolve_repo_id("small.en"), "Systran/faster-whisper-small.en"
        )

    def test_repo_id_passes_through_explicit_ids(self):
        import engine

        self.assertEqual(
            engine._resolve_repo_id("someorg/some-model"), "someorg/some-model"
        )

    def test_repo_id_returns_none_for_unknown_size(self):
        import engine

        self.assertIsNone(engine._resolve_repo_id("not-a-real-size"))

    def test_hub_progress_reports_byte_updates_only(self):
        import engine

        calls = []
        cls = engine._make_progress_reporter(lambda n, total: calls.append((n, total)))

        bytes_bar = cls(total=100, unit="B")
        bytes_bar.update(40)
        files_bar = cls(total=5, unit="")
        files_bar.update(1)

        self.assertEqual(calls, [(40, 100)])

    def test_hub_progress_absorbs_unknown_tqdm_calls(self):
        import engine

        cls = engine._make_progress_reporter(lambda n, total: None)
        bar = cls(total=10, unit="B")

        with bar as ctx:
            self.assertIs(ctx, bar)
        bar.refresh()
        bar.close()
        self.assertEqual(bar.format_dict, {})
        bar.set_postfix_str("whatever", refresh=False)  # must not raise

    def test_download_progress_is_throttled_but_always_reports_completion(self):
        import engine

        eng = engine.Engine.__new__(engine.Engine)
        states = []
        eng._on_state = lambda state, detail, progress=None: states.append(progress)

        def fake_predownload(size, on_bytes):
            on_bytes(1, 1000)  # far below the 1%/100ms floor -- should be dropped
            on_bytes(999, 1000)  # a huge jump -- should report
            on_bytes(1000, 1000)  # completion -- must always report

        with patch.object(engine, "_predownload_with_progress", fake_predownload):
            eng._download_with_progress("small.en", "cpu")

        fractions = [p for p in states if p is not None]
        self.assertEqual(fractions, [0.999, 1.0])


class EngineGpuDownloadTests(unittest.TestCase):
    @staticmethod
    def _bare_engine(device: str) -> "engine.Engine":
        import engine

        eng = engine.Engine.__new__(engine.Engine)
        eng._settings = config.Settings(device=device, model_size="tiny.en")
        eng._model = None
        eng._loaded_key = None
        eng._lock = threading.RLock()
        eng._last_used = time.monotonic()
        eng._state = engine.UNLOADED
        eng._detail = ""
        eng._progress = None
        eng._on_state = lambda *a, **k: None
        return eng

    def test_downloads_gpu_runtime_when_cuda_selected_and_files_missing(self):
        import engine

        eng = self._bare_engine("cuda")
        calls = []
        with (
            patch.object(engine, "resolve_device", return_value="cuda"),
            patch.object(engine.gpu_runtime, "needs_download", return_value=True),
            patch.object(eng, "_download_gpu_runtime", side_effect=lambda: calls.append(1)),
            patch.object(engine, "_register_cuda_dlls"),
            patch.object(eng, "_download_with_progress"),
            patch("faster_whisper.WhisperModel", return_value=Mock()),
        ):
            eng.ensure_loaded()
        self.assertEqual(calls, [1])

    def test_skips_gpu_download_when_files_already_present(self):
        import engine

        eng = self._bare_engine("cuda")
        calls = []
        with (
            patch.object(engine, "resolve_device", return_value="cuda"),
            patch.object(engine.gpu_runtime, "needs_download", return_value=False),
            patch.object(eng, "_download_gpu_runtime", side_effect=lambda: calls.append(1)),
            patch.object(engine, "_register_cuda_dlls"),
            patch.object(eng, "_download_with_progress"),
            patch("faster_whisper.WhisperModel", return_value=Mock()),
        ):
            eng.ensure_loaded()
        self.assertEqual(calls, [])

    def test_skips_gpu_download_when_device_resolves_to_cpu(self):
        import engine

        eng = self._bare_engine("cpu")
        calls = []
        with (
            patch.object(engine, "resolve_device", return_value="cpu"),
            patch.object(
                engine.gpu_runtime, "needs_download", side_effect=AssertionError
            ),
            patch.object(eng, "_download_gpu_runtime", side_effect=lambda: calls.append(1)),
            patch.object(engine, "_register_cuda_dlls"),
            patch.object(eng, "_download_with_progress"),
            patch("faster_whisper.WhisperModel", return_value=Mock()),
        ):
            eng.ensure_loaded()
        self.assertEqual(calls, [])


def _fake_wheel_zip(path: Path, subdir: str, dll_names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in dll_names:
            zf.writestr(f"nvidia/{subdir}/bin/{name}", b"fake-dll-bytes")
        zf.writestr(f"nvidia/{subdir}/include/header.h", b"not-a-dll")


class GpuRuntimeVersionTests(unittest.TestCase):
    def test_version_tuple_parses_dotted_numbers(self):
        self.assertEqual(gpu_runtime._version_tuple("12.9.86"), (12, 9, 86))
        self.assertEqual(gpu_runtime._version_tuple("9.24.0.43"), (9, 24, 0, 43))

    def test_latest_wheel_picks_highest_version_under_the_ceiling(self):
        payload = json.dumps(
            {
                "releases": {
                    "9.1.0": [
                        {
                            "filename": "pkg-9.1.0-py3-none-win_amd64.whl",
                            "url": "https://x/9.1.0.whl",
                            "size": 100,
                        }
                    ],
                    "9.24.0.43": [
                        {
                            "filename": "pkg-9.24.0.43-py3-none-win_amd64.whl",
                            "url": "https://x/9.24.whl",
                            "size": 200,
                        }
                    ],
                    "10.0.0": [
                        {
                            "filename": "pkg-10.0.0-py3-none-win_amd64.whl",
                            "url": "https://x/10.0.whl",
                            "size": 300,
                        }
                    ],
                }
            }
        ).encode("utf-8")
        with patch.object(
            gpu_runtime.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            result = gpu_runtime._latest_win_amd64_wheel("pkg", max_major=10)
        self.assertEqual(result, ("https://x/9.24.whl", 200))

    def test_latest_wheel_ignores_yanked_and_non_windows_files(self):
        payload = json.dumps(
            {
                "releases": {
                    "2.0.0": [
                        {
                            "filename": "pkg-2.0.0-py3-none-manylinux.whl",
                            "url": "https://x/linux.whl",
                            "size": 50,
                        },
                        {
                            "filename": "pkg-2.0.0-py3-none-win_amd64.whl",
                            "url": "https://x/yanked.whl",
                            "size": 60,
                            "yanked": True,
                        },
                    ],
                    "1.0.0": [
                        {
                            "filename": "pkg-1.0.0-py3-none-win_amd64.whl",
                            "url": "https://x/1.0.whl",
                            "size": 70,
                        }
                    ],
                }
            }
        ).encode("utf-8")
        with patch.object(
            gpu_runtime.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            result = gpu_runtime._latest_win_amd64_wheel("pkg")
        self.assertEqual(result, ("https://x/1.0.whl", 70))

    def test_latest_wheel_returns_none_on_network_failure(self):
        with patch.object(
            gpu_runtime.urllib.request, "urlopen", side_effect=OSError("offline")
        ):
            self.assertIsNone(gpu_runtime._latest_win_amd64_wheel("pkg"))


class GpuRuntimeStateTests(unittest.TestCase):
    def test_not_frozen_never_needs_or_reports_installed(self):
        self.assertFalse(gpu_runtime.is_installed())
        self.assertFalse(gpu_runtime.needs_download(gpu_available=True))

    def test_frozen_with_all_files_present_is_installed(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for subdir in gpu_runtime._PACKAGE_SUBDIRS.values():
                (base / subdir / "bin").mkdir(parents=True)
            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime, "runtime_dir", return_value=base),
            ):
                self.assertTrue(gpu_runtime.is_installed())
                self.assertFalse(gpu_runtime.needs_download(gpu_available=True))
                self.assertFalse(gpu_runtime.needs_download(gpu_available=False))

    def test_frozen_with_missing_files_needs_download_only_with_gpu_present(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)  # empty -- nothing installed
            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime, "runtime_dir", return_value=base),
            ):
                self.assertFalse(gpu_runtime.is_installed())
                self.assertTrue(gpu_runtime.needs_download(gpu_available=True))
                self.assertFalse(gpu_runtime.needs_download(gpu_available=False))


class GpuRuntimeInstallTests(unittest.TestCase):
    def test_not_frozen_download_is_a_noop(self):
        self.assertFalse(gpu_runtime.download_and_install())

    def test_builds_the_full_tree_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "install" / "nvidia"

            def fake_latest(package, max_major=None):
                return (f"https://x/{package}.whl", 10)

            def fake_download(url, dest, on_progress):
                package = url.rsplit("/", 1)[-1].removesuffix(".whl")
                subdir = gpu_runtime._PACKAGE_SUBDIRS[package]
                _fake_wheel_zip(dest, subdir, [f"{subdir}64.dll"])
                if on_progress:
                    on_progress(10, 10)

            progress_calls = []
            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime, "runtime_dir", return_value=target),
                patch.object(
                    gpu_runtime, "_latest_win_amd64_wheel", side_effect=fake_latest
                ),
                patch.object(gpu_runtime, "_download", side_effect=fake_download),
            ):
                result = gpu_runtime.download_and_install(
                    lambda n, t: progress_calls.append((n, t))
                )

            self.assertTrue(result)
            for subdir in gpu_runtime._PACKAGE_SUBDIRS.values():
                self.assertTrue((target / subdir / "bin").is_dir())
                # Only bin/* was extracted from each wheel -- confirms the
                # zip-member filter actually narrows what lands on disk
                # rather than unpacking the whole wheel.
                self.assertFalse((target / subdir / "include").exists())
            self.assertTrue(progress_calls)
            self.assertEqual(progress_calls[-1], (30, 30))

    def test_fails_cleanly_when_a_wheel_lookup_fails(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "install" / "nvidia"
            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime, "runtime_dir", return_value=target),
                patch.object(gpu_runtime, "_latest_win_amd64_wheel", return_value=None),
            ):
                result = gpu_runtime.download_and_install()
        self.assertFalse(result)
        self.assertFalse(target.exists())

    def test_fails_cleanly_when_a_wheel_has_no_matching_files(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "install" / "nvidia"

            def fake_latest(package, max_major=None):
                return (f"https://x/{package}.whl", 10)

            def fake_download_empty(url, dest, on_progress):
                with zipfile.ZipFile(dest, "w") as zf:
                    zf.writestr("unrelated/file.txt", b"nothing useful here")

            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime, "runtime_dir", return_value=target),
                patch.object(
                    gpu_runtime, "_latest_win_amd64_wheel", side_effect=fake_latest
                ),
                patch.object(gpu_runtime, "_download", side_effect=fake_download_empty),
            ):
                result = gpu_runtime.download_and_install()
        self.assertFalse(result)
        self.assertFalse(target.exists())


class SettingsMigrationTests(unittest.TestCase):
    def test_legacy_settings_get_new_defaults(self):
        old_values = {
            "device": "cpu",
            "model_size": "small.en",
            "sleep_enabled": True,
            "sleep_after_minutes": 10,
        }
        migrated = config.Settings(**old_values).clamped()
        self.assertEqual(migrated.input_device, "")
        self.assertFalse(migrated.onboarding_complete)
        self.assertFalse(migrated.start_with_windows)
        self.assertNotIn("output_mode", config.Settings.__dataclass_fields__)
        self.assertNotIn("trailing_space", config.Settings.__dataclass_fields__)

    def test_outcome_modes_map_to_real_engine_settings(self):
        self.assertEqual(
            config.transcription_mode_settings("balanced"), ("small.en", "cpu")
        )
        self.assertEqual(
            config.transcription_mode_settings("faster"), ("small.en", "cuda")
        )
        self.assertEqual(
            config.transcription_mode_settings("accurate"), ("medium.en", "auto")
        )
        self.assertEqual(
            config.transcription_mode_settings("max"), ("large-v3-turbo", "cuda")
        )
        self.assertEqual(
            config.transcription_mode_for("large-v3-turbo", "cuda"), "max"
        )
        self.assertEqual(config.transcription_mode_for("base.en", "cpu"), "custom")


class StartupTests(unittest.TestCase):
    def test_startup_command_uses_hidden_launcher(self):
        command = startup.startup_command()
        self.assertIn("wscript.exe", command.lower())
        self.assertIn("run-dictate-hidden.vbs", command.lower())

    def test_frozen_build_starts_itself_directly(self):
        # A PyInstaller build has no run-dictate-hidden.vbs on disk to point
        # at -- pointing there anyway would silently no-op on every login.
        with (
            patch.object(startup.sys, "frozen", True, create=True),
            patch.object(startup.sys, "executable", r"C:\Program Files\Dictate\dictate.exe"),
        ):
            command = startup.startup_command()
        self.assertNotIn("wscript", command.lower())
        self.assertIn("dictate.exe", command.lower())


class ThemeTests(unittest.TestCase):
    def test_windows_app_theme_zero_means_dark(self):
        key = MagicMock()
        with (
            patch.object(theme.winreg, "OpenKey", return_value=key),
            patch.object(theme.winreg, "QueryValueEx", return_value=(0, 4)),
        ):
            self.assertTrue(theme.system_is_dark())

    def test_windows_app_theme_one_means_light(self):
        key = MagicMock()
        with (
            patch.object(theme.winreg, "OpenKey", return_value=key),
            patch.object(theme.winreg, "QueryValueEx", return_value=(1, 4)),
        ):
            self.assertFalse(theme.system_is_dark())

    def test_light_palette_has_dark_text_and_surface(self):
        palette = theme.colors(False)
        self.assertGreater(palette["surface"].lightness(), 200)
        self.assertLess(palette["text"].lightness(), 40)


class HotkeyTests(unittest.TestCase):
    def _hotkeys(self, *, ptt_key: str = "f9", settings_hotkey: str = "ctrl+alt+d"):
        events: list[tuple[str, float | None]] = []
        listener = hotkeys.Hotkeys(
            config.Settings(ptt_key=ptt_key, settings_hotkey=settings_hotkey),
            on_talk_start=lambda: events.append(("start", None)),
            on_talk_end=lambda duration: events.append(("end", duration)),
            on_settings=lambda: events.append(("settings", None)),
        )
        return listener, events

    def test_mouse_and_keyboard_hold_combination(self):
        listener, events = self._hotkeys(ptt_key="ctrl+mouse4")
        listener._press("ctrl")
        listener._press("mouse4")
        listener._release("mouse4")
        listener._release("ctrl")

        self.assertEqual([event[0] for event in events], ["start", "end"])

    def test_hold_ends_when_any_part_of_combo_is_released(self):
        listener, events = self._hotkeys(ptt_key="ctrl+f9")
        listener._press("ctrl")
        listener._press("f9")
        listener._release("ctrl")

        self.assertEqual([event[0] for event in events], ["start", "end"])

    def test_settings_combo_with_mouse_fires_once_per_hold(self):
        listener, events = self._hotkeys(settings_hotkey="ctrl+mouse5")
        listener._press("ctrl")
        listener._press("mouse5")
        listener._press("mouse5")
        listener._release("mouse5")
        listener._release("ctrl")

        self.assertEqual([event[0] for event in events], ["settings"])

    def test_capture_mode_does_not_fire_the_old_binding(self):
        listener, events = self._hotkeys(ptt_key="mouse4")
        listener.set_capture_active(True)
        listener._press("mouse4")
        listener._release("mouse4")
        listener.set_capture_active(False)

        self.assertEqual(events, [])

    def test_combination_is_stored_and_displayed_consistently(self):
        from pynput import mouse

        self.assertEqual(hotkeys.canonical_combo({"mouse4", "ctrl", "shift"}), "ctrl+shift+mouse4")
        self.assertEqual(hotkeys.format_combo("ctrl+shift+mouse4"), "Ctrl + Shift + Mouse 4")
        self.assertEqual(hotkeys.normalize_mouse(mouse.Button.x1), "mouse4")
        self.assertEqual(hotkeys.normalize_mouse(mouse.Button.x2), "mouse5")


class PttWarmStartTests(unittest.TestCase):
    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app._busy = False
        app._dictation_active = False
        app._ptt_preload_pending = False
        app._reload_pending = False
        app.meter = Mock()
        app.mic = Mock()
        app.cues = Mock()
        app.bar = Mock()
        app.meter_timer = Mock()
        app.engine = Mock()
        app.settings_window = None
        return app, main

    def test_ptt_warms_an_unloaded_model_after_capture_opens(self):
        app, main = self._app()
        app.engine.state = main.engine_mod.UNLOADED

        app._start_listening()

        app.mic.start.assert_called_once()
        app.engine.preload.assert_called_once()
        app.bar.set_state.assert_called_once_with("listening")
        self.assertTrue(app._dictation_active)
        self.assertTrue(app._ptt_preload_pending)

    def test_ptt_does_not_reload_a_model_that_is_already_ready(self):
        app, main = self._app()
        app.engine.state = main.engine_mod.READY

        app._start_listening()

        app.engine.preload.assert_not_called()
        self.assertTrue(app._dictation_active)
        self.assertFalse(app._ptt_preload_pending)

    def test_warmup_does_not_replace_the_listening_bar(self):
        app, main = self._app()
        app.engine.state = main.engine_mod.UNLOADED
        app._start_listening()
        app.bar.reset_mock()

        app._on_engine_state(main.engine_mod.LOADING, "small.en on CPU")
        app._on_engine_state(main.engine_mod.READY, "small.en on CPU")

        app.bar.set_state.assert_not_called()
        self.assertFalse(app._ptt_preload_pending)

    def test_failed_microphone_does_not_warm_the_model(self):
        app, main = self._app()
        app.engine.state = main.engine_mod.UNLOADED
        app.mic.start.side_effect = OSError("not available")

        app._start_listening()

        app.engine.preload.assert_not_called()
        self.assertFalse(app._dictation_active)


class StopListeningTests(unittest.TestCase):
    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app._busy = False
        app._dictation_active = True
        app.bridge = Mock()
        app.meter = Mock()
        app.mic = Mock()
        app.cues = Mock()
        app.bar = Mock()
        app.meter_timer = Mock()
        app.engine = Mock()
        app.settings_window = None
        return app, main

    def test_silent_hold_reports_no_audio_error(self):
        app, main = self._app()
        app.mic.stop.return_value = np.zeros(4000, dtype=np.float32)

        app._stop_listening(1.0)

        app.bar.set_state.assert_called_once_with(
            "error", "No audio detected — check your microphone"
        )
        self.assertFalse(app._dictation_active)
        self.assertFalse(app._busy)

    def test_muted_device_reports_no_audio_error_even_with_full_duration(self):
        app, main = self._app()
        # A muted or disconnected mic still streams frames -- just at
        # near-zero amplitude -- so clip.size alone cannot catch this case.
        app.mic.stop.return_value = np.full(8000, 1e-6, dtype=np.float32)

        app._stop_listening(2.0)

        app.bar.set_state.assert_called_once_with(
            "error", "No audio detected — check your microphone"
        )
        self.assertFalse(app._busy)

    def test_audible_hold_proceeds_to_transcription(self):
        app, main = self._app()
        app.mic.stop.return_value = np.linspace(-0.4, 0.4, 4000).astype(np.float32)
        app.engine.transcribe.return_value = ""
        app.hotkeys = Mock()

        app._stop_listening(1.0)

        app.bar.set_state.assert_called_once_with("transcribing")
        self.assertTrue(app._busy)


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(
            ["dictate-tests", "-platform", "offscreen"]
        )

    def test_settings_first_run_and_privacy_views_construct(self):
        from PySide6.QtWidgets import QLabel

        import engine
        import settings_window

        microphone = audio.InputDevice("Windows WASAPI|Desk Mic|0", "Desk Mic", 2)

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=False),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[microphone]),
        ):
            settings = config.Settings(input_device=microphone.key)
            window = settings_window.SettingsWindow(settings, DummyEngine())
            first_run = settings_window.FirstRunDialog(settings)
            privacy = settings_window.PrivacyDialog()

        self.assertEqual(window.mic_box.currentData(), microphone.key)
        self.assertEqual(window.mode_box.currentData(), "balanced")
        self.assertEqual(window.mode_box.currentText(), "Everyday (recommended)")
        self.assertGreaterEqual(window.mode_box.findData("max"), 0)
        self.assertIn("Default", window.sleep_slider.value_label.text())
        self.assertTrue(window.sleep_slider.reset_btn.isHidden())
        window.sleep_slider.setValue(30)
        self.assertIn("Default: 10 min", window.sleep_slider.value_label.text())
        self.assertFalse(window.sleep_slider.reset_btn.isHidden())
        window.sleep_slider.reset_btn.click()
        self.assertEqual(window.sleep_slider.value(), 10)
        self.assertTrue(window.advanced_panel.isHidden())
        window.advanced_btn.click()
        self.assertFalse(window.advanced_panel.isHidden())
        self.assertFalse(hasattr(window, "apply_btn"))
        self.assertFalse(hasattr(window, "status"))
        self.assertFalse(hasattr(window, "output_box"))
        self.assertFalse(hasattr(window, "space_check"))
        self.assertEqual(window.ptt_edit.text(), "F9")
        self.assertEqual(window.hotkey_edit.text(), "Ctrl + Alt + D")
        self.assertEqual(first_run.input_device, microphone.key)
        privacy_text = " ".join(label.text() for label in privacy.findChildren(QLabel))
        self.assertIn("Your voice stays on this PC", privacy_text)
        self.assertIn("Hugging Face", privacy_text)
        self.assertIn("does not print", privacy_text)
        self.assertNotIn(str(config.CONFIG_PATH), window.save_status.text())

        privacy.close()
        first_run.close()
        window.close()

    def test_keybind_controls_record_mouse_and_keyboard_combinations(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())

        recorded: list[str] = []
        window.ptt_edit.bindingChanged.connect(recorded.append)
        window.ptt_edit._start_capture()
        window.ptt_edit._press("ctrl")
        window.ptt_edit._press("mouse4")
        window.ptt_edit._release("mouse4")
        window.ptt_edit._release("ctrl")

        self.assertEqual(recorded, ["ctrl+mouse4"])
        self.assertEqual(window.ptt_edit.binding(), "ctrl+mouse4")
        self.assertEqual(window.ptt_edit.text(), "Ctrl + Mouse 4")
        self.assertEqual(
            settings_window._qt_key_name(
                QKeyEvent(QEvent.KeyPress, Qt.Key_F9, Qt.NoModifier)
            ),
            "f9",
        )
        window.close()

    def test_settings_auto_save_and_startup_switch(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""

            def __init__(self):
                self.preload_calls = 0

            def preload(self):
                self.preload_calls += 1

        dummy = DummyEngine()
        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.config, "save") as save,
            patch.object(settings_window.startup_mod, "set_enabled") as set_startup,
        ):
            window = settings_window.SettingsWindow(config.Settings(), dummy)
            window.startup_check.setChecked(True)
            window._save_timer.stop()
            window._save_now()

            self.assertTrue(window._settings.start_with_windows)
            set_startup.assert_called_once_with(True)
            save.assert_called_once()

            window.mode_box.setCurrentIndex(window.mode_box.findData("faster"))
            window._save_timer.stop()
            window._save_now()

        self.assertEqual(window._settings.model_size, "small.en")
        self.assertEqual(window._settings.device, "cuda")
        self.assertEqual(dummy.preload_calls, 1)
        window.close()

    def test_device_row_explains_the_gpu_download_when_files_are_missing(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.gpu_runtime, "needs_download", return_value=True),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
        self.assertIn("downloads", window.device_desc_label.text().lower())
        window.close()

    def test_refresh_status_shows_live_progress_and_updater_state(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.LOADING
            active_device = ""
            last_status = (engine.LOADING, "GPU acceleration — downloading 42%", 0.42)

            def preload(self):
                pass

        class DummyUpdater:
            last_status = (updater.IDLE, "")

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
        ):
            dummy_updater = DummyUpdater()
            window = settings_window.SettingsWindow(
                config.Settings(), DummyEngine(), dummy_updater
            )
            window._pending_reload = True

            window.refresh_status()
            self.assertIn("42%", window.save_status.text())

            # Updater activity takes priority over the engine's own status,
            # and disables the button while a check/download is in flight.
            dummy_updater.last_status = (
                updater.DOWNLOADING,
                "Downloading update 0.1.0-beta.3 — 10%",
            )
            window.refresh_status()
            self.assertIn("Downloading update", window.save_status.text())
            self.assertFalse(window.update_btn.isEnabled())

            dummy_updater.last_status = (updater.IDLE, "")
            window.refresh_status()
            self.assertTrue(window.update_btn.isEnabled())
            self.assertIn("42%", window.save_status.text())
        window.close()

    def test_check_for_updates_button_hidden_without_an_updater(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)

            def preload(self):
                pass

        class DummyUpdater:
            last_status = (updater.IDLE, "")

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
        ):
            no_updater = settings_window.SettingsWindow(config.Settings(), DummyEngine())
            with_updater = settings_window.SettingsWindow(
                config.Settings(), DummyEngine(), DummyUpdater()
            )
        # isVisible() only reflects an explicit setVisible() call once the
        # whole window has actually been shown -- without .show() every
        # widget reports invisible regardless, which would pass this
        # assertion for the wrong reason.
        no_updater.show()
        with_updater.show()
        self.assertFalse(no_updater.update_btn.isVisible())
        self.assertTrue(with_updater.update_btn.isVisible())
        no_updater.close()
        with_updater.close()


class TalkKeyTests(unittest.TestCase):
    def test_f9_is_the_default(self):
        self.assertEqual(config.Settings().ptt_key, "f9")
        self.assertEqual(config.DEFAULT_PTT_KEY, "f9")

    def test_an_unusable_binding_falls_back_to_f9(self):
        # parse_combo("") yields no keys at all, so without this the app has
        # nothing bound and no way to recover except hand-editing the file.
        for broken in ("", "   ", "+", "+++", None, 123, []):
            with self.subTest(binding=broken):
                self.assertEqual(
                    config.Settings(ptt_key=broken).clamped().ptt_key, "f9"
                )

    def test_a_broken_settings_hotkey_cannot_lock_you_out(self):
        for broken in ("", "  ", None):
            with self.subTest(binding=broken):
                self.assertEqual(
                    config.Settings(settings_hotkey=broken).clamped().settings_hotkey,
                    "ctrl+alt+d",
                )

    def test_pressing_f9_actually_starts_and_stops_talking(self):
        # The default, the clamp and the parser were all correct in isolation
        # while F9 was still reported as not working, so this drives the real
        # listener with a real key event rather than asserting about strings.
        import hotkeys
        from pynput import keyboard

        events = []
        listener = hotkeys.Hotkeys(
            config.Settings(),
            on_talk_start=lambda: events.append("start"),
            on_talk_end=lambda held: events.append("end"),
            on_settings=lambda: events.append("settings"),
        )
        listener._on_press(keyboard.Key.f9)
        listener._on_release(keyboard.Key.f9)
        self.assertEqual(events, ["start", "end"])

    def test_f9_still_works_after_a_corrupt_binding_is_clamped(self):
        import hotkeys
        from pynput import keyboard

        events = []
        listener = hotkeys.Hotkeys(
            config.Settings(ptt_key="").clamped(),
            on_talk_start=lambda: events.append("start"),
            on_talk_end=lambda held: events.append("end"),
            on_settings=lambda: None,
        )
        listener._on_press(keyboard.Key.f9)
        listener._on_release(keyboard.Key.f9)
        self.assertEqual(events, ["start", "end"])

    def test_a_real_binding_is_left_alone(self):
        for good in ("f9", "ctrl+shift", "mouse4", "ctrl+alt+space"):
            with self.subTest(binding=good):
                self.assertEqual(
                    config.Settings(ptt_key=good).clamped().ptt_key, good
                )


class SoundCueTests(unittest.TestCase):
    def test_cues_are_click_free_and_short(self):
        import sounds

        for name, clip in sounds._cues().items():
            with self.subTest(cue=name):
                # A waveform that starts or ends away from zero is a click,
                # which is the most fatiguing thing a per-use sound can have.
                self.assertAlmostEqual(float(clip[0]), 0.0, places=4)
                self.assertAlmostEqual(float(clip[-1]), 0.0, places=4)
                self.assertLessEqual(float(abs(clip).max()), 1.0)
                self.assertLess(len(clip) / sounds.SAMPLE_RATE, 0.25)
        # Stored near full scale for dynamic range; loudness is one knob.
        self.assertGreater(sounds.FILE_PEAK, 0.5)
        self.assertLessEqual(sounds.CUE_VOLUME, 0.6)

    def test_retuning_a_cue_invalidates_the_cached_file(self):
        import sounds

        before = sounds._fingerprint()
        original = sounds.HIGH_HZ
        try:
            sounds.HIGH_HZ = original + 1.0
            self.assertNotEqual(sounds._fingerprint(), before)
        finally:
            sounds.HIGH_HZ = original
        self.assertEqual(sounds._fingerprint(), before)

    def test_open_and_close_are_the_same_pair_reversed(self):
        import sounds

        # The cues must stay a matched pair; if one is retuned and the other
        # is not they stop reading as "open" and "close".
        self.assertEqual(len(sounds._cues()["start"]), len(sounds._cues()["stop"]))

    def test_disabled_cues_never_touch_the_audio_device(self):
        import sounds

        with patch.object(sounds.Cues, "_fire") as fire:
            quiet = sounds.Cues(enabled=False)
            quiet.play("start")
            quiet.play("stop")
            fire.assert_not_called()
            quiet.update_settings(config.Settings(sound_cues=True))
            quiet.play("start")
            fire.assert_called_once_with("start")

    def test_a_missing_output_device_cannot_break_dictation(self):
        import sounds

        with patch.object(sounds.sd, "play", side_effect=OSError("no device")):
            sounds._emit(sounds._cues()["start"])  # must swallow, not raise

    def test_toggle_follows_settings(self):
        import sounds

        cues = sounds.Cues(enabled=True)
        cues.update_settings(config.Settings(sound_cues=False))
        self.assertFalse(cues.enabled)
        cues.update_settings(config.Settings(sound_cues=True))
        self.assertTrue(cues.enabled)

    def test_setting_survives_a_save_and_load(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "settings.json"
            with patch.object(config, "CONFIG_DIR", Path(folder)), patch.object(
                config, "CONFIG_PATH", target
            ):
                config.save(config.Settings(sound_cues=False))
                self.assertFalse(config.load().sound_cues)


def _fake_response(body: bytes):
    """A context-manager-shaped stand-in for urllib.request.urlopen's result."""
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class UpdaterVersionTests(unittest.TestCase):
    def test_parses_a_beta_tag(self):
        self.assertEqual(updater.parse_version("0.1.0-beta.2"), (0, 1, 0, 0, 2))

    def test_strips_a_leading_v(self):
        self.assertEqual(updater.parse_version("v0.1.0-beta.2"), (0, 1, 0, 0, 2))

    def test_a_final_release_ranks_above_any_beta(self):
        self.assertEqual(updater.parse_version("0.1.0"), (0, 1, 0, 1, 0))

    def test_unparseable_text_sorts_lowest(self):
        self.assertEqual(updater.parse_version("not-a-version"), (0, 0, 0, 0, 0))

    def test_beta_numbers_compare_numerically_not_lexicographically(self):
        # A naive string/tuple compare would rank "beta.10" below "beta.2".
        self.assertTrue(updater.is_newer("0.1.0-beta.10", "0.1.0-beta.2"))
        self.assertFalse(updater.is_newer("0.1.0-beta.2", "0.1.0-beta.10"))

    def test_final_release_outranks_a_beta_of_the_same_version(self):
        self.assertTrue(updater.is_newer("0.1.0", "0.1.0-beta.99"))
        self.assertFalse(updater.is_newer("0.1.0-beta.1", "0.1.0"))

    def test_equal_versions_are_not_newer(self):
        self.assertFalse(updater.is_newer("0.1.0-beta.2", "0.1.0-beta.2"))

    def test_unparseable_candidate_is_never_newer(self):
        self.assertFalse(updater.is_newer("garbage", "0.0.1-beta.1"))


class UpdaterReleaseFetchTests(unittest.TestCase):
    def test_parses_the_installer_asset(self):
        payload = json.dumps(
            {
                "tag_name": "v0.1.0-beta.3",
                "assets": [
                    {
                        "name": "Dictate-Setup-0.1.0-beta.3.exe",
                        "browser_download_url": "https://x/installer.exe",
                        "size": 12345,
                    },
                ],
            }
        ).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            info = updater._fetch_latest_release()
        self.assertEqual(info["version"], "0.1.0-beta.3")
        self.assertEqual(info["installer_url"], "https://x/installer.exe")
        self.assertEqual(info["installer_size"], 12345)

    def test_returns_none_without_an_installer_asset(self):
        payload = json.dumps({"tag_name": "v0.1.0-beta.3", "assets": []}).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            self.assertIsNone(updater._fetch_latest_release())

    def test_returns_none_on_network_failure(self):
        with patch.object(
            updater.urllib.request, "urlopen", side_effect=OSError("offline")
        ):
            self.assertIsNone(updater._fetch_latest_release())


class UpdaterFlowTests(unittest.TestCase):
    def test_stages_a_newer_release_and_notifies_ready(self):
        ready = threading.Event()
        ready_args = []

        def on_ready(version, path):
            ready_args.append((version, path))
            ready.set()

        info = {
            "version": "9.9.9",
            "installer_url": "https://x/installer.exe",
            "installer_name": "installer-flow-test.exe",
            "installer_size": 5,
        }
        with (
            patch.object(updater, "_fetch_latest_release", return_value=info),
            patch.object(
                updater,
                "_download",
                side_effect=lambda url, dest, cb: dest.write_bytes(b"12345"),
            ),
        ):
            u = updater.Updater(
                on_ready=on_ready, current_version="0.1.0-beta.2", check_interval=9999
            )
            try:
                self.assertTrue(ready.wait(timeout=5))
            finally:
                u.shutdown()

        version, path = ready_args[0]
        self.assertEqual(version, "9.9.9")
        self.assertTrue(path.exists())
        path.unlink(missing_ok=True)

    def test_up_to_date_notification_fires_only_for_a_manual_check(self):
        startup_checked = threading.Event()
        manual_done = threading.Event()
        calls = []
        call_count = [0]

        def fake_fetch():
            call_count[0] += 1
            if call_count[0] == 1:
                startup_checked.set()
            return {
                "version": "0.0.1",
                "installer_url": "x",
                "installer_name": "x.exe",
                "installer_size": None,
            }

        def on_up_to_date():
            calls.append(1)
            manual_done.set()

        with patch.object(updater, "_fetch_latest_release", side_effect=fake_fetch):
            u = updater.Updater(
                on_up_to_date=on_up_to_date,
                current_version="0.1.0-beta.2",
                check_interval=9999,
            )
            try:
                self.assertTrue(startup_checked.wait(timeout=5))
                time.sleep(0.05)  # let the automatic check release its lock
                self.assertEqual(calls, [])  # the automatic check stayed silent
                u.check_now(silent=False)
                self.assertTrue(manual_done.wait(timeout=5))
            finally:
                u.shutdown()
        self.assertEqual(calls, [1])

    def test_apply_staged_is_false_with_nothing_ready(self):
        with patch.object(updater, "_fetch_latest_release", return_value=None):
            u = updater.Updater(current_version="0.1.0-beta.2", check_interval=9999)
            try:
                time.sleep(0.1)
                self.assertFalse(u.apply_staged())
            finally:
                u.shutdown()

    def test_apply_staged_launches_the_installer_silently(self):
        ready = threading.Event()
        info = {
            "version": "9.9.9",
            "installer_url": "https://x/installer.exe",
            "installer_name": "installer-apply-test.exe",
            "installer_size": 5,
        }
        staged_path = Path(tempfile.gettempdir()) / "dictate-update" / info["installer_name"]
        with (
            patch.object(updater, "_fetch_latest_release", return_value=info),
            patch.object(
                updater,
                "_download",
                side_effect=lambda url, dest, cb: dest.write_bytes(b"12345"),
            ),
        ):
            u = updater.Updater(
                on_ready=lambda v, p: ready.set(),
                current_version="0.1.0-beta.2",
                check_interval=9999,
            )
            try:
                self.assertTrue(ready.wait(timeout=5))
                with patch.object(updater.subprocess, "Popen") as popen:
                    result = u.apply_staged()
                self.assertTrue(result)
                popen.assert_called_once()
                args = popen.call_args[0][0]
                self.assertTrue(args[0].endswith(info["installer_name"]))
                self.assertEqual(args[1], "/VERYSILENT")
            finally:
                u.shutdown()
                staged_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

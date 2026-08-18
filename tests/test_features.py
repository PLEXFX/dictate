from __future__ import annotations

import hashlib
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

    def test_temporary_copy_restores_the_previous_clipboard_after_the_window(self):
        copied: list[str] = []
        with (
            patch.object(inject.pyperclip, "paste", return_value="before"),
            patch.object(inject.pyperclip, "copy", side_effect=copied.append),
            patch.object(inject, "_clipboard_sequence", side_effect=[42, 42]),
        ):
            restore = inject.copy_temporarily("dictated words")
            self.assertIsNotNone(restore)
            self.assertTrue(restore())

        self.assertEqual(copied, ["dictated words", "before"])

    def test_temporary_copy_never_restores_over_a_new_user_clipboard_item(self):
        copied: list[str] = []
        with (
            patch.object(inject.pyperclip, "paste", return_value="before"),
            patch.object(inject.pyperclip, "copy", side_effect=copied.append),
            patch.object(inject, "_clipboard_sequence", side_effect=[42, 43]),
        ):
            restore = inject.copy_temporarily("dictated words")
            self.assertIsNotNone(restore)
            self.assertFalse(restore())

        self.assertEqual(copied, ["dictated words"])

    def test_temporary_copy_refuses_to_touch_an_unreadable_clipboard(self):
        with patch.object(inject.pyperclip, "paste", side_effect=RuntimeError("busy")):
            self.assertIsNone(inject.copy_temporarily("dictated words"))


class VocabularyTests(unittest.TestCase):
    def test_vocabulary_is_cleaned_deduplicated_and_capped(self):
        settings = config.Settings(
            vocabulary=[" Northwind   Studio ", "northwind studio", "", 17] + [
                f"term {n}" for n in range(150)
            ]
        ).clamped()

        self.assertEqual(settings.vocabulary[0], "Northwind Studio")
        self.assertEqual(sum(word.casefold() == "northwind studio" for word in settings.vocabulary), 1)
        self.assertLessEqual(len(settings.vocabulary), config.MAX_VOCABULARY_WORDS)

    def test_engine_sends_vocabulary_as_a_recognition_hint(self):
        import engine

        model = Mock()
        model.transcribe.return_value = ([Mock(text=" Northwind Studio ")], Mock())
        subject = engine.Engine.__new__(engine.Engine)
        subject._lock = threading.RLock()
        subject._settings = config.Settings(vocabulary=["Northwind Studio", "CTranslate2"])
        subject._model = model
        subject._loaded_key = ("small.en", "cpu")
        subject._last_used = 0.0
        subject.ensure_loaded = Mock(return_value=model)
        subject._set_state = Mock()

        self.assertEqual(subject.transcribe(np.ones(20, dtype=np.float32)), "Northwind Studio")
        self.assertEqual(
            model.transcribe.call_args.kwargs["initial_prompt"],
            "Names and terms that may appear: Northwind Studio, CTranslate2.",
        )

    def test_engine_omits_the_hint_when_no_words_are_added(self):
        import engine

        model = Mock()
        model.transcribe.return_value = ([Mock(text=" hello ")], Mock())
        subject = engine.Engine.__new__(engine.Engine)
        subject._lock = threading.RLock()
        subject._settings = config.Settings()
        subject._model = model
        subject._loaded_key = ("small.en", "cpu")
        subject._last_used = 0.0
        subject.ensure_loaded = Mock(return_value=model)
        subject._set_state = Mock()

        subject.transcribe(np.ones(20, dtype=np.float32))
        self.assertNotIn("initial_prompt", model.transcribe.call_args.kwargs)

    def test_live_preview_uses_the_model_without_entering_transcribing_state(self):
        import engine

        model = Mock()
        model.transcribe.return_value = ([Mock(text=" words appearing live ")], Mock())
        subject = engine.Engine.__new__(engine.Engine)
        subject._lock = threading.RLock()
        subject._settings = config.Settings()
        subject._model = model
        subject._loaded_key = ("small.en", "cpu")
        subject._last_used = 0.0
        subject.ensure_loaded = Mock(return_value=model)
        subject._set_state = Mock()

        result = subject.transcribe_preview(np.ones(20, dtype=np.float32))

        self.assertEqual(result, "words appearing live")
        self.assertTrue(model.transcribe.call_args.kwargs["without_timestamps"])
        subject._set_state.assert_not_called()

    def test_enhanced_preview_uses_a_dedicated_cpu_model_and_reports_inference_time(self):
        import engine

        model = Mock()
        model.transcribe.return_value = ([Mock(text=" quick preview ")], Mock())
        subject = engine.PreviewEngine(config.Settings(vocabulary=["Northwind"]))
        subject.ensure_loaded = Mock(return_value=model)

        with patch.object(engine.time, "perf_counter", side_effect=[10.0, 10.35]):
            text, seconds = subject.transcribe(np.ones(20, dtype=np.float32))

        self.assertEqual(text, "quick preview")
        self.assertAlmostEqual(seconds, 0.35)
        self.assertTrue(model.transcribe.call_args.kwargs["without_timestamps"])
        self.assertIn("Northwind", model.transcribe.call_args.kwargs["initial_prompt"])


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

    def test_snapshot_reads_recording_without_stopping_and_can_bound_the_window(self):
        capture = audio.MicCapture()
        capture._frames = [
            np.full(audio.SAMPLE_RATE, 1.0, dtype=np.float32),
            np.full(audio.SAMPLE_RATE, 2.0, dtype=np.float32),
        ]

        preview = capture.snapshot(max_seconds=0.5)

        self.assertEqual(preview.size, audio.SAMPLE_RATE // 2)
        self.assertTrue((preview == 2.0).all())
        self.assertEqual(len(capture._frames), 2)


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
        eng._on_state = lambda state, detail, progress=None: states.append((detail, progress))

        def fake_predownload(size, on_bytes):
            on_bytes(1, 1000)  # far below the 1%/100ms floor -- should be dropped
            on_bytes(999, 1000)  # a huge jump -- should report
            on_bytes(1000, 1000)  # completion -- must always report

        with patch.object(engine, "_predownload_with_progress", fake_predownload):
            eng._download_with_progress("small.en", "cpu")

        fractions = [progress for _detail, progress in states if progress is not None]
        self.assertEqual(fractions, [0.999, 1.0])
        self.assertEqual(states[-1], ("Downloading Small English · CPU · 100%", 1.0))

    def test_preload_reuses_the_active_warmup_thread(self):
        import engine

        eng = engine.Engine.__new__(engine.Engine)
        eng._preload_guard = threading.Lock()
        eng._preload_thread = None

        class FakeThread:
            instances = []

            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon
                self.alive = False
                self.__class__.instances.append(self)

            def is_alive(self):
                return self.alive

            def start(self):
                self.alive = True

        with patch.object(engine.threading, "Thread", FakeThread):
            self.assertTrue(eng.preload())
            self.assertFalse(eng.preload())

        self.assertEqual(len(FakeThread.instances), 1)
        self.assertTrue(FakeThread.instances[0].daemon)


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
        eng._gpu_download_thread = None
        eng._gpu_downloading = False
        eng._gpu_progress = None
        eng._on_gpu_status = lambda *a, **k: None
        return eng

    def test_gpu_preference_resolves_to_cpu_until_the_optional_runtime_is_installed(self):
        """First dictation stays on CPU; GPU installation is an explicit UI action."""
        import engine

        eng = self._bare_engine("cuda")
        with (
            patch.object(engine, "resolve_device", return_value="cpu"),
            patch.object(engine, "_register_cuda_dlls"),
            patch.object(eng, "_download_with_progress"),
            patch("faster_whisper.WhisperModel", return_value=Mock()) as mock_model,
        ):
            eng.ensure_loaded()
        self.assertEqual(eng._loaded_key, ("tiny.en", "cpu"))
        mock_model.assert_called_once_with(
            "tiny.en", device="cpu", compute_type="int8", download_root=config.model_dir()
        )

    def test_skips_gpu_download_when_files_already_present(self):
        import engine

        eng = self._bare_engine("cuda")
        calls = []
        with (
            patch.object(engine, "resolve_device", return_value="cuda"),
            patch.object(engine.gpu_runtime, "needs_download", return_value=False),
            patch.object(
                eng, "_ensure_gpu_download_started", side_effect=lambda: calls.append(1)
            ),
            patch.object(engine, "_register_cuda_dlls"),
            patch.object(eng, "_download_with_progress"),
            patch("faster_whisper.WhisperModel", return_value=Mock()),
        ):
            eng.ensure_loaded()
        self.assertEqual(calls, [])
        self.assertEqual(eng._loaded_key, ("tiny.en", "cuda"))

    def test_skips_gpu_download_when_device_resolves_to_cpu(self):
        import engine

        eng = self._bare_engine("cpu")
        calls = []
        with (
            patch.object(engine, "resolve_device", return_value="cpu"),
            patch.object(
                engine.gpu_runtime, "needs_download", side_effect=AssertionError
            ),
            patch.object(
                eng, "_ensure_gpu_download_started", side_effect=lambda: calls.append(1)
            ),
            patch.object(engine, "_register_cuda_dlls"),
            patch.object(eng, "_download_with_progress"),
            patch("faster_whisper.WhisperModel", return_value=Mock()),
        ):
            eng.ensure_loaded()
        self.assertEqual(calls, [])

    def test_ensure_gpu_download_started_is_idempotent_while_running(self):
        import engine

        eng = self._bare_engine("cuda")
        started = threading.Event()
        release = threading.Event()

        def fake_download(on_progress=None):
            started.set()
            release.wait(timeout=2)
            return True

        with patch.object(engine.gpu_runtime, "download_and_install", side_effect=fake_download):
            eng._ensure_gpu_download_started()
            self.assertTrue(started.wait(timeout=2))
            first_thread = eng._gpu_download_thread
            eng._ensure_gpu_download_started()
            self.assertIs(eng._gpu_download_thread, first_thread)
            release.set()
            first_thread.join(timeout=2)

    def test_run_gpu_download_reports_status_and_invalidates_cpu_model_on_success(self):
        import engine

        eng = self._bare_engine("cuda")
        eng._model = Mock()
        eng._loaded_key = ("tiny.en", "cpu")
        statuses = []
        eng._on_gpu_status = lambda downloading, progress=None: statuses.append(
            (downloading, progress)
        )
        with (
            patch.object(engine.gpu_runtime, "download_and_install", return_value=True),
            patch.object(engine, "resolve_device", return_value="cuda"),
            patch.object(engine.gpu_runtime, "needs_download", return_value=False),
        ):
            eng._run_gpu_download()
        self.assertFalse(eng._gpu_downloading)
        self.assertIsNone(eng._gpu_progress)
        self.assertEqual(statuses[-1], (False, None))
        self.assertIsNone(eng._model)  # invalidated so the next load lands on CUDA

    def test_start_gpu_download_no_op_without_real_gpu_or_when_already_present(self):
        import engine

        eng = self._bare_engine("cuda")
        with (
            patch.object(engine, "cuda_available", return_value=False),
            patch.object(eng, "_ensure_gpu_download_started") as mock_start,
        ):
            self.assertFalse(eng.start_gpu_download())
            mock_start.assert_not_called()

        with (
            patch.object(engine, "cuda_available", return_value=True),
            patch.object(engine.gpu_runtime, "needs_download", return_value=False),
            patch.object(eng, "_ensure_gpu_download_started") as mock_start,
        ):
            self.assertFalse(eng.start_gpu_download())
            mock_start.assert_not_called()

        with (
            patch.object(engine, "cuda_available", return_value=True),
            patch.object(engine.gpu_runtime, "needs_download", return_value=True),
            patch.object(eng, "_ensure_gpu_download_started") as mock_start,
        ):
            self.assertTrue(eng.start_gpu_download())
            mock_start.assert_called_once()


class ModelStorageTests(unittest.TestCase):
    def test_models_live_under_dictates_own_data_folder(self):
        self.assertEqual(config.model_dir(), config.CONFIG_DIR / "models")

    def test_progress_download_uses_dictates_private_model_cache(self):
        import engine

        hub = Mock()
        hub.snapshot_download.side_effect = [RuntimeError("not cached"), None]
        with patch.dict(sys.modules, {"huggingface_hub": hub}):
            engine._predownload_with_progress("small.en", lambda *_args: None)

        self.assertEqual(hub.snapshot_download.call_count, 2)
        for call in hub.snapshot_download.call_args_list:
            self.assertEqual(call.kwargs["cache_dir"], config.model_dir())

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


class InstallerGpuScriptSyncTests(unittest.TestCase):
    """installer/download-gpu-runtime.ps1 is a hand-maintained Pascal-Script-
    callable mirror of gpu_runtime.py's own PyPI-fetch logic, since Inno
    Setup can't call into this Python module directly. Nothing enforces the
    two staying in sync except a human remembering to update both -- this is
    the guard that catches a silent drift (a renamed package, a bumped
    cudnn ceiling) before it ships a script that quietly extracts the wrong
    files, or none at all, during install."""

    @staticmethod
    def _script_text() -> str:
        path = (
            Path(__file__).resolve().parent.parent
            / "installer"
            / "download-gpu-runtime.ps1"
        )
        return path.read_text(encoding="utf-8")

    def test_script_names_the_same_packages_and_subdirs(self):
        text = self._script_text()
        for package, subdir in gpu_runtime._PACKAGE_SUBDIRS.items():
            self.assertIn(package, text)
            self.assertIn(subdir, text)

    def test_script_uses_the_same_cudnn_major_ceiling(self):
        text = self._script_text()
        self.assertIn(f"MaxMajor = {gpu_runtime._CUDNN_MAX_MAJOR}", text)


class InstallerUninstallPreservesDownloadsTests(unittest.TestCase):
    """installer/dictate.iss used to unconditionally wipe both
    {userappdata}\\dictate (settings.json + every downloaded speech model)
    and {app}\\_internal (which now also holds the GPU runtime) on every
    uninstall, with no way to opt out -- a real user complaint (a plain
    reinstall re-downloading a multi-GB GPU runtime and every speech
    model). Fixed to ask first and only delete on an explicit yes. Nothing
    Python-side can execute this Pascal script, so this is a text-level
    guard against a future edit silently reintroducing the unconditional
    wipe -- not a substitute for actually running an uninstall."""

    @staticmethod
    def _installer_text() -> str:
        path = (
            Path(__file__).resolve().parent.parent / "installer" / "dictate.iss"
        )
        return path.read_text(encoding="utf-8")

    def test_uninstall_delete_no_longer_unconditionally_wipes_user_data_or_internal(self):
        text = self._installer_text()
        uninstall_delete = text.split("[UninstallDelete]", 1)[1].split("[Code]", 1)[0]
        self.assertNotIn('"{userappdata}\\dictate"', uninstall_delete)
        self.assertNotIn('"{app}\\_internal"', uninstall_delete)

    def test_uninstall_prompts_before_removing_downloads(self):
        text = self._installer_text()
        self.assertIn("function InitializeUninstall", text)
        self.assertIn("MsgBox", text)
        self.assertIn("KeepDownloads", text)
        self.assertIn("usPostUninstall", text)
        self.assertIn("not KeepDownloads", text)

    def test_uninstall_never_blocks_on_a_msgbox_during_a_silent_run(self):
        """A silent/unattended uninstall (a script, /VERYSILENT with no
        /REMOVEDATA) must never reach the MsgBox call -- custom Pascal
        Script message boxes aren't suppressed by /SUPPRESSMSGBOXES the way
        Setup's own built-in dialogs are, so one left reachable there would
        hang forever waiting for a click nobody's there to give."""
        text = self._installer_text()
        function_body = text.split("function InitializeUninstall", 1)[1].split(
            "procedure CurUninstallStepChanged", 1
        )[0]
        self.assertIn("UninstallSilent()", function_body)
        self.assertIn("/REMOVEDATA", function_body)
        # Both early-exit branches must appear before the MsgBox call, not
        # after -- a naive reordering would silently reintroduce the hang.
        remove_data_pos = function_body.index("CmdLineParamExists")
        silent_pos = function_body.index("UninstallSilent()")
        msgbox_pos = function_body.index("MsgBox")
        self.assertLess(remove_data_pos, msgbox_pos)
        self.assertLess(silent_pos, msgbox_pos)

    def test_uninstall_cleanup_retries_and_logs_instead_of_trying_once_silently(self):
        """A real incident during testing: the uninstaller reported exit
        code 0, but %APPDATA%\\dictate was still mostly there afterward --
        most likely a file the just-exited app process hadn't fully
        released yet. A single silent DelTree attempt gives no way to tell
        "succeeded" from "failed and nobody will ever know." Both must be
        logged, and a failure must be retried rather than accepted as
        final on the first try."""
        text = self._installer_text()
        self.assertIn("function DelTreeWithRetry", text)
        self.assertIn("Log(", text)
        retry_body = text.split("function DelTreeWithRetry", 1)[1].split(
            "procedure CurUninstallStepChanged", 1
        )[0]
        self.assertIn("Sleep(", retry_body)
        self.assertIn("for Attempt := 1 to", retry_body)


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
        self.assertTrue(migrated.live_preview_enabled)
        self.assertFalse(migrated.enhanced_preview_enabled)
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

    def test_activity_surface_is_always_opaque(self):
        self.assertEqual(theme.colors(True)["surface"].alpha(), 255)
        self.assertEqual(theme.colors(False)["surface"].alpha(), 255)

    def test_theme_watcher_only_emits_a_color_mode_change(self):
        watcher = theme.ThemeWatcher.__new__(theme.ThemeWatcher)
        watcher._dark = True
        changed = []
        watcher.changed = Mock(emit=lambda v: changed.append(v))
        with patch.object(theme, "system_is_dark", return_value=False):
            watcher._check()
        self.assertEqual(changed, [False])
        self.assertFalse(watcher.dark)


class HotkeyTests(unittest.TestCase):
    def _hotkeys(
        self,
        *,
        ptt_key: str = "f9",
        settings_hotkey: str = "ctrl+alt+d",
        tap_to_lock: bool = False,
    ):
        """A listener plus the callbacks it fired, in order.

        ``tap_to_lock`` defaults off so a test that only cares about combo
        mechanics can press and release in the same breath without that
        counting as the tap-to-lock gesture.
        """
        events: list[tuple[str, float | None]] = []
        listener = hotkeys.Hotkeys(
            config.Settings(
                ptt_key=ptt_key,
                settings_hotkey=settings_hotkey,
                tap_to_lock=tap_to_lock,
            ),
            on_talk_start=lambda: events.append(("start", None)),
            on_talk_end=lambda duration: events.append(("end", duration)),
            on_settings=lambda: events.append(("settings", None)),
            on_talk_lock=lambda: events.append(("lock", None)),
            on_talk_cancel=lambda: events.append(("cancel", None)),
        )
        return listener, events

    @staticmethod
    def _make_it_a_hold(listener) -> None:
        """Backdate the press so the next release reads as a hold, not a tap."""
        listener._press_time -= hotkeys.MIN_HOLD_SECONDS + 0.1

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

    # --- tap to lock ---

    def test_tap_locks_recording_instead_of_ending_it(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener._release("f9")

        self.assertEqual([event[0] for event in events], ["start", "lock"])
        self.assertTrue(listener.is_locked())

    def test_second_tap_ends_a_locked_recording(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener._release("f9")
        listener._press("f9")
        listener._release("f9")

        self.assertEqual([event[0] for event in events], ["start", "lock", "end"])
        self.assertFalse(listener.is_locked())

    def test_locked_duration_covers_the_whole_capture(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener._release("f9")
        # Stand in for the user talking for a while before tapping to finish.
        listener._press_time -= 4.0
        listener._press("f9")

        end = [event for event in events if event[0] == "end"][0]
        self.assertGreaterEqual(end[1], 4.0)

    def test_holding_still_ends_on_release_when_lock_is_enabled(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        self._make_it_a_hold(listener)
        listener._release("f9")

        self.assertEqual([event[0] for event in events], ["start", "end"])
        self.assertFalse(listener.is_locked())

    def test_tap_does_not_lock_when_the_setting_is_off(self):
        listener, events = self._hotkeys(tap_to_lock=False)
        listener._press("f9")
        listener._release("f9")

        self.assertEqual([event[0] for event in events], ["start", "end"])
        self.assertFalse(listener.is_locked())

    def test_escape_cancels_a_locked_recording(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener._release("f9")
        listener._press("esc")

        self.assertEqual([event[0] for event in events], ["start", "lock", "cancel"])
        self.assertFalse(listener.is_locked())

    def test_escape_is_ignored_when_nothing_is_locked(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("esc")
        listener._release("esc")

        self.assertEqual(events, [])

    def test_escape_bound_as_the_talk_key_still_records(self):
        listener, events = self._hotkeys(ptt_key="esc", tap_to_lock=True)
        listener._press("esc")
        listener._release("esc")
        listener._press("esc")

        # Ending it, not cancelling it: Esc is the talk key here.
        self.assertEqual([event[0] for event in events], ["start", "lock", "end"])

    def test_cancel_lock_stops_the_current_press_from_locking(self):
        """The app calls this when it could not actually open the microphone."""
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener.cancel_lock()
        listener._release("f9")

        self.assertEqual([event[0] for event in events], ["start", "end"])
        self.assertFalse(listener.is_locked())

    def test_release_lock_ends_the_capture_for_the_time_limit(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener._release("f9")
        listener.release_lock()

        self.assertEqual([event[0] for event in events], ["start", "lock", "end"])
        self.assertFalse(listener.is_locked())

    def test_a_later_tap_can_lock_again_after_one_was_cancelled(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener._release("f9")
        listener._press("esc")          # cancel the first one
        listener._press("f9")
        listener._release("f9")

        self.assertEqual(
            [event[0] for event in events], ["start", "lock", "cancel", "start", "lock"]
        )
        self.assertTrue(listener.is_locked())

    def test_rebinding_the_talk_key_drops_a_locked_recording(self):
        listener, events = self._hotkeys(tap_to_lock=True)
        listener._press("f9")
        listener._release("f9")
        listener.set_capture_active(True)

        self.assertEqual([event[0] for event in events], ["start", "lock", "cancel"])
        self.assertFalse(listener.is_locked())

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
        app._gpu_download_showing = False
        app._update_showing = False
        app.settings = config.Settings()
        app.meter = Mock()
        app.mic = Mock()
        app.cues = Mock()
        app.bar = Mock()
        app.meter_timer = Mock()
        app.live_preview_timer = Mock()
        app._preview_generation = 0
        app._preview_running = False
        app.lock_limit_timer = Mock()
        app.hotkeys = Mock()
        app.engine = Mock()
        app.tray = Mock()
        app.act_check_update = Mock()
        app.updater = Mock()
        app.settings_window = None
        return app, main

    def test_gpu_status_shows_bar_loading_and_clears_on_finish(self):
        app, _main = self._app()

        app._on_gpu_status(True, 0.42)
        app.bar.set_state.assert_called_once_with(
            "loading", "Downloading GPU acceleration… 42%", 0.42
        )
        self.assertTrue(app._gpu_download_showing)

        app.bar.reset_mock()
        app._on_gpu_status(False, None)
        app.bar.set_state.assert_called_once_with("loaded", "GPU acceleration ready")
        self.assertFalse(app._gpu_download_showing)

    def test_gpu_status_never_touches_the_bar_during_a_real_dictation(self):
        app, _main = self._app()
        app._dictation_active = True

        app._on_gpu_status(True, 0.5)

        app.bar.set_state.assert_not_called()
        self.assertFalse(app._gpu_download_showing)

    def test_gpu_status_finish_is_a_no_op_when_never_shown(self):
        """A download that finishes instantly (files already present) must
        not fire a spurious "loaded" toast nobody saw the start of."""
        app, _main = self._app()

        app._on_gpu_status(False, None)

        app.bar.set_state.assert_not_called()

    def test_update_busy_states_show_on_the_bar(self):
        app, main = self._app()

        app.updater.last_status = (main.updater_mod.CHECKING, "Checking for updates…", None)
        app._on_update_status_changed()
        app.bar.set_state.assert_called_once_with("loading", "Checking for updates…", None)
        self.assertTrue(app._update_showing)

        app.bar.reset_mock()
        app.updater.last_status = (
            main.updater_mod.DOWNLOADING,
            "Downloading update 1.2.3 — 10%",
            0.10,
        )
        app._on_update_status_changed()
        app.bar.set_state.assert_called_once_with(
            "loading", "Downloading update 1.2.3 — 10%", 0.10
        )

    def test_update_terminal_state_clears_the_flag_without_a_second_bar_call(self):
        """UP_TO_DATE/ERROR/AVAILABLE each already have their own dedicated
        callback (_on_update_current/_on_update_error/_on_update_available)
        that shows the right toast -- this handler must only stop tracking,
        never duplicate that with its own bar.set_state call."""
        app, main = self._app()
        app._update_showing = True

        app.updater.last_status = (main.updater_mod.UP_TO_DATE, "You're on the latest version", None)
        app._on_update_status_changed()

        app.bar.set_state.assert_not_called()
        self.assertFalse(app._update_showing)

    def test_update_status_never_touches_the_bar_during_a_real_dictation(self):
        app, main = self._app()
        app._dictation_active = True

        app.updater.last_status = (main.updater_mod.DOWNLOADING, "Downloading update…", 0.5)
        app._on_update_status_changed()

        app.bar.set_state.assert_not_called()

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

    def test_disabled_live_preview_does_not_start_the_preview_timer(self):
        app, main = self._app()
        app.engine.state = main.engine_mod.READY
        app.settings.live_preview_enabled = False

        app._start_listening()

        app.live_preview_timer.start.assert_not_called()

    def test_turning_live_preview_off_during_recording_stops_and_hides_it(self):
        from dataclasses import replace

        app, _main = self._app()
        app._dictation_active = True
        disabled = replace(app.settings, live_preview_enabled=False)

        app._apply_settings(disabled)

        app.live_preview_timer.stop.assert_called_once()
        self.assertEqual(app._preview_generation, 1)
        app.bar.update_settings.assert_called_once_with(disabled)

    def test_toggling_auto_update_syncs_updater_and_tray_action_live(self):
        """Every way of triggering an update check -- Settings' own button,
        the tray action, the console command -- must agree with the toggle.
        Settings' button already disables itself reactively; the tray
        action has no such polling loop, so _apply_settings has to push the
        new enabled state to it directly the moment the toggle flips."""
        from dataclasses import replace

        app, _main = self._app()
        disabled = replace(app.settings, auto_update_enabled=False)
        app._apply_settings(disabled)
        app.updater.set_enabled.assert_called_once_with(False)
        app.act_check_update.setEnabled.assert_called_once_with(False)

        app.updater.reset_mock()
        app.act_check_update.reset_mock()
        enabled = replace(disabled, auto_update_enabled=True)
        app._apply_settings(enabled)
        app.updater.set_enabled.assert_called_once_with(True)
        app.act_check_update.setEnabled.assert_called_once_with(True)

    def test_enabling_enhanced_preview_reports_hardware_limits(self):
        from dataclasses import replace

        app, main = self._app()
        enabled = replace(app.settings, enhanced_preview_enabled=True)

        with patch.object(main, "preview_hardware", return_value=(4, 8.0, True)):
            app._apply_settings(enabled)

        app.bar.notify.assert_called_once_with(
            "Enhanced preview may be slow.",
            tone="info",
            on_click=None,
            duration_ms=5000,
        )
        app.tray.showMessage.assert_not_called()
        self.assertTrue(app._enhanced_benchmark_pending)

    def test_windows_notifications_use_the_same_notice_text(self):
        app, main = self._app()
        app.settings.system_notifications_enabled = True

        app._notify("Update download failed.", tone="error")

        app.bar.notify.assert_called_once_with(
            "Update download failed.", tone="error", on_click=None, duration_ms=5000
        )
        app.tray.showMessage.assert_called_once_with(
            main.APP_NAME,
            "Update download failed.",
            main.QSystemTrayIcon.Warning,
            5000,
        )

    def test_warmup_does_not_replace_the_listening_bar(self):
        app, main = self._app()
        app.engine.state = main.engine_mod.UNLOADED
        app._start_listening()
        app.bar.reset_mock()

        app._on_engine_state(main.engine_mod.LOADING, "small.en on CPU")
        app._on_engine_state(main.engine_mod.READY, "small.en on CPU")

        app.bar.set_state.assert_not_called()
        self.assertFalse(app._ptt_preload_pending)

    def test_model_download_during_talk_explains_recording_continues(self):
        app, main = self._app()
        app._dictation_active = True

        app._on_engine_state(
            main.engine_mod.LOADING,
            "Downloading speech model — small.en on CPU — 42%",
            0.42,
        )

        app.bar.set_state.assert_called_once_with(
            "loading", "Recording continues — speech model downloading 42%", 0.42
        )

    def test_first_run_opens_settings_and_starts_the_default_model_download(self):
        app, main = self._app()
        dialog = Mock()
        dialog.exec.return_value = True
        dialog.input_device = ""
        app._apply_settings = Mock()
        app._show_settings = Mock()

        with patch.object(main, "FirstRunDialog", return_value=dialog), patch.object(
            main.config, "save"
        ) as save:
            app._show_first_run()

        saved = save.call_args.args[0]
        self.assertTrue(saved.onboarding_complete)
        self.assertEqual(saved.model_size, "small.en")
        self.assertEqual(saved.device, "cpu")
        app._apply_settings.assert_called_once_with(saved)
        app._show_settings.assert_called_once()
        app.engine.preload.assert_called_once()

    def test_update_completion_connects_the_rendered_window_signal(self):
        app, main = self._app()
        app._update_notice = "A few fixes."
        dialog = Mock()

        with patch.object(
            main, "UpdateCompleteDialog", return_value=dialog
        ) as update_dialog:
            app._show_update_complete()

        update_dialog.assert_called_once_with(main.VERSION, "A few fixes.")
        dialog.presented.connect.assert_called_once_with(main._signal_updated_window_ready)
        dialog.exec.assert_called_once()

    def test_failed_microphone_does_not_warm_the_model(self):
        app, main = self._app()
        app.engine.state = main.engine_mod.UNLOADED
        app.mic.start.side_effect = OSError("not available")

        app._start_listening()

        app.engine.preload.assert_not_called()
        self.assertFalse(app._dictation_active)
        # A tap that opened no microphone must not leave a lock behind.
        app.hotkeys.cancel_lock.assert_called_once()

    def test_a_press_while_busy_cannot_lock_a_recording(self):
        app, main = self._app()
        app._busy = True

        app._start_listening()

        app.mic.start.assert_not_called()
        app.hotkeys.cancel_lock.assert_called_once()


class UndoLastPasteTests(unittest.TestCase):
    """Undo must refuse on any evidence the paste is no longer the last change.

    Dictate cannot read another application's undo history, so these gates are
    the entire safety story: a refused undo costs the user one re-selection, a
    wrong one silently destroys work Dictate never created.
    """

    TARGET = 4242

    def _app(self):
        import main

        app = main.App.__new__(main.App)
        app._undo_target = self.TARGET
        app._undo_at = main.time.monotonic()
        app.hotkeys = Mock()
        app.hotkeys.watched_hits.return_value = 0
        app.bar = Mock()
        app.settings = config.Settings()
        app.tray = Mock()
        app.act_undo = Mock()
        app.undo_expiry_timer = Mock()
        return app, main

    def test_a_clean_undo_goes_to_the_recorded_window(self):
        app, main = self._app()
        with patch.object(main.inject, "window_is_alive", return_value=True), patch.object(
            main.inject, "undo_in", return_value=True
        ) as undo_in:
            app._undo_last_paste()

        undo_in.assert_called_once_with(self.TARGET)

    def test_typing_in_that_window_since_blocks_the_undo(self):
        app, main = self._app()
        app.hotkeys.watched_hits.return_value = 3
        with patch.object(main.inject, "window_is_alive", return_value=True), patch.object(
            main.inject, "undo_in"
        ) as undo_in:
            app._undo_last_paste()

        undo_in.assert_not_called()

    def test_an_expired_offer_blocks_the_undo(self):
        app, main = self._app()
        app._undo_at = main.time.monotonic() - main.UNDO_WINDOW_SECONDS - 1
        with patch.object(main.inject, "window_is_alive", return_value=True), patch.object(
            main.inject, "undo_in"
        ) as undo_in:
            app._undo_last_paste()

        undo_in.assert_not_called()

    def test_a_closed_window_blocks_the_undo(self):
        app, main = self._app()
        with patch.object(main.inject, "window_is_alive", return_value=False), patch.object(
            main.inject, "undo_in"
        ) as undo_in:
            app._undo_last_paste()

        undo_in.assert_not_called()

    def test_nothing_pasted_yet_blocks_the_undo(self):
        app, main = self._app()
        app._undo_target = None
        with patch.object(main.inject, "undo_in") as undo_in:
            app._undo_last_paste()

        undo_in.assert_not_called()

    def test_undo_is_offered_only_once(self):
        app, main = self._app()
        with patch.object(main.inject, "window_is_alive", return_value=True), patch.object(
            main.inject, "undo_in", return_value=True
        ) as undo_in:
            app._undo_last_paste()
            app._undo_last_paste()

        self.assertEqual(undo_in.call_count, 1)
        self.assertIsNone(app._undo_target)

    def test_a_refusal_explains_itself_and_withdraws_the_offer(self):
        app, main = self._app()
        app.hotkeys.watched_hits.return_value = 1
        with patch.object(main.inject, "window_is_alive", return_value=True), patch.object(
            main.inject, "undo_in"
        ):
            app._undo_last_paste()

        app.bar.notify.assert_called_once()
        self.assertIn("You changed that app", app.bar.notify.call_args[0][0])
        self.assertIsNone(app._undo_target)

    def test_arming_undo_watches_the_pasted_window(self):
        app, main = self._app()
        app._undo_target = None

        app._offer_undo(99)

        self.assertEqual(app._undo_target, 99)
        app.hotkeys.watch_window.assert_called_once()
        self.assertEqual(app.hotkeys.watch_window.call_args[0][0], 99)
        app.act_undo.setEnabled.assert_called_with(True)

    def test_the_synthetic_undo_keystroke_is_hidden_from_our_own_listener(self):
        app, main = self._app()
        with patch.object(main.inject, "window_is_alive", return_value=True), patch.object(
            main.inject, "undo_in", return_value=True
        ):
            app._undo_last_paste()

        self.assertEqual(
            [call.args[0] for call in app.hotkeys.suppress.call_args_list], [True, False]
        )


class WindowActivityWatchTests(unittest.TestCase):
    """Only input that reached the watched window counts against it."""

    def _listener(self, focused):
        events = []
        listener = hotkeys.Hotkeys(
            config.Settings(),
            on_talk_start=lambda: events.append("start"),
            on_talk_end=lambda held: events.append("end"),
            on_settings=lambda: None,
        )
        listener.watch_window(7, lambda: focused["hwnd"])
        return listener

    def test_typing_into_the_watched_window_counts(self):
        focused = {"hwnd": 7}
        listener = self._listener(focused)
        listener._press("a")
        listener._press("b")

        self.assertEqual(listener.watched_hits(), 2)

    def test_typing_into_a_different_window_does_not_count(self):
        """Keystrokes in another app cannot disturb this one's undo history."""
        focused = {"hwnd": 999}
        listener = self._listener(focused)
        listener._press("a")
        listener._press("b")

        self.assertEqual(listener.watched_hits(), 0)

    def test_opening_dictates_own_menu_does_not_count(self):
        """The click that reaches the tray menu never lands in the target."""
        focused = {"hwnd": 7}
        listener = self._listener(focused)
        focused["hwnd"] = 555  # focus moves to Dictate's menu
        listener._press("mouse1")

        self.assertEqual(listener.watched_hits(), 0)

    def test_our_own_synthetic_keystrokes_do_not_count(self):
        focused = {"hwnd": 7}
        listener = self._listener(focused)
        listener.suppress(True)
        listener._press("ctrl")
        listener._press("v")
        listener.suppress(False)

        self.assertEqual(listener.watched_hits(), 0)

    def test_an_unreadable_foreground_window_counts_as_activity(self):
        """Failing closed: an unknown state must refuse the undo, not allow it."""
        def broken():
            raise OSError("no window")

        listener = hotkeys.Hotkeys(
            config.Settings(),
            on_talk_start=lambda: None,
            on_talk_end=lambda held: None,
            on_settings=lambda: None,
        )
        listener.watch_window(7, broken)
        listener._press("a")

        self.assertEqual(listener.watched_hits(), 1)

    def test_nothing_is_counted_before_a_window_is_watched(self):
        listener = hotkeys.Hotkeys(
            config.Settings(),
            on_talk_start=lambda: None,
            on_talk_end=lambda held: None,
            on_settings=lambda: None,
        )
        listener._press("a")

        self.assertEqual(listener.watched_hits(), 0)

    def test_stopping_the_watch_clears_the_count(self):
        focused = {"hwnd": 7}
        listener = self._listener(focused)
        listener._press("a")
        listener.stop_watching()
        listener._press("b")

        self.assertEqual(listener.watched_hits(), 0)


class ConsoleCommandTests(unittest.TestCase):
    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app.settings = config.Settings()
        app.engine = Mock()
        app.engine.state = "ready"
        app.engine.active_device = "cpu"
        app.updater = Mock()
        app.cues = Mock()
        app.hotkeys = Mock()
        app.tray = Mock()
        app.qt = Mock()
        app.bridge = Mock()
        app._reload_pending = False
        app._show_settings = Mock()
        return app, main

    def test_status_and_gpu_and_version_print_without_error(self):
        app, main = self._app()
        with patch.object(main.engine_mod, "cuda_available", return_value=True), patch.object(
            main.gpu_runtime, "is_installed", return_value=False
        ):
            app._on_command("status")
            app._on_command("gpu")
            app._on_command("version")
        # Nothing here has an assertion beyond "did not raise" -- these are
        # plain diagnostic prints, the same contract as the original single
        # -line status/help output they replaced.

    def test_check_update_respects_the_settings_toggle(self):
        app, main = self._app()
        app.settings.auto_update_enabled = False
        app._on_command("check update")
        app.updater.check_now.assert_not_called()

        app.settings.auto_update_enabled = True
        app._on_command("check update")
        app.updater.check_now.assert_called_once_with(silent=False)

    def test_settings_command_opens_the_settings_window(self):
        app, _main = self._app()
        app._on_command("settings")
        app._show_settings.assert_called_once()

    def test_quit_command_runs_the_normal_shutdown_path(self):
        app, _main = self._app()
        app._on_command("quit")
        app.hotkeys.stop.assert_called_once()
        app.engine.shutdown.assert_called_once()
        app.updater.shutdown.assert_called_once()
        app.tray.hide.assert_called_once()
        app.qt.quit.assert_called_once()

    def test_open_data_opens_the_settings_folder(self):
        app, main = self._app()
        with patch.object(main.os, "startfile") as startfile:
            app._on_command("open data")
        startfile.assert_called_once_with(config.CONFIG_DIR)

    def test_help_lists_every_command_once(self):
        app, main = self._app()
        with patch("builtins.print") as mock_print:
            app._on_command("help")
        printed = "\n".join(call.args[0] for call in mock_print.call_args_list)
        for name, _desc in main.CONSOLE_COMMANDS:
            self.assertIn(name, printed)


class StopListeningTests(unittest.TestCase):
    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app._busy = False
        app._dictation_active = True
        app._start_cue_at = 0.0
        app.bridge = Mock()
        app.meter = Mock()
        app.mic = Mock()
        app.cues = Mock()
        app.bar = Mock()
        app.meter_timer = Mock()
        app.live_preview_timer = Mock()
        app._preview_generation = 0
        app._preview_running = False
        app.lock_limit_timer = Mock()
        app.hotkeys = Mock()
        app.engine = Mock()
        app.settings_window = None
        return app, main

    def test_cancelling_a_locked_recording_discards_the_audio(self):
        app, main = self._app()

        app._cancel_listening()

        app.mic.stop.assert_called_once()
        app.engine.transcribe.assert_not_called()
        app.bar.set_state.assert_called_once_with("idle")
        self.assertFalse(app._dictation_active)

    def test_the_time_limit_finishes_a_locked_recording(self):
        app, main = self._app()

        app._on_lock_limit()

        # Ends it rather than throwing it away -- the words already spoken
        # should still land.
        app.hotkeys.release_lock.assert_called_once()

    def test_a_lock_with_no_open_microphone_is_dropped(self):
        app, main = self._app()
        app._dictation_active = False

        app._on_talk_locked()

        app.hotkeys.cancel_lock.assert_called_once()
        app.lock_limit_timer.start.assert_not_called()

    def test_locking_starts_the_time_limit(self):
        app, main = self._app()

        app._on_talk_locked()

        app.lock_limit_timer.start.assert_called_once()

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


class LivePreviewTests(unittest.TestCase):
    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app._dictation_active = True
        app._preview_generation = 4
        app._preview_running = False
        app._preview_last_request_at = 0.0
        app._preview_last_voice_at = 0.0
        app._preview_was_speaking = False
        app._enhanced_benchmark_pending = False
        app.settings = config.Settings()
        app.mic = Mock()
        app.mic.latest_window.return_value = np.ones(512, dtype=np.float32) * 0.2
        app.engine = Mock()
        app.preview_engine = Mock()
        app.bar = Mock()
        app.bridge = Mock()
        app.tray = Mock()
        return app, main

    def test_current_preview_reaches_the_integrated_bar_card(self):
        app, _main = self._app()
        app._preview_running = True

        app._on_live_preview(4, "smooth words")

        self.assertFalse(app._preview_running)
        app.bar.set_live_text.assert_called_once_with("smooth words")

    def test_preview_from_a_finished_recording_is_ignored(self):
        app, _main = self._app()
        app._preview_running = True
        app._preview_generation = 5

        app._on_live_preview(4, "stale words")

        app.bar.set_live_text.assert_not_called()

    def test_only_one_preview_inference_can_be_in_flight(self):
        app, _main = self._app()
        app._preview_running = True

        app._request_live_preview()

        app.mic.snapshot.assert_not_called()
        app.engine.transcribe_preview.assert_not_called()

    def test_pause_edge_requests_a_preview_before_the_regular_interval(self):
        app, main = self._app()
        now = time.perf_counter()
        app._preview_last_request_at = now - 0.30
        app._preview_last_voice_at = now - 0.20
        app._preview_was_speaking = True
        app.mic.latest_window.return_value = np.zeros(512, dtype=np.float32)
        app.mic.snapshot.return_value = np.ones(
            int(audio.SAMPLE_RATE * 0.8), dtype=np.float32
        ) * 0.2

        class ImmediateThread:
            def __init__(self, target, daemon=True):
                self.target = target

            def start(self):
                self.target()

        app.engine.transcribe_preview.return_value = "pause words"
        with patch.object(main.threading, "Thread", ImmediateThread):
            app._request_live_preview()

        app.engine.transcribe_preview.assert_called_once()
        app.bridge.live_preview.emit.assert_called_once()

    def test_enhanced_preview_uses_the_dedicated_engine(self):
        app, main = self._app()
        app.settings.enhanced_preview_enabled = True
        app._preview_last_request_at = time.perf_counter() - 1.0
        app.mic.snapshot.return_value = np.ones(
            int(audio.SAMPLE_RATE * 0.8), dtype=np.float32
        ) * 0.2
        app.preview_engine.transcribe.return_value = ("enhanced words", 0.3)

        class ImmediateThread:
            def __init__(self, target, daemon=True):
                self.target = target

            def start(self):
                self.target()

        with patch.object(main.threading, "Thread", ImmediateThread):
            app._request_live_preview()

        app.preview_engine.transcribe.assert_called_once()
        app.engine.transcribe_preview.assert_not_called()
        app.bridge.live_preview.emit.assert_called_once_with(
            4, "enhanced words", 0.3, True
        )

    def test_slow_enhanced_benchmark_shows_a_hardware_limit_notification(self):
        app, _main = self._app()
        app._preview_running = True
        app._enhanced_benchmark_pending = True

        app._on_live_preview(4, "words", 1.2, True)

        app.bar.notify.assert_called_once_with(
            "Enhanced preview may be slow.",
            tone="info",
            on_click=None,
            duration_ms=5000,
        )
        app.tray.showMessage.assert_not_called()

    def test_disabled_preview_never_reads_the_live_microphone_buffer(self):
        app, _main = self._app()
        app.settings.live_preview_enabled = False

        app._request_live_preview()

        app.mic.snapshot.assert_not_called()
        app.engine.transcribe_preview.assert_not_called()


class LastDictationTests(unittest.TestCase):
    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app._last_dictation = ""
        app.act_copy_last = Mock()
        app.bar = Mock()
        app.settings = config.Settings()
        app.tray = Mock()
        app._clipboard_restore = None
        app._clipboard_restore_timer = Mock()
        app._clipboard_restore_timer.isActive.return_value = False
        return app, main

    def test_successful_dictation_enables_one_local_recovery_copy(self):
        app, _main = self._app()

        app._remember_last_dictation("hello there")

        self.assertEqual(app._last_dictation, "hello there")
        app.act_copy_last.setEnabled.assert_called_once_with(True)

    def test_copy_last_schedules_a_five_second_restore(self):
        app, main = self._app()
        app._last_dictation = "hello there"
        restore = Mock(return_value=True)
        with patch.object(main.inject, "copy_temporarily", return_value=restore):
            app._copy_last_dictation()

        self.assertIs(app._clipboard_restore, restore)
        app._clipboard_restore_timer.start.assert_called_once_with(
            main.inject.TEMPORARY_COPY_SECONDS * 1000
        )
        app.bar.notify.assert_called_once_with(
            "Last dictation copied.",
            tone="info",
            on_click=None,
            duration_ms=main.inject.TEMPORARY_COPY_SECONDS * 1000,
        )

    def test_copy_last_restarts_without_losing_the_original_clipboard(self):
        app, main = self._app()
        previous_restore = Mock(return_value=True)
        new_restore = Mock(return_value=True)
        app._clipboard_restore = previous_restore
        app._clipboard_restore_timer.isActive.return_value = True
        with patch.object(main.inject, "copy_temporarily", return_value=new_restore):
            app._copy_last_dictation()

        app._clipboard_restore_timer.stop.assert_called_once()
        previous_restore.assert_called_once()
        self.assertIs(app._clipboard_restore, new_restore)

    def test_copy_last_refuses_when_the_existing_clipboard_cannot_be_protected(self):
        app, main = self._app()
        app._last_dictation = "hello there"
        with patch.object(main.inject, "copy_temporarily", return_value=None):
            app._copy_last_dictation()

        app.bar.set_state.assert_called_once_with(
            "error", "Couldn't safely protect your clipboard"
        )


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(
            ["dictate-tests", "-platform", "offscreen"]
        )

    def test_stylesheet_has_only_the_solid_light_or_dark_root(self):
        import settings_window

        dark = settings_window.stylesheet(dark=True)
        light = settings_window.stylesheet(dark=False)
        self.assertIn("QWidget#root { background: #202020; }", dark)
        self.assertIn("QWidget#root { background: #F3F3F3; }", light)
        self.assertNotIn("QWidget#root { background: transparent; }", dark)
        self.assertNotIn("rgba(43, 43, 43, 174)", dark)

    def test_apply_native_chrome_sets_only_color_and_corner_attributes(self):
        import settings_window

        calls = []

        class FakeDwm:
            def DwmSetWindowAttribute(self, hwnd, attribute, value_ptr, size):
                calls.append(attribute)
                return 0

        with patch.object(settings_window.ctypes, "windll", MagicMock(dwmapi=FakeDwm())):
            settings_window.apply_native_chrome(12345, dark=True)
        self.assertEqual(
            calls,
            [
                settings_window.DWMWA_USE_IMMERSIVE_DARK_MODE,
                settings_window.DWMWA_WINDOW_CORNER_PREFERENCE,
            ],
        )

    def test_settings_window_uses_an_opaque_background(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)
            gpu_status = (False, None)

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=False),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
            self.assertFalse(window.testAttribute(settings_window.Qt.WA_TranslucentBackground))
            self.assertNotIn("QWidget#root { background: transparent; }", window.styleSheet())
        window.close()

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
        self.assertTrue(window.live_preview_check.isChecked())
        self.assertEqual(first_run.input_device, microphone.key)
        privacy_text = " ".join(label.text() for label in privacy.findChildren(QLabel))
        self.assertIn("Your voice stays on this PC", privacy_text)
        self.assertIn("Hugging Face", privacy_text)
        self.assertIn("does not print", privacy_text)
        self.assertNotIn(str(config.CONFIG_PATH), window.save_status.text())

        privacy.close()
        first_run.close()
        window.close()

    def test_words_i_use_dialog_keeps_one_clean_phrase_per_line(self):
        import settings_window

        dialog = settings_window.VocabularyDialog(["Northwind Studio", "CTranslate2"])
        dialog.editor.setPlainText("Northwind   Studio\nnorthwind studio\nSpringfield")
        self.assertEqual(dialog.vocabulary, ["Northwind Studio", "Springfield"])
        dialog.close()

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

    def test_live_preview_toggle_saves_and_collects_off(self):
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
            patch.object(settings_window.config, "save") as save,
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
            window.live_preview_check.setChecked(False)
            window._save_timer.stop()
            window._save_now()

            self.assertFalse(window._settings.live_preview_enabled)
            self.assertFalse(window._collect_settings().live_preview_enabled)
            save.assert_called_once()
        window.close()

    def test_enhanced_preview_is_nested_under_and_disabled_with_live_preview(self):
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

        self.assertTrue(window.enhanced_preview_check.isEnabled())
        self.assertIn(
            "Enhanced preview (Alpha)",
            [label.text() for label in window.findChildren(settings_window.QLabel)],
        )
        window.enhanced_preview_check.setChecked(True)
        self.assertTrue(window._collect_settings().enhanced_preview_enabled)
        window.live_preview_check.setChecked(False)
        self.assertFalse(window.enhanced_preview_check.isEnabled())
        window.close()

    def test_color_mode_control_persists_and_applies_a_solid_panel(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)
            gpu_status = (False, None)

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=False),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window, "system_is_dark", return_value=True),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
            window.theme_box.setCurrentIndex(window.theme_box.findData("light"))

        chosen = window._collect_settings()
        self.assertEqual(chosen.theme_mode, "light")
        self.assertFalse(window.testAttribute(settings_window.Qt.WA_TranslucentBackground))
        self.assertIn("QFrame#settingsGroup { background: #FFFFFF;", window.styleSheet())
        window.close()

    def test_first_model_download_is_visible_in_settings_without_a_settings_reload(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.LOADING
            active_device = ""
            last_status = (engine.LOADING, "Downloading Small English · CPU · 42%", 0.42)
            gpu_status = (False, None)

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=False),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
            window.refresh_status()

        self.assertFalse(window.reload_progress.isHidden())
        self.assertEqual(window.reload_progress.value(), 42)
        self.assertFalse(window.model_download_row.isHidden())
        self.assertEqual(window.model_download_progress.value(), 42)
        self.assertIn("Small English", window.save_status.text())
        self.assertNotIn("small.en", window.save_status.text())
        self.assertEqual(window._download_overview_mode, "active")
        self.assertEqual(window.download_overview_detail.text(), "Small English · 42%")
        self.assertTrue(window.download_overview_status.property("active"))
        self.assertFalse(window.download_overview_details.isHidden())
        self.assertFalse(window.download_overview_progress.isHidden())
        self.assertEqual(window.download_overview_progress.value(), 42)
        with patch.object(window, "_scroll_to_widget") as scroll_to:
            window.download_overview.click()
        scroll_to.assert_called_once_with(window.model_download_row)
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

    def test_download_overview_opens_gpu_setup_from_the_header(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)
            gpu_status = (False, None)

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.gpu_runtime, "needs_download", return_value=True),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
            window.refresh_status()

        self.assertEqual(window._download_focus, "gpu")
        self.assertEqual(window._download_overview_mode, "ready")
        self.assertEqual(window.download_overview_title.text(), "GPU acceleration ready")
        self.assertTrue(window.download_overview_details.isHidden())
        with (
            patch.object(window, "_scroll_to_widget") as scroll_to,
            patch.object(settings_window.QTimer, "singleShot") as schedule,
        ):
            window.download_overview.click()

        self.assertTrue(window.advanced_btn.isChecked())
        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.args[0], settings_window.ENTER_MS + 30)
        self.assertTrue(callable(schedule.call_args.args[1]))
        scroll_to.assert_not_called()  # the jump is intentionally queued until expansion finishes
        window.close()

    def test_gpu_download_row_hidden_when_files_already_present(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)
            gpu_status = (False, None)

            def preload(self):
                pass

            def start_gpu_download(self):
                raise AssertionError("should not be called when nothing needs downloading")

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.gpu_runtime, "needs_download", return_value=False),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
        self.assertTrue(window.gpu_download_row.isHidden())
        self.assertTrue(window.gpu_download_progress_row.isHidden())
        window.close()

    def test_gpu_download_button_click_starts_download_and_disables_itself(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)
            gpu_status = (False, None)
            start_calls = 0

            def preload(self):
                pass

            def start_gpu_download(self):
                DummyEngine.start_calls += 1

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.gpu_runtime, "needs_download", return_value=True),
        ):
            window = settings_window.SettingsWindow(
                config.Settings(device="cpu"), DummyEngine()
            )
        # device is "cpu" so window construction itself must not have
        # auto-started a download -- only the explicit button does that here.
        self.assertEqual(DummyEngine.start_calls, 0)
        self.assertFalse(window.gpu_download_row.isHidden())
        self.assertTrue(window.gpu_download_btn.isEnabled())

        window.gpu_download_btn.click()
        self.assertEqual(DummyEngine.start_calls, 1)
        self.assertFalse(window.gpu_download_btn.isEnabled())
        self.assertEqual(window.gpu_download_btn.text(), "Downloading…")
        window.close()

    def test_gpu_processing_and_gpu_modes_stay_disabled_until_runtime_is_installed(self):
        """The explicit download button is the only path that installs GPU files."""
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)
            gpu_status = (False, None)
            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.gpu_runtime, "needs_download", return_value=True),
        ):
            window = settings_window.SettingsWindow(
                config.Settings(device="cpu"), DummyEngine()
            )
            gpu_item = window.device_box.model().item(window.device_box.findData("cuda"))
            faster_item = window.mode_box.model().item(window.mode_box.findData("faster"))
            max_item = window.mode_box.model().item(window.mode_box.findData("max"))
            self.assertFalse(gpu_item.isEnabled())
            self.assertFalse(faster_item.isEnabled())
        self.assertFalse(max_item.isEnabled())
        window.close()

    def test_gpu_processing_and_modes_enable_after_the_runtime_is_ready(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)
            gpu_status = (False, None)

            def preload(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.gpu_runtime, "needs_download", return_value=False),
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
        gpu_item = window.device_box.model().item(window.device_box.findData("cuda"))
        faster_item = window.mode_box.model().item(window.mode_box.findData("faster"))
        max_item = window.mode_box.model().item(window.mode_box.findData("max"))
        self.assertTrue(gpu_item.isEnabled())
        self.assertTrue(faster_item.isEnabled())
        self.assertTrue(max_item.isEnabled())
        window.close()

    def test_refresh_status_shows_live_gpu_download_progress(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.READY
            active_device = "cpu"
            last_status = (engine.READY, "tiny.en on CPU", None)
            gpu_status = (True, 0.55)

            def preload(self):
                pass

            def start_gpu_download(self):
                pass

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.gpu_runtime, "needs_download", return_value=True),
        ):
            window = settings_window.SettingsWindow(
                config.Settings(device="cpu"), DummyEngine()
            )
            window.refresh_status()
        self.assertFalse(window.gpu_download_progress_row.isHidden())
        self.assertFalse(window.gpu_download_progress.isHidden())
        self.assertEqual(window.gpu_download_progress.value(), 55)
        self.assertFalse(window.gpu_download_btn.isEnabled())
        self.assertIn("55%", window.gpu_download_desc_label.text())
        window.close()

    def test_privacy_navigates_in_place_instead_of_opening_a_window(self):
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
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())

        self.assertIs(window._pages.currentWidget(), window._pages.widget(0))
        window.privacy_btn.click()
        self.assertIs(window._pages.currentWidget(), window._privacy_page)
        self.assertIn(
            "stays on this PC",
            " ".join(
                label.text()
                for label in window._privacy_page.findChildren(settings_window.QLabel)
            ),
        )
        window._privacy_page.back.emit()
        self.assertIs(window._pages.currentWidget(), window._pages.widget(0))
        window.close()

    def test_github_button_opens_the_public_repo(self):
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
            patch.object(settings_window.QDesktopServices, "openUrl") as open_url,
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
            window.github_btn.click()

        open_url.assert_called_once()
        self.assertEqual(
            open_url.call_args[0][0].toString(), "https://github.com/PLEXFX/dictate"
        )
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
            last_status = (updater.IDLE, "", None)

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
            self.assertFalse(window.reload_progress.isHidden())
            self.assertEqual(window.reload_progress.value(), 42)
            self.assertTrue(window.update_progress.isHidden())

            # Updater activity takes priority over the engine's own status,
            # and disables the button while a check/download is in flight.
            dummy_updater.last_status = (
                updater.DOWNLOADING,
                "Downloading update 0.1.0-beta.3 — 10%",
                0.10,
            )
            window.refresh_status()
            self.assertIn("Downloading update", window.save_status.text())
            self.assertFalse(window.update_btn.isEnabled())
            self.assertFalse(window.update_progress.isHidden())
            self.assertEqual(window.update_progress.value(), 10)

            dummy_updater.last_status = (updater.IDLE, "", None)
            window.refresh_status()
            self.assertTrue(window.update_btn.isEnabled())
            self.assertIn("42%", window.save_status.text())
            self.assertTrue(window.update_progress.isHidden())
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
            last_status = (updater.IDLE, "", None)

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

    def test_auto_update_toggle_disables_check_button_and_saves(self):
        import engine
        import settings_window

        class DummyEngine:
            state = engine.UNLOADED
            active_device = ""
            last_status = (engine.UNLOADED, "", None)

            def preload(self):
                pass

        class DummyUpdater:
            last_status = (updater.IDLE, "", None)

        with (
            patch.object(settings_window.engine_mod, "cuda_available", return_value=True),
            patch.object(settings_window.audio_mod, "input_devices", return_value=[]),
            patch.object(settings_window.config, "save") as save,
        ):
            window = settings_window.SettingsWindow(
                config.Settings(), DummyEngine(), DummyUpdater()
            )
            window.show()

            self.assertTrue(window.auto_update_check.isChecked())
            self.assertTrue(window.update_btn.isEnabled())

            window.auto_update_check.setChecked(False)
            self.assertFalse(window.update_btn.isEnabled())
            self.assertIn("Turned off", window.update_desc_label.text())
            self.assertFalse(window._collect_settings().auto_update_enabled)

            window._save_now()
            self.assertFalse(window._settings.auto_update_enabled)
            save.assert_called_once()
        window.close()

    def test_advanced_panel_animates_open_and_closed(self):
        import bar as bar_mod
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
        ):
            window = settings_window.SettingsWindow(config.Settings(), DummyEngine())
        window.show()

        # Collapsed at construction: zero height, fully transparent, hidden.
        self.assertEqual(window.advanced_panel.maximumHeight(), 0)
        self.assertEqual(window._advanced_opacity.opacity(), 0.0)
        self.assertFalse(window.advanced_panel.isVisible())

        window.advanced_btn.setChecked(True)
        self.assertTrue(window.advanced_panel.isVisible())
        self.assertEqual(
            window._advanced_height_anim.endValue(), window.advanced_panel.sizeHint().height()
        )
        self.assertEqual(window._advanced_fade_anim.endValue(), 1.0)
        self.assertEqual(
            window._advanced_height_anim.easingCurve().type(), bar_mod.FLUENT_DECELERATE.type()
        )
        self.assertEqual(window._advanced_height_anim.duration(), bar_mod.ENTER_MS)

        window.advanced_btn.setChecked(False)
        self.assertEqual(window._advanced_height_anim.endValue(), 0)
        self.assertEqual(window._advanced_fade_anim.endValue(), 0.0)
        self.assertEqual(
            window._advanced_height_anim.easingCurve().type(), bar_mod.FLUENT_ACCELERATE.type()
        )
        self.assertEqual(window._advanced_height_anim.duration(), bar_mod.EXIT_MS)

        # Finishing a collapse hides the panel again; finishing an expand must not.
        window._on_advanced_anim_finished()
        self.assertFalse(window.advanced_panel.isVisible())
        window.close()


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
            # Held, not tapped: tap-to-lock has its own tests below.
            config.Settings(tap_to_lock=False),
            on_talk_start=lambda: events.append("start"),
            on_talk_end=lambda held: events.append("end"),
            on_settings=lambda: events.append("settings"),
        )
        listener._on_press(keyboard.Key.f9)
        listener._on_release(keyboard.Key.f9)
        self.assertEqual(events, ["start", "end"])

    def test_tapping_a_real_f9_key_event_locks_recording(self):
        """The lock gesture from real key events, not just internal names."""
        import hotkeys
        from pynput import keyboard

        events = []
        listener = hotkeys.Hotkeys(
            config.Settings(tap_to_lock=True),
            on_talk_start=lambda: events.append("start"),
            on_talk_end=lambda held: events.append("end"),
            on_settings=lambda: None,
            on_talk_lock=lambda: events.append("lock"),
        )
        listener._on_press(keyboard.Key.f9)
        listener._on_release(keyboard.Key.f9)
        self.assertEqual(events, ["start", "lock"])

        listener._on_press(keyboard.Key.f9)
        listener._on_release(keyboard.Key.f9)
        self.assertEqual(events, ["start", "lock", "end"])

    def test_f9_still_works_after_a_corrupt_binding_is_clamped(self):
        import hotkeys
        from pynput import keyboard

        events = []
        listener = hotkeys.Hotkeys(
            config.Settings(ptt_key="", tap_to_lock=False).clamped(),
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


class BarClickTests(unittest.TestCase):
    """The bar's own click gesture: a bounce that only means something (and
    only fires) while a locked recording makes it mean something."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(
            ["dictate-tests", "-platform", "offscreen"]
        )

    @staticmethod
    def _click(bar, inside: bool = True):
        from PySide6.QtCore import QPointF

        class _FakeMouseEvent:
            def __init__(self, pos):
                self._pos = pos

            def position(self):
                return self._pos

        pos = (
            QPointF(bar.width() / 2, bar.height() / 2)
            if inside
            else QPointF(-50, -50)
        )
        bar.mousePressEvent(_FakeMouseEvent(pos))
        bar.mouseReleaseEvent(_FakeMouseEvent(pos))

    def test_click_does_nothing_while_not_armed(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        fired = []
        b.clicked.connect(lambda: fired.append(1))

        self._click(b)

        self.assertEqual(fired, [])
        self.assertEqual(b._press_target, 1.0)  # never dipped -- nothing to press

    def test_click_fires_and_dips_while_armed(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        fired = []
        b.clicked.connect(lambda: fired.append(1))
        b.set_clickable(True)

        from PySide6.QtCore import QPointF

        class _FakeMouseEvent:
            def __init__(self, pos):
                self._pos = pos

            def position(self):
                return self._pos

        centre = QPointF(b.width() / 2, b.height() / 2)
        b.mousePressEvent(_FakeMouseEvent(centre))
        self.assertEqual(b._press_target, bar_mod.PRESS_DIP)

        b.mouseReleaseEvent(_FakeMouseEvent(centre))
        self.assertEqual(fired, [1])
        self.assertEqual(b._press_target, 1.0)  # springs back on release

    def test_release_outside_the_bar_does_not_fire(self):
        """A drag-off-and-release should not count as a click -- ordinary
        button semantics, and it still resets the press dip either way."""
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        fired = []
        b.clicked.connect(lambda: fired.append(1))
        b.set_clickable(True)

        self._click(b, inside=False)

        self.assertEqual(fired, [])
        self.assertEqual(b._press_target, 1.0)

    def test_set_clickable_toggles_the_cursor(self):
        import bar as bar_mod
        from PySide6.QtCore import Qt

        b = bar_mod.Bar(config.Settings())
        self.assertTrue(b.testAttribute(Qt.WA_TransparentForMouseEvents))
        b.set_clickable(True)
        self.assertEqual(b.cursor().shape(), Qt.PointingHandCursor)
        self.assertFalse(b.testAttribute(Qt.WA_TransparentForMouseEvents))
        b.set_clickable(False)
        self.assertEqual(b.cursor().shape(), Qt.ArrowCursor)
        self.assertTrue(b.testAttribute(Qt.WA_TransparentForMouseEvents))

    def test_state_change_morphs_from_the_drawn_position_not_the_target(self):
        """Regression: _apply_state used to capture _target, which can differ
        from what is actually on screen (_drawn) because the spring lags and
        overshoots -- a real, if usually small, source of a visible pop."""
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        b._drawn[:] = 0.3
        b._target[:] = 0.9  # spring hasn't caught up yet

        b._apply_state("listening", "")

        self.assertTrue((b._morph_from == 0.3).all())

    def test_listening_starts_as_a_small_empty_connected_card(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        b._apply_state("listening", "")

        self.assertTrue(b._card_active)
        self.assertEqual(b._card_target, 0.0)
        self.assertEqual((b._text_top, b._text_bottom), ("", ""))

    def test_transcript_and_bar_use_one_connected_outer_silhouette(self):
        import bar as bar_mod
        from PySide6.QtCore import QPointF

        path = bar_mod._surface_path(bar_mod.CARD_FULL_H)
        centre_x = bar_mod.SHADOW_PAD + bar_mod.PILL_W / 2

        self.assertTrue(
            path.contains(QPointF(centre_x, bar_mod.PILL_TOP + bar_mod.PILL_H / 2))
        )
        self.assertTrue(
            path.contains(
                QPointF(centre_x, bar_mod.PILL_TOP - bar_mod.CARD_FULL_H / 2)
            )
        )
        # The overlap itself belongs to the same path, so no internal border
        # or shadow can be painted through the card-to-pill connection.
        self.assertTrue(
            path.contains(QPointF(centre_x, bar_mod.PILL_TOP + 1))
        )

    def test_disabled_live_preview_has_no_card_or_text(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings(live_preview_enabled=False))
        b._apply_state("listening", "")
        b.set_live_text("this must remain hidden")

        self.assertFalse(b._card_active)
        self.assertEqual(b._card_target, 0.0)
        self.assertEqual(b._text_to, ("", ""))

    def test_live_text_expands_into_only_the_newest_two_rows(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        b._apply_state("listening", "")
        b.set_live_text(
            "This is enough dictated text to wrap across several narrow preview rows "
            "while the person continues speaking"
        )

        self.assertEqual(b._card_target, 1.0)
        self.assertIsNotNone(b._text_elapsed)
        self.assertTrue(b._text_to[1])
        # The UI contract is two rows, not an accumulating transcript view.
        self.assertEqual(len(b._text_to), 2)

    def test_one_line_uses_the_compact_height_then_grows_for_a_second(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        b._apply_state("listening", "")
        b.set_live_text("one short line")
        one_line_target = b._card_target

        self.assertGreater(one_line_target, 0.0)
        self.assertLess(one_line_target, 1.0)
        b._text_top, b._text_bottom = b._text_to
        b._text_elapsed = None
        b.set_live_text(
            "one short line with enough extra spoken words to need the history row"
        )
        self.assertEqual(b._card_target, 1.0)

    def test_latest_preview_words_remain_tentative(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        b._apply_state("listening", "")
        b.set_live_text("these settled words may still change")

        self.assertLess(b._text_confirmed_to[1], len(b._text_to[1]))

    def test_a_new_wrapped_row_uses_the_upward_line_transition(self):
        import bar as bar_mod

        b = bar_mod.Bar(config.Settings())
        b._apply_state("listening", "")
        b.set_live_text("first short line")
        b._text_top, b._text_bottom = b._text_to
        b._text_elapsed = None
        b.set_live_text(
            "first short line followed by enough additional words to create another row"
        )

        self.assertTrue(b._text_advancing)


class ToastWidthTests(unittest.TestCase):
    """The toast used to elide routine update text mid-sentence because
    MAX_W (320px) was narrower than realistic messages -- a long version
    string plus "available -- click to download & install" easily exceeds
    that. It now wraps to a second line instead; this locks in that the
    wrapped box is actually tall enough to hold the whole message, rather
    than just eyeballing it once."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(
            ["dictate-tests", "-platform", "offscreen"]
        )

    def test_realistic_update_messages_are_not_clipped(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFontMetrics

        import bar as bar_mod

        toast = bar_mod.Toast()
        messages = [
            "You're up to date.",
            "Update 0.2.10-beta.12 ready. Click to install.",
            "Ready. Hold Ctrl+Alt+F9 to talk.",
        ]
        metrics = QFontMetrics(toast._font)
        for text in messages:
            toast.show_message(text, bar_mod.QRect(0, 0, 1, 1))
            rect_w = toast.width() - toast.PAD * 2 - 16
            if metrics.horizontalAdvance(text) <= rect_w:
                continue  # fits on one line, drawn verbatim -- nothing to check
            wrapped = metrics.boundingRect(
                bar_mod.QRect(0, 0, rect_w, 10_000), Qt.TextWordWrap, text
            )
            self.assertLessEqual(
                wrapped.height(),
                toast.height(),
                msg=f"wrapped text still clipped: {text!r}",
            )


class ModelDownloadBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(
            ["dictate-tests", "-platform", "offscreen"]
        )

    def test_first_model_download_labels_the_bar_above_its_progress_fill(self):
        import bar as bar_mod

        bar = bar_mod.Bar(config.Settings())
        bar._state_since = 0.0  # bypass the transition dwell in this focused test
        bar.set_state("loading", "small.en on CPU — downloading 25%", 0.25)

        self.assertEqual(bar._state, "loading")
        self.assertEqual(bar._progress, 0.25)
        self.assertTrue(bar._loading_notice_active)
        # The toast's label is the real detail text, not a hardcoded
        # "Downloading model…" -- a GPU-runtime or update download passes
        # its own accurate detail through the exact same path (see the two
        # tests below), so a hardcoded string would have mislabeled those.
        self.assertEqual(bar._toast._text, "small.en on CPU — downloading 25%")

        bar.set_state("loaded", "small.en on CPU")

        self.assertFalse(bar._loading_notice_active)

    def test_toast_label_updates_live_as_progress_ticks_without_replaying_entrance(self):
        """This is the actual "so u can see the progress" ask: the percentage
        shown in the label above the bar has to keep pace with the fill, not
        freeze at whatever it said the moment the toast first appeared."""
        import bar as bar_mod

        bar = bar_mod.Bar(config.Settings())
        bar._state_since = 0.0
        bar.set_state("loading", "Downloading GPU acceleration… 0%", 0.0)
        self.assertEqual(bar._toast._text, "Downloading GPU acceleration… 0%")
        first_size = bar._toast.size()

        # Same "loading" state, later progress ticks -- update_text(), not a
        # second show_message() call, so no repeated slide-in/fade-in.
        bar.set_state("loading", "Downloading GPU acceleration… 42%", 0.42)
        self.assertEqual(bar._toast._text, "Downloading GPU acceleration… 42%")
        self.assertTrue(bar._loading_notice_active)

        bar.set_state("loading", "Downloading GPU acceleration… 100%", 1.0)
        self.assertEqual(bar._toast._text, "Downloading GPU acceleration… 100%")
        # A wider label can grow the toast; a narrower one won't crash either.
        self.assertGreaterEqual(bar._toast.size().width(), first_size.width() - 40)

    def test_gpu_download_and_update_labels_flow_through_the_same_toast_path(self):
        import bar as bar_mod

        bar = bar_mod.Bar(config.Settings())
        bar._state_since = 0.0

        bar.set_state("loading", "Downloading GPU acceleration… 10%", 0.10)
        self.assertEqual(bar._toast._text, "Downloading GPU acceleration… 10%")

        bar.set_state("loaded", "GPU acceleration ready")
        self.assertFalse(bar._loading_notice_active)

        bar._state_since = 0.0
        bar.set_state("loading", "Downloading update 1.2.3 — 55%", 0.55)
        self.assertEqual(bar._toast._text, "Downloading update 1.2.3 — 55%")


class BarClickWiringTests(unittest.TestCase):
    """main.py's side of the gesture: arming/disarming the bar and routing
    a click to the same finish path a second key-press already used."""

    @staticmethod
    def _app():
        import main

        app = main.App.__new__(main.App)
        app.hotkeys = Mock()
        app.bar = Mock()
        app.lock_limit_timer = Mock()
        app.meter_timer = Mock()
        app.live_preview_timer = Mock()
        app._preview_generation = 0
        app._preview_running = False
        app.mic = Mock()
        app.cues = Mock()
        app._dictation_active = True
        app._busy = False
        app._start_cue_at = 0.0
        return app, main

    def test_click_finishes_a_locked_recording(self):
        app, _main = self._app()
        app.hotkeys.is_locked.return_value = True

        app._on_bar_clicked()

        app.hotkeys.release_lock.assert_called_once()

    def test_click_does_nothing_when_not_locked(self):
        app, _main = self._app()
        app.hotkeys.is_locked.return_value = False

        app._on_bar_clicked()

        app.hotkeys.release_lock.assert_not_called()

    def test_locking_arms_the_bar(self):
        app, _main = self._app()
        app._on_talk_locked()
        app.bar.set_clickable.assert_called_once_with(True)

    def test_cancelling_disarms_the_bar(self):
        app, _main = self._app()
        app.mic.stop.return_value = None
        app._cancel_listening()
        app.bar.set_clickable.assert_called_once_with(False)

    def test_stopping_disarms_the_bar(self):
        import numpy as _np
        app, _main = self._app()
        app.mic.stop.return_value = _np.zeros(0, dtype=_np.float32)
        app._stop_listening(0.01)
        app.bar.set_clickable.assert_called_once_with(False)


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

    def test_note_pitch_settles_from_a_bend(self):
        """The onset should measurably start sharp of the target pitch and
        settle to it well before the note ends -- the "pluck" character,
        not just a vibe. More zero crossings in an early window than an
        equal-length late window means a higher instantaneous frequency."""
        import sounds

        note = sounds._note(sounds.HIGH_HZ)
        window = int(sounds.SAMPLE_RATE * sounds.PITCH_BEND_TAU * 2)

        def zero_crossings(x):
            return int(np.sum(np.diff(np.sign(x)) != 0))

        self.assertGreater(zero_crossings(note[:window]), zero_crossings(note[-window:]))

    def test_decay_has_a_quieter_tail_under_the_main_decay(self):
        """The two-stage envelope should stay above a pure single-exponential
        decay late in the note -- that lingering difference is the "bloom"."""
        import sounds

        t = np.arange(int(sounds.SAMPLE_RATE * sounds.NOTE_SECONDS)) / sounds.SAMPLE_RATE
        single_stage = np.exp(-t / sounds.DECAY_TAU)
        two_stage = (1.0 - sounds.TAIL_MIX) * np.exp(-t / sounds.DECAY_TAU) + (
            sounds.TAIL_MIX * np.exp(-t / sounds.TAIL_TAU)
        )
        late = len(t) * 3 // 4
        self.assertGreater(two_stage[late], single_stage[late])

    def test_new_timbre_constants_are_in_the_fingerprint(self):
        """Regression guard: a future constant added to _note() that is not
        also added to _fingerprint()'s recipe would leave a stale cached
        .wav on disk after the sound is retuned."""
        import sounds

        for attr in ("PITCH_BEND", "PITCH_BEND_TAU", "TAIL_TAU", "TAIL_MIX"):
            with self.subTest(constant=attr):
                before = sounds._fingerprint()
                original = getattr(sounds, attr)
                try:
                    setattr(sounds, attr, original + 0.001)
                    self.assertNotEqual(sounds._fingerprint(), before)
                finally:
                    setattr(sounds, attr, original)

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

    def test_no_cue_is_louder_than_the_others(self):
        """Peak alone does not settle loudness, so hold every cue to one RMS.

        The lock cue is two notes at the same pitch. They overlap
        constructively where a rising or falling pair does not, so peak
        normalisation alone left it hitting the same maximum sample while
        carrying noticeably more energy -- which is what the ear actually
        hears as "louder".
        """
        import sounds

        levels = {
            name: float(np.sqrt(np.mean(np.square(clip))))
            for name, clip in sounds._cues().items()
        }
        for name, rms in levels.items():
            with self.subTest(cue=name):
                self.assertLessEqual(rms, sounds.RMS_CEILING + 1e-6)
        # And they must stay close to each other, not merely under the cap.
        self.assertLess(max(levels.values()) - min(levels.values()), 0.02)

    def test_every_cue_stays_click_free_after_the_loudness_cap(self):
        """The RMS cap rescales the clip, so re-check the zero endpoints."""
        import sounds

        for name, clip in sounds._cues().items():
            with self.subTest(cue=name):
                self.assertAlmostEqual(float(clip[0]), 0.0, places=4)
                self.assertAlmostEqual(float(clip[-1]), 0.0, places=4)
                self.assertLessEqual(float(abs(clip).max()), sounds.FILE_PEAK + 1e-6)

    def test_locking_a_recording_plays_its_own_cue(self):
        """The lock cue is deferred until the start cue has actually
        finished (both end on the same A5, so overlapping them would phase
        against each other) -- see main.py's _play_lock_cue."""
        import main

        app = main.App.__new__(main.App)
        app._dictation_active = True
        app._start_cue_at = 0.0
        app.cues = Mock()
        app.hotkeys = Mock()
        app.hotkeys.is_locked.return_value = True
        app.bar = Mock()
        app.lock_limit_timer = Mock()

        app._on_talk_locked()
        app.cues.play.assert_not_called()  # not yet -- the start cue is still playing

        app._play_lock_cue()
        app.cues.play.assert_called_once_with("lock")

    def test_lock_cue_is_skipped_if_the_lock_already_ended(self):
        """A very fast finish-tap right after locking can end the recording
        before the deferred cue fires; playing "lock" for a recording that
        is already over would be actively misleading."""
        import main

        app = main.App.__new__(main.App)
        app.cues = Mock()
        app.hotkeys = Mock()
        app.hotkeys.is_locked.return_value = False

        app._play_lock_cue()

        app.cues.play.assert_not_called()

    def test_a_lock_that_never_started_makes_no_sound(self):
        import main

        app = main.App.__new__(main.App)
        app._dictation_active = False
        app.cues = Mock()
        app.hotkeys = Mock()
        app.lock_limit_timer = Mock()

        app._on_talk_locked()

        app.cues.play.assert_not_called()

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


def _release(tag: str, assets: list, *, draft: bool = False) -> dict:
    entry = {"tag_name": tag, "assets": assets}
    if draft:
        entry["draft"] = True
    return entry


def _installer_assets(
    tag: str, *, digest: str | None = None
) -> tuple[list, str, str]:
    version = tag.lstrip("vV")
    installer_url = (
        f"https://github.com/PLEXFX/dictate/releases/download/{tag}/"
        f"Dictate-Setup-{version}.exe"
    )
    if digest is None:
        digest = hashlib.sha256(version.encode("utf-8")).hexdigest()
    assets = [
        {
            "name": f"Dictate-Setup-{version}.exe",
            "browser_download_url": installer_url,
            "size": 12345,
            "digest": f"sha256:{digest}",
        },
    ]
    return assets, installer_url, digest


class UpdaterReleaseFetchTests(unittest.TestCase):
    def test_parses_the_installer_asset(self):
        assets, installer_url, digest = _installer_assets("v0.1.0-beta.3")
        payload = json.dumps([_release("v0.1.0-beta.3", assets)]).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            info = updater._fetch_latest_release()
        self.assertEqual(info["version"], "0.1.0-beta.3")
        self.assertEqual(info["installer_url"], installer_url)
        self.assertEqual(info["installer_size"], 12345)
        self.assertEqual(info["installer_sha256"], digest)

    def test_picks_the_highest_version_regardless_of_list_order(self):
        # The real endpoint used, /releases, does not promise any particular
        # order -- this must not just trust entry [0].
        old_assets, _, _ = _installer_assets("v0.1.0-beta.2")
        new_assets, new_url, _ = _installer_assets("v0.2.0-beta.2")
        payload = json.dumps(
            [_release("v0.1.0-beta.2", old_assets), _release("v0.2.0-beta.2", new_assets)]
        ).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            info = updater._fetch_latest_release()
        self.assertEqual(info["version"], "0.2.0-beta.2")
        self.assertEqual(info["installer_url"], new_url)

    def test_ignores_prerelease_flag(self):
        # This is the actual bug: GitHub's /releases/latest endpoint hides
        # anything flagged prerelease, and every Dictate release is one.
        # /releases (this fetch) must not apply that same filtering itself.
        assets, installer_url, _ = _installer_assets("v0.2.0-beta.2")
        entry = _release("v0.2.0-beta.2", assets)
        entry["prerelease"] = True
        payload = json.dumps([entry]).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            info = updater._fetch_latest_release()
        self.assertEqual(info["installer_url"], installer_url)

    def test_skips_draft_releases(self):
        draft_assets, _, _ = _installer_assets("v9.9.9-beta.1")
        real_assets, real_url, _ = _installer_assets("v0.2.0-beta.2")
        payload = json.dumps(
            [
                _release("v9.9.9-beta.1", draft_assets, draft=True),
                _release("v0.2.0-beta.2", real_assets),
            ]
        ).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            info = updater._fetch_latest_release()
        self.assertEqual(info["installer_url"], real_url)

    def test_rejects_an_asset_from_any_other_repository(self):
        payload = json.dumps(
            [
                _release(
                    "v0.1.0-beta.3",
                    [
                        {
                            "name": "Dictate-Setup-0.1.0-beta.3.exe",
                            "browser_download_url": "https://github.com/other/repo/releases/download/v0/x.exe",
                            "size": 12345,
                            "digest": f"sha256:{'a' * 64}",
                        },
                    ],
                )
            ]
        ).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            self.assertIsNone(updater._fetch_latest_release())

    def test_returns_none_without_an_installer_asset(self):
        payload = json.dumps([_release("v0.1.0-beta.3", [])]).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            self.assertIsNone(updater._fetch_latest_release())

    def test_returns_none_when_the_installer_asset_has_no_digest(self):
        # GitHub computes this itself; a missing/malformed digest must fail
        # closed rather than skip the integrity check.
        assets, _, _ = _installer_assets("v0.1.0-beta.3")
        del assets[0]["digest"]
        payload = json.dumps([_release("v0.1.0-beta.3", assets)]).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            self.assertIsNone(updater._fetch_latest_release())

    def test_returns_none_when_the_digest_is_malformed(self):
        assets, _, _ = _installer_assets("v0.1.0-beta.3", digest="not-hex")
        payload = json.dumps([_release("v0.1.0-beta.3", assets)]).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            self.assertIsNone(updater._fetch_latest_release())

    def test_returns_none_when_every_tag_is_unparseable(self):
        payload = json.dumps([_release("not-a-version", [])]).encode("utf-8")
        with patch.object(
            updater.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            self.assertIsNone(updater._fetch_latest_release())

    def test_returns_none_on_network_failure(self):
        with patch.object(
            updater.urllib.request, "urlopen", side_effect=OSError("offline")
        ):
            self.assertIsNone(updater._fetch_latest_release())


class UpdateNoticeTests(unittest.TestCase):
    """The What's New dialog after a restart: written pre-update, read once
    by the version it names, at the notice path (patched here rather than
    the real %APPDATA%, so this test can't ever touch a real user's file)."""

    def test_notes_survive_the_restart_when_versions_match(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "update-notice.json"
            with patch.object(updater, "_update_notice_path", return_value=path):
                updater._write_update_notice("0.2.2-beta.1", "Short release notes.")
                self.assertEqual(
                    updater.consume_update_notice("0.2.2-beta.1"), "Short release notes."
                )

    def test_notice_is_discarded_for_a_different_version(self):
        # This is what a relabeled-same-binary test release looks like: the
        # notice names the release tag, but the exe that actually restarted
        # is still the old build, so its compiled-in VERSION won't match.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "update-notice.json"
            with patch.object(updater, "_update_notice_path", return_value=path):
                updater._write_update_notice("0.2.2-beta.1", "Notes for the real build.")
                self.assertIsNone(updater.consume_update_notice("0.2.1-beta.1"))

    def test_consuming_the_notice_deletes_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "update-notice.json"
            with patch.object(updater, "_update_notice_path", return_value=path):
                updater._write_update_notice("0.2.2-beta.1", "Notes.")
                updater.consume_update_notice("0.2.2-beta.1")
                self.assertFalse(path.exists())

    def test_no_notice_file_returns_none(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "update-notice.json"
            with patch.object(updater, "_update_notice_path", return_value=path):
                self.assertIsNone(updater.consume_update_notice("0.2.2-beta.1"))


class UpdateCleanupTests(unittest.TestCase):
    def test_removes_stale_download_folders(self):
        with tempfile.TemporaryDirectory() as fake_temp:
            stale = Path(fake_temp) / f"{updater._DOWNLOAD_TEMP_PREFIX}abc123"
            stale.mkdir()
            (stale / "Dictate-Setup-0.2.1-beta.1.exe").write_bytes(b"not a real installer")
            unrelated = Path(fake_temp) / "some-other-app-temp"
            unrelated.mkdir()
            with patch.object(updater.tempfile, "gettempdir", return_value=fake_temp):
                updater.cleanup_stale_downloads()
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())

    def test_tolerates_an_unreadable_temp_directory(self):
        with patch.object(
            updater.Path, "glob", side_effect=OSError("permission denied")
        ):
            updater.cleanup_stale_downloads()  # must not raise


class UpdaterFlowTests(unittest.TestCase):
    SIGNER = "A" * 40

    @staticmethod
    def _info(**overrides):
        info = {
            "version": "9.9.9",
            "installer_url": "https://x/installer.exe",
            "installer_name": "installer-flow-test.exe",
            "installer_size": 5,
            "installer_sha256": hashlib.sha256(b"12345").hexdigest(),
            "release_notes": "A safer updater.",
        }
        info.update(overrides)
        return info

    def test_check_now_never_downloads_on_its_own(self):
        """The core behaviour change: finding a newer release must only ever
        report AVAILABLE, never start a download by itself -- that is the
        whole point of dropping the old auto-download-on-check design."""
        available = threading.Event()
        downloaded = threading.Event()

        with (
            patch.object(updater, "_fetch_latest_release", return_value=self._info()),
            patch.object(updater, "_download", side_effect=lambda *a, **k: downloaded.set()),
        ):
            u = updater.Updater(
                on_available=lambda version, notes: available.set(),
                current_version="0.1.0-beta.2",
                check_interval=9999,
                trusted_signer_thumbprint=self.SIGNER,
            )
            try:
                self.assertTrue(available.wait(timeout=5))
                time.sleep(0.2)  # give a wrongly-auto-triggered download time to start
                self.assertFalse(downloaded.is_set())
                self.assertEqual(u.last_status[0], updater.AVAILABLE)
            finally:
                u.shutdown()

    def test_start_update_downloads_verifies_and_installs(self):
        available = threading.Event()
        installing = threading.Event()
        installing_args = []

        def on_installing(version, installer_pid):
            installing_args.append((version, installer_pid))
            installing.set()

        with (
            patch.object(updater, "_fetch_latest_release", return_value=self._info()),
            patch.object(updater, "_verify_authenticode", return_value=True),
            patch.object(
                updater,
                "_download",
                side_effect=lambda url, dest, cb: dest.write_bytes(b"12345"),
            ),
            patch.object(updater.subprocess, "Popen") as popen,
        ):
            u = updater.Updater(
                on_available=lambda version, notes: available.set(),
                on_installing=on_installing,
                current_version="0.1.0-beta.2",
                check_interval=9999,
                trusted_signer_thumbprint=self.SIGNER,
            )
            try:
                self.assertTrue(available.wait(timeout=5))
                self.assertEqual(u.last_status[0], updater.AVAILABLE)
                self.assertTrue(u.start_update())
                self.assertTrue(installing.wait(timeout=5))
            finally:
                u.shutdown()

        self.assertEqual(installing_args, [("9.9.9", popen.return_value.pid)])
        popen.assert_called_once()
        args = popen.call_args[0][0]
        self.assertTrue(args[0].endswith("installer-flow-test.exe"))
        self.assertEqual(args[1:], ["/SP-", "/VERYSILENT", "/NORESTART"])

    def test_start_update_is_false_with_nothing_available(self):
        with patch.object(updater, "_fetch_latest_release", return_value=None):
            u = updater.Updater(
                current_version="0.1.0-beta.2",
                check_interval=9999,
                trusted_signer_thumbprint=self.SIGNER,
            )
            try:
                time.sleep(0.1)
                self.assertFalse(u.start_update())
            finally:
                u.shutdown()

    def test_start_update_is_false_on_a_duplicate_click(self):
        """A second click (bar toast and Settings button both wired to the
        same Updater) must not start a second overlapping download."""
        available = threading.Event()
        installing = threading.Event()
        download_started = threading.Event()
        release_download = threading.Event()

        def slow_download(url, dest, cb):
            download_started.set()
            release_download.wait(timeout=5)
            dest.write_bytes(b"12345")

        with (
            patch.object(updater, "_fetch_latest_release", return_value=self._info()),
            patch.object(updater, "_verify_authenticode", return_value=True),
            patch.object(updater, "_download", side_effect=slow_download),
            patch.object(updater.subprocess, "Popen"),
        ):
            u = updater.Updater(
                on_available=lambda version, notes: available.set(),
                on_installing=lambda version, installer_pid: installing.set(),
                current_version="0.1.0-beta.2",
                check_interval=9999,
                trusted_signer_thumbprint=self.SIGNER,
            )
            try:
                self.assertTrue(available.wait(timeout=5))
                self.assertTrue(u.start_update())
                self.assertTrue(download_started.wait(timeout=5))
                self.assertFalse(u.start_update())  # the duplicate click
                release_download.set()
                self.assertTrue(installing.wait(timeout=5))
            finally:
                u.shutdown()

    def test_unsigned_release_still_installs_on_hash_and_url_alone(self):
        """No code-signing cert is configured yet (TRUSTED_SIGNER_THUMBPRINT
        is ""), so updates must still work from URL + SHA-256 verification
        only -- and must never call the Authenticode check, since a call
        with an empty expected thumbprint would be meaningless anyway."""
        available = threading.Event()
        installing = threading.Event()

        with (
            patch.object(updater, "_fetch_latest_release", return_value=self._info()),
            patch.object(updater, "_verify_authenticode") as verify_authenticode,
            patch.object(
                updater,
                "_download",
                side_effect=lambda url, dest, cb: dest.write_bytes(b"12345"),
            ),
            patch.object(updater.subprocess, "Popen"),
        ):
            u = updater.Updater(
                on_available=lambda version, notes: available.set(),
                on_installing=lambda version, installer_pid: installing.set(),
                current_version="0.1.0-beta.2",
                check_interval=9999,
                trusted_signer_thumbprint="",
            )
            try:
                self.assertTrue(available.wait(timeout=5))
                self.assertTrue(u.start_update())
                self.assertTrue(installing.wait(timeout=5))
            finally:
                u.shutdown()
        verify_authenticode.assert_not_called()

    def test_start_update_reports_an_error_on_a_bad_checksum(self):
        """A download/verify failure during start_update() must always be
        reported -- unlike a background check, there is no silent path here,
        because the only way to reach this code is an explicit click."""
        available = threading.Event()
        errored = threading.Event()
        errors = []

        with (
            patch.object(
                updater,
                "_fetch_latest_release",
                return_value=self._info(installer_sha256="0" * 64),
            ),
            patch.object(
                updater,
                "_download",
                side_effect=lambda url, dest, cb: dest.write_bytes(b"12345"),
            ),
        ):
            u = updater.Updater(
                on_available=lambda version, notes: available.set(),
                on_error=lambda message: (errors.append(message), errored.set()),
                current_version="0.1.0-beta.2",
                check_interval=9999,
                trusted_signer_thumbprint=self.SIGNER,
            )
            try:
                self.assertTrue(available.wait(timeout=5))
                self.assertTrue(u.start_update())
                self.assertTrue(errored.wait(timeout=5))
                self.assertEqual(u.last_status[0], updater.ERROR)
            finally:
                u.shutdown()
        self.assertEqual(len(errors), 1)

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
                trusted_signer_thumbprint=self.SIGNER,
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

    def test_disabled_updater_never_checks_until_re_enabled(self):
        fetch_calls = threading.Event()

        def fake_fetch():
            fetch_calls.set()
            return None

        with patch.object(updater, "_fetch_latest_release", side_effect=fake_fetch):
            u = updater.Updater(
                current_version="0.1.0-beta.2",
                check_interval=9999,
                trusted_signer_thumbprint=self.SIGNER,
                enabled=False,
            )
            try:
                time.sleep(0.1)
                self.assertFalse(fetch_calls.is_set())
                u.check_now()  # a stray manual call must also stay a no-op
                time.sleep(0.1)
                self.assertFalse(fetch_calls.is_set())

                u.set_enabled(True)
                self.assertTrue(fetch_calls.wait(timeout=5))
            finally:
                u.shutdown()


class UpdateSplashTests(unittest.TestCase):
    """update_splash.py's own state machine and path logic -- pure, no
    ctypes/Qt/COM involved, so these run the same as any other unit test
    rather than needing a real installer process or window."""

    def test_still_running_keeps_waiting(self):
        import update_splash as splash

        self.assertEqual(
            splash.decide_next_action(
                exit_code=None,
                elapsed_seconds=1.0,
                updated_window_ready=False,
            ),
            splash.WAITING,
        )

    def test_nonzero_exit_relaunches_immediately(self):
        import update_splash as splash

        self.assertEqual(
            splash.decide_next_action(
                exit_code=1,
                elapsed_seconds=1.0,
                updated_window_ready=False,
            ),
            splash.RELAUNCH_AND_CLOSE,
        )

    def test_success_waits_for_the_updated_window_to_report_ready(self):
        import update_splash as splash

        self.assertEqual(
            splash.decide_next_action(
                exit_code=0,
                elapsed_seconds=20.0,
                updated_window_ready=False,
            ),
            splash.SUCCESS_GRACE,
        )

    def test_success_closes_only_once_the_updated_window_reports_ready(self):
        import update_splash as splash

        self.assertEqual(
            splash.decide_next_action(
                exit_code=0,
                elapsed_seconds=2.0,
                updated_window_ready=True,
            ),
            splash.CLOSE,
        )

    def test_safety_timeout_wins_over_everything(self):
        import update_splash as splash

        self.assertEqual(
            splash.decide_next_action(
                exit_code=None,
                elapsed_seconds=splash.SAFETY_TIMEOUT_SECONDS,
                updated_window_ready=False,
            ),
            splash.CLOSE,
        )
        # Even a fresh, healthy DOWNLOADING-equivalent state can't survive
        # past the ceiling -- it isn't only a "gave up waiting" fallback.
        self.assertEqual(
            splash.decide_next_action(
                exit_code=0,
                elapsed_seconds=splash.SAFETY_TIMEOUT_SECONDS,
                updated_window_ready=True,
            ),
            splash.CLOSE,
        )

    def test_relaunch_target_is_one_level_up_from_the_updater_subfolder(self):
        """Matches installer/dictate.iss's layout: {app}\\dictate.exe next to
        {app}\\updater\\dictate-updater.exe."""
        import update_splash as splash

        splash_exe = Path(r"C:\Program Files\Dictate\updater\dictate-updater.exe")
        self.assertEqual(
            splash.relaunch_target_path(splash_exe),
            Path(r"C:\Program Files\Dictate\dictate.exe"),
        )


if __name__ == "__main__":
    unittest.main()

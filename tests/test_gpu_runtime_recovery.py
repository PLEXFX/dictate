"""Regression coverage for GPU setup after a Core-only install."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import gpu_runtime
import engine


def _make_runtime(root: Path) -> None:
    for subdir in gpu_runtime._PACKAGE_SUBDIRS.values():
        (root / subdir / "bin").mkdir(parents=True)


class GpuRuntimeRecoveryTests(unittest.TestCase):
    def test_settings_download_target_is_user_data_not_the_install_folder(self):
        """A normal Dictate process must never need write access to _internal."""
        with tempfile.TemporaryDirectory() as temp:
            appdata = Path(temp) / "Roaming"
            bundled = Path(temp) / "Dictate" / "_internal"
            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime.sys, "_MEIPASS", str(bundled), create=True),
                patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False),
            ):
                self.assertEqual(
                    gpu_runtime.runtime_dir(),
                    appdata / "dictate" / "gpu-runtime" / "nvidia",
                )

    def test_installer_runtime_still_counts_as_ready(self):
        """Selecting GPU during setup remains compatible with the new target."""
        with tempfile.TemporaryDirectory() as temp:
            appdata = Path(temp) / "Roaming"
            bundled = Path(temp) / "Dictate" / "_internal"
            bundled_runtime = bundled / "nvidia"
            _make_runtime(bundled_runtime)
            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime.sys, "_MEIPASS", str(bundled), create=True),
                patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False),
            ):
                self.assertTrue(gpu_runtime.is_installed())
                self.assertEqual(gpu_runtime.installed_runtime_dir(), bundled_runtime)

    def test_complete_user_runtime_takes_precedence_over_old_installer_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            appdata = Path(temp) / "Roaming"
            bundled = Path(temp) / "Dictate" / "_internal"
            bundled_runtime = bundled / "nvidia"
            user_runtime = appdata / "dictate" / "gpu-runtime" / "nvidia"
            _make_runtime(bundled_runtime)
            _make_runtime(user_runtime)
            with (
                patch.object(gpu_runtime.sys, "frozen", True, create=True),
                patch.object(gpu_runtime.sys, "_MEIPASS", str(bundled), create=True),
                patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False),
            ):
                self.assertEqual(gpu_runtime.installed_runtime_dir(), user_runtime)

    def test_user_runtime_is_added_before_cuda_dll_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            appdata = Path(temp) / "Roaming"
            bundled = Path(temp) / "Dictate" / "_internal"
            user_runtime = appdata / "dictate" / "gpu-runtime" / "nvidia"
            _make_runtime(user_runtime)
            original_path = sys.path[:]
            try:
                with (
                    patch.object(gpu_runtime.sys, "frozen", True, create=True),
                    patch.object(gpu_runtime.sys, "_MEIPASS", str(bundled), create=True),
                    patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False),
                ):
                    gpu_runtime.activate_runtime()
                    self.assertEqual(sys.path[0], str(user_runtime.parent))
            finally:
                sys.path[:] = original_path

    def test_cuda_dll_registration_retries_after_runtime_arrives(self):
        """Opening Settings before download must not consume the one retry."""
        original = engine._cuda_dlls_registered
        try:
            engine._cuda_dlls_registered = False
            with (
                patch.object(engine.sys, "platform", "win32"),
                patch.object(engine.gpu_runtime, "activate_runtime"),
                patch("importlib.util.find_spec", return_value=None),
            ):
                engine._register_cuda_dlls()
            self.assertFalse(engine._cuda_dlls_registered)
        finally:
            engine._cuda_dlls_registered = original

    def test_incomplete_model_snapshot_is_forced_to_download_again(self):
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / "broken"
            broken.mkdir()
            complete = Path(temp) / "complete"
            complete.mkdir()
            (complete / "model.bin").write_bytes(b"model")
            hub = Mock()
            hub.snapshot_download.side_effect = [str(broken), str(complete)]
            with patch.dict(sys.modules, {"huggingface_hub": hub}):
                engine._predownload_with_progress("small.en", lambda *_args: None)
            self.assertEqual(hub.snapshot_download.call_count, 2)
            self.assertTrue(hub.snapshot_download.call_args_list[1].kwargs["force_download"])

    def test_model_bin_load_error_repairs_and_retries_the_verified_snapshot(self):
        """A nonempty but corrupt weight file must not surface its raw error."""
        with tempfile.TemporaryDirectory() as temp:
            repaired = Path(temp) / "repaired"
            repaired.mkdir()
            (repaired / "model.bin").write_bytes(b"healthy weights")

            subject = engine.Engine.__new__(engine.Engine)
            subject._settings = type("Settings", (), {"model_size": "tiny.en", "device": "cpu"})()
            subject._model = None
            subject._loaded_key = None
            subject._lock = threading.RLock()
            subject._last_used = 0.0
            subject._set_state = Mock()

            first_load = RuntimeError("Unable to open file 'model.bin'")
            with (
                patch.object(engine, "resolve_device", return_value="cpu"),
                patch.object(engine, "_register_cuda_dlls"),
                patch.object(
                    subject,
                    "_download_with_progress",
                    side_effect=[None, str(repaired)],
                ) as download,
                patch(
                    "faster_whisper.WhisperModel",
                    side_effect=[first_load, Mock()],
                ) as whisper_model,
            ):
                subject.ensure_loaded()

            self.assertEqual(download.call_args_list[1].kwargs, {"force_download": True})
            self.assertEqual(whisper_model.call_args_list[1].args[0], str(repaired))
            self.assertEqual(subject._loaded_key, ("tiny.en", "cpu"))


if __name__ == "__main__":
    unittest.main()

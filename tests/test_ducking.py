"""Lowering other apps while recording (ducking.py and its wiring).

Every test drives fake audio sessions synchronously; nothing here touches
the real Windows volume mixer.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import config
import ducking

OWN_PID = 111


class FakeSession:
    def __init__(self, key: str, pid: int, volume: float = 1.0) -> None:
        self.key = key
        self.pid = pid
        self.volume = volume
        self.gone = False

    def get_volume(self) -> float:
        if self.gone:
            raise OSError("session closed")
        return self.volume

    def set_volume(self, value: float) -> None:
        if self.gone:
            raise OSError("session closed")
        self.volume = value


class DuckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.journal = Path(self._dir.name) / "ducked-audio.json"
        self.music = FakeSession("music", 222, 0.8)
        self.video = FakeSession("video", 333, 1.0)
        self.own = FakeSession("dictate", OWN_PID, 1.0)
        self.sessions = [self.music, self.video, self.own]

    def tearDown(self) -> None:
        self._dir.cleanup()

    def make(self, enabled: bool = True) -> ducking.Ducker:
        return ducking.Ducker(
            enabled,
            sessions=lambda: self.sessions,
            journal=self.journal,
            own_pid=OWN_PID,
            threaded=False,
            ramp_steps=1,
        )

    def test_duck_lowers_other_apps_but_not_dictate(self):
        ducker = self.make()
        ducker.duck()
        self.assertAlmostEqual(self.music.volume, 0.8 * ducking.DUCK_LEVEL)
        self.assertAlmostEqual(self.video.volume, 1.0 * ducking.DUCK_LEVEL)
        self.assertEqual(self.own.volume, 1.0)

    def test_restore_puts_every_app_back(self):
        ducker = self.make()
        ducker.duck()
        ducker.restore()
        self.assertAlmostEqual(self.music.volume, 0.8)
        self.assertAlmostEqual(self.video.volume, 1.0)
        self.assertFalse(self.journal.exists())

    def test_disabled_does_nothing(self):
        ducker = self.make(enabled=False)
        ducker.duck()
        self.assertEqual(self.music.volume, 0.8)
        self.assertFalse(self.journal.exists())

    def test_a_second_duck_does_not_lower_twice(self):
        ducker = self.make()
        ducker.duck()
        ducker.duck()
        self.assertAlmostEqual(self.video.volume, ducking.DUCK_LEVEL)
        ducker.restore()
        self.assertAlmostEqual(self.video.volume, 1.0)

    def test_a_slider_the_user_moved_mid_recording_is_left_alone(self):
        ducker = self.make()
        ducker.duck()
        self.video.volume = 0.2  # user changed it in Volume Mixer
        ducker.restore()
        self.assertEqual(self.video.volume, 0.2)
        self.assertAlmostEqual(self.music.volume, 0.8)

    def test_an_app_that_closed_mid_recording_does_not_break_restore(self):
        ducker = self.make()
        ducker.duck()
        self.video.gone = True
        ducker.restore()
        self.assertAlmostEqual(self.music.volume, 0.8)

    def test_turning_the_setting_off_mid_recording_restores(self):
        ducker = self.make()
        ducker.duck()
        ducker.update_settings(config.Settings(duck_other_audio=False))
        self.assertFalse(ducker.enabled)
        self.assertAlmostEqual(self.video.volume, 1.0)

    def test_duck_journals_before_restore_clears_it(self):
        ducker = self.make()
        ducker.duck()
        saved = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"music", "video"})
        self.assertAlmostEqual(saved["music"][0], 0.8)

    def test_next_launch_repairs_a_crash_mid_recording(self):
        self.make().duck()  # the app dies here without restoring
        self.assertAlmostEqual(self.video.volume, ducking.DUCK_LEVEL)
        self.make(enabled=False)  # relaunch, even with the setting now off
        self.assertAlmostEqual(self.music.volume, 0.8)
        self.assertAlmostEqual(self.video.volume, 1.0)
        self.assertFalse(self.journal.exists())

    def test_recovery_respects_a_volume_changed_after_the_crash(self):
        self.make().duck()
        self.video.volume = 0.3
        self.make()
        self.assertEqual(self.video.volume, 0.3)

    def test_a_corrupt_journal_is_ignored(self):
        self.journal.write_text("{not json", encoding="utf-8")
        self.make()  # must not raise
        self.assertEqual(self.music.volume, 0.8)

    def test_a_failing_audio_system_never_raises(self):
        def broken():
            raise OSError("no audio service")

        ducker = ducking.Ducker(
            True, sessions=broken, journal=self.journal, threaded=False
        )
        ducker.duck()
        ducker.restore()
        ducker.shutdown()

    def test_threaded_worker_runs_jobs_in_order(self):
        ducker = ducking.Ducker(
            True,
            sessions=lambda: self.sessions,
            journal=self.journal,
            own_pid=OWN_PID,
            ramp_steps=1,
        )
        ducker.duck()
        ducker.restore()
        ducker.shutdown()
        self.assertAlmostEqual(self.video.volume, 1.0)
        self.assertFalse(ducker._thread.is_alive())


class DuckingSettingTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(config.Settings().duck_other_audio)

    def test_a_bad_stored_value_clamps_to_a_bool(self):
        self.assertTrue(config.Settings(duck_other_audio=1).clamped().duck_other_audio)
        self.assertFalse(config.Settings(duck_other_audio=None).clamped().duck_other_audio)


class DuckingWiringTests(unittest.TestCase):
    def _app(self):
        import main

        app = main.App.__new__(main.App)
        app.ducker = Mock()
        return app

    def test_stop_and_cancel_restore_audio(self):
        import main

        app = self._app()
        app._restore_other_audio()
        app.ducker.restore.assert_called_once()

    def test_missing_ducker_is_tolerated(self):
        import main

        app = main.App.__new__(main.App)
        app._duck_other_audio()
        app._restore_other_audio()


if __name__ == "__main__":
    unittest.main()

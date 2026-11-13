"""Packaging: the WinUI program rides in the installer, the versions agree, and the engine finds it once installed."""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui_bridge
from version import VERSION

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "installer" / "dictate.iss").read_text(encoding="utf-8")


def section(name: str) -> str:
    """The body of one [Section] of the Inno script."""
    match = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", ISS, re.S | re.M)
    assert match, f"no [{name}] in dictate.iss"
    return match.group(1)


class VersionTests(unittest.TestCase):
    """One version, in every file that carries it (the release rule in CLAUDE.md)."""

    def test_every_file_agrees_with_version_py(self):
        pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
        iss = re.search(r'#define MyAppVersion "([^"]+)"', ISS)
        csproj = re.search(r"<Version>([^<]+)</Version>", (ROOT / "ui" / "DictateUI.csproj").read_text(encoding="utf-8"))
        lock = re.search(r'name = "dictate"\nversion = "([^"]+)"', (ROOT / "uv.lock").read_text(encoding="utf-8"))
        self.assertEqual(pyproject.group(1), VERSION)
        self.assertEqual(iss.group(1), VERSION)
        self.assertEqual(csproj.group(1), VERSION, "ui\\DictateUI.csproj <Version> must match version.py")
        # uv.lock writes the PEP 440 form: 1.4.3-beta1 becomes 1.4.3b1
        self.assertEqual(lock.group(1), VERSION.replace("-beta", "b"))


class InstallerShipsTheProgramTests(unittest.TestCase):
    def test_the_program_is_installed_beside_the_app_without_debug_symbols(self):
        files = section("Files")
        line = next(l for l in files.splitlines() if "{#UiSourceDir}" in l and not l.lstrip().startswith(";"))
        self.assertIn('DestDir: "{app}\\ui"', line)
        self.assertIn('Excludes: "*.pdb"', line)
        self.assertIn("recursesubdirs", line)

    def test_an_upgrade_replaces_the_program_as_a_unit_and_uninstall_removes_it(self):
        for name in ("InstallDelete", "UninstallDelete"):
            self.assertRegex(section(name), r'Type: filesandordirs; Name: "\{app\}\\ui"', name)

    def test_the_new_interface_has_its_own_shortcut_and_the_normal_one_is_unchanged(self):
        icons = section("Icons")
        normal = next(l for l in icons.splitlines() if l.startswith('Name: "{group}\\Dictate"'))
        new = next(l for l in icons.splitlines() if "new interface" in l and not l.startswith(";"))
        self.assertNotIn("Parameters", normal)
        self.assertIn('Parameters: "--winui"', new)
        self.assertIn("{app}\\{#MyAppExeName}", new)

    def test_post_install_launches_are_skipped_only_for_a_test_build(self):
        run = section("Run")
        start, end = run.index("#ifndef NoLaunch"), run.index("#endif")
        launches = [i for i, l in enumerate(run.splitlines()) if l.startswith('Filename: "{app}\\{#MyAppExeName}"')]
        lines_before = lambda marker: run[: run.index(marker)].count("\n")
        self.assertEqual(len(launches), 2, "both launches are still there for a normal build")
        self.assertTrue(all(lines_before("#ifndef NoLaunch") < i < lines_before("#endif") for i in launches))
        self.assertLess(start, end)

    def test_a_test_build_can_redirect_every_input_and_the_app_id(self):
        for macro in ("SourceDir", "UpdaterSourceDir", "UiSourceDir", "MyAppId"):
            self.assertRegex(ISS, rf"#ifndef {macro}\n#define {macro} ", macro)
        self.assertIn("AppId={#MyAppId}", ISS)
        # the default id is still the real one, so upgrades keep finding the existing install
        self.assertIn("65B8AB9F-FF1B-4148-8ED5-76F30681FD64", ISS)

    def test_the_stage_script_and_the_tray_icon_files_exist(self):
        self.assertTrue((ROOT / "ui" / "Publish.bat").is_file())
        csproj = (ROOT / "ui" / "DictateUI.csproj").read_text(encoding="utf-8")
        for asset in ("Assets\\dictate.ico", "Assets\\dictate-32.png"):
            self.assertIn(f'Content Include="{asset}"', csproj, f"{asset} must be copied beside the program")
            self.assertTrue((ROOT / "ui" / asset).is_file(), asset)


class FindingTheInstalledProgramTests(unittest.TestCase):
    def test_an_installed_build_looks_in_its_own_ui_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / "ui").mkdir()
            exe = app / "ui" / "DictateUI.exe"
            exe.write_bytes(b"MZ")
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(app / "dictate.exe")), patch.dict(
                os.environ, {"DICTATE_UI_EXE": ""}
            ):
                self.assertEqual(ui_bridge.find_ui_exe(), exe.resolve())

    def test_an_installed_build_without_the_program_finds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(Path(tmp) / "dictate.exe")), patch.dict(
                os.environ, {"DICTATE_UI_EXE": ""}
            ):
                self.assertIsNone(ui_bridge.find_ui_exe())


class WhichInterfaceTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("DICTATE_WINUI", None)

    def tearDown(self):
        self._env.stop()

    def test_the_built_in_interface_is_the_default_until_the_new_one_is_approved(self):
        self.assertFalse(ui_bridge.WINUI_BY_DEFAULT)
        self.assertFalse(ui_bridge.winui_requested([]))
        self.assertFalse(ui_bridge.winui_requested(["--updated"]))

    def test_the_shortcut_flag_and_the_environment_ask_for_the_new_one(self):
        self.assertTrue(ui_bridge.winui_requested(["--winui"]))
        self.assertTrue(ui_bridge.winui_requested(["--updated", "--winui"]))
        os.environ["DICTATE_WINUI"] = "1"
        self.assertTrue(ui_bridge.winui_requested([]))

    def test_opting_out_beats_asking_in(self):
        self.assertFalse(ui_bridge.winui_requested(["--winui", "--builtin-ui"]))
        os.environ["DICTATE_WINUI"] = "0"
        self.assertFalse(ui_bridge.winui_requested(["--winui"]))

    def test_once_it_is_the_default_the_opt_outs_still_work(self):
        with patch.object(ui_bridge, "WINUI_BY_DEFAULT", True):
            self.assertTrue(ui_bridge.winui_requested([]))
            self.assertFalse(ui_bridge.winui_requested(["--builtin-ui"]))
            os.environ["DICTATE_WINUI"] = "0"
            self.assertFalse(ui_bridge.winui_requested([]))

    def test_it_reads_the_real_command_line_when_given_none(self):
        with patch.object(sys, "argv", ["dictate.exe", "--winui"]):
            self.assertTrue(ui_bridge.winui_requested())
        with patch.object(sys, "argv", ["dictate.exe"]):
            self.assertFalse(ui_bridge.winui_requested())


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from mayor.locate import ScanResult, add_shortcut_targets, install_folder_for, scan


class LocateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = Path(self.tmp.name)
        (r / "Maxis" / "SimCity 3000 Unlimited" / "Apps").mkdir(parents=True)
        (r / "Maxis" / "SimCity 3000 Unlimited" / "Apps" / "SC3U.exe").write_bytes(b"")
        (r / "Downloads" / "old games").mkdir(parents=True)
        (r / "Downloads" / "old games" / "SimCity3000_KOR.iso").write_bytes(b"x" * 10)
        (r / "Downloads" / "old games" / "README.TXT").write_bytes(
            "심시티 3000 추가설명 파일 http://www.mediasoft.co.kr/simcity3000".encode("cp949"))
        (r / "Downloads" / "other.txt").write_bytes(b"nothing here")
        (r / "Desktop").mkdir()
        (r / "Desktop" / "SimCity 3000.lnk").write_bytes(b"")
        (r / "Desktop" / "SC3Setup.exe").write_bytes(b"")
        (r / "Windows" / "deep").mkdir(parents=True)
        (r / "Windows" / "deep" / "SC3.exe").write_bytes(b"")  # skipped folder
        (r / "a" / "b" / "c" / "d").mkdir(parents=True)
        (r / "a" / "b" / "c" / "d" / "SC3.exe").write_bytes(b"")  # deeper than max depth

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan(self):
        res = scan([(self.root, 3), (self.root / "Downloads", 3)])  # second root overlaps: no duplicates
        got = {(c.kind, Path(c.path).relative_to(self.root).as_posix()) for c in res.clues}
        self.assertEqual(got, {
            ("game_exe", "Maxis/SimCity 3000 Unlimited/Apps/SC3U.exe"),
            ("shortcut", "Desktop/SimCity 3000.lnk"),
            ("readme", "Downloads/old games/README.TXT"),
            ("exe_like", "Desktop/SC3Setup.exe"),
            ("archive", "Downloads/old games/SimCity3000_KOR.iso"),
            ("folder", "Maxis"),
            ("folder", "Maxis/SimCity 3000 Unlimited"),
        })
        self.assertTrue(res.complete)
        self.assertEqual(res.clues[0].kind, "game_exe")  # sorted by importance

    def test_callbacks(self):
        seen, progress = [], []
        res = scan([(self.root, 3)], on_clue=seen.append, progress_every_s=0,
                   on_progress=lambda *a: progress.append(a))
        self.assertEqual(len(seen), len(res.clues))
        self.assertTrue(progress)

    def test_time_budget(self):
        res = scan([(self.root, 3)], time_budget_s=-1)
        self.assertFalse(res.complete)

    def test_shortcut_target_becomes_game_exe(self):
        res = scan([(self.root / "Desktop", 1)])
        target = str(self.root / "Games" / "SC3" / "SC3.exe")
        add_shortcut_targets(res, resolver=lambda p: target)
        exe = res.by_kind("game_exe")
        self.assertEqual(len(exe), 1)
        self.assertIn("target missing", exe[0].detail)  # never claims a file that isn't there
        self.assertTrue(res.by_kind("shortcut")[0].detail.endswith("SC3.exe"))

    def test_install_folder_for(self):
        self.assertEqual(install_folder_for("/x/SimCity 3000 Unlimited/Apps/SC3U.exe").name, "SimCity 3000 Unlimited")
        self.assertEqual(install_folder_for("/x/SC3/SC3.exe").name, "SC3")

    def test_empty(self):
        self.assertEqual(ScanResult([], [], 0, 0.0, True).by_kind("game_exe"), [])


if __name__ == "__main__":
    unittest.main()

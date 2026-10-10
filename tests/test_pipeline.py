"""End-to-end on synthetic frames: capture -> checks -> extract -> merge -> log."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mayor.config import load_config
from mayor.extractors import VisionExtractor
from mayor.extractors.roi_ocr import RoiOcrExtractor, crop_roi, roi_box
from mayor.frame_check import change_ratio, check_frame
from mayor.llm.mock import MockLLM
from mayor.observer import Observer
from mayor.sources import FileSource, SourceError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from make_mock_frames import MOCK_ROIS, make_frames  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FrameCheckTests(unittest.TestCase):
    def test_blank_and_change(self):
        black = Image.new("RGB", (800, 600))
        self.assertTrue(check_frame(black).blank)
        with tempfile.TemporaryDirectory() as d:
            p = make_frames(Path(d))
            a, b, c = (Image.open(x) for x in p[:3])
            self.assertFalse(check_frame(a).blank)
            self.assertEqual(change_ratio(a, b), 0.0)
            self.assertGreater(change_ratio(a, c), 0.05)

    def test_roi(self):
        img = Image.new("RGB", (800, 600))
        self.assertEqual(roi_box(img, (0.5, 0.5, 1.0, 1.0)), (400, 300, 800, 600))
        self.assertEqual(crop_roi(img, MOCK_ROIS["funds"]).size, (168, 33))
        with self.assertRaises(ValueError):
            roi_box(img, (0.5, 0.5, 0.4, 1.0))


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.frames = make_frames(self.dir / "frames_in")
        self.cfg = load_config(None)
        self.cfg["llm"].update(min_interval_s=60, min_change=0.05)

    def tearDown(self):
        self.tmp.cleanup()

    def _observer(self, response):
        clock = FakeClock()
        llm = MockLLM(response=response)
        obs = Observer(self.cfg, FileSource(str(self.dir / "frames_in")), self.dir / "run",
                       vision=VisionExtractor(llm), clock=clock)
        return obs, llm, clock

    def test_full_run(self):
        resp = {"fields": {"funds": {"value": "§20,000", "visible": True, "evidence": "hud"},
                           "game_date": {"value": "Jan 2050", "visible": True, "evidence": "hud"}}}
        obs, llm, clock = self._observer(resp)
        recs = []
        for _ in range(5):
            recs.append(obs.tick())
            clock.t += 120  # past min interval each tick
        obs.run(0, max_ticks=5)  # source exhausted: writes summary only

        # tick1: first call. tick2: identical frame -> no call, values stale. tick3: changed -> call.
        self.assertEqual([r.get("llm", {}).get("called") for r in recs], [True, False, True, None, True])
        self.assertIn("unchanged", recs[1]["llm"]["reason"])
        self.assertEqual(recs[1]["state"]["fields"]["funds"]["status"], "stale")
        self.assertEqual(recs[3]["status"], "blank_frame")
        self.assertEqual(recs[3]["state"]["fields"]["funds"]["status"], "stale")  # kept, but not claimed as seen
        self.assertEqual(recs[0]["state"]["fields"]["population"]["status"], "unknown")
        self.assertEqual(len(llm.calls), 3)

        run = self.dir / "run"
        lines = (run / "log.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 5)
        saved = sorted(p.name for p in (run / "frames").iterdir())
        self.assertEqual(saved, ["000001.png", "000003.png", "000005.png"])  # dup and blank not saved
        self.assertEqual(recs[1]["frame_file"], "frames/000001.png")
        self.assertEqual(json.loads((run / "latest_state.json").read_text())["tick"], 5)
        self.assertEqual(json.loads((run / "summary.json").read_text())["llm_calls"], 3)

    def test_min_interval_gate(self):
        obs, llm, clock = self._observer({"fields": {}})
        obs.tick()
        obs.tick()
        r = obs.tick()  # changed frame but clock did not advance
        self.assertFalse(r["llm"]["called"])
        self.assertIn("min interval", r["llm"]["reason"])

    def test_call_cap(self):
        self.cfg["llm"]["max_calls_per_run"] = 1
        obs, llm, clock = self._observer({"fields": {}})
        obs.tick()
        clock.t += 999
        obs.tick()
        r = obs.tick()
        self.assertIn("cap", r["llm"]["reason"])

    def test_missing_window_is_logged_not_fatal(self):
        class Gone:
            exhausted = False

            def grab(self):
                raise SourceError("no window")

        obs = Observer(self.cfg, Gone(), self.dir / "run2")
        r = obs.tick()
        self.assertEqual((r["status"], r["reason"]), ("no_frame", "no window"))

    def test_ocr_without_tesseract_degrades(self):
        ocr = RoiOcrExtractor(MOCK_ROIS)
        if ocr.ok:
            self.skipTest("tesseract present; covered by test_ocr_reads_mock_hud")
        s = ocr.extract(Image.open(self.frames[0]))
        self.assertTrue(any("ocr skipped" in w for w in s.warnings))
        self.assertTrue(all(f.status == "unknown" for f in s.fields.values()))

    def test_ocr_reads_mock_hud(self):
        ocr = RoiOcrExtractor(MOCK_ROIS)
        if not ocr.ok:
            self.skipTest(ocr.status)
        s = ocr.extract(Image.open(self.frames[2]))
        self.assertEqual(s.get("population").value, 1240)
        self.assertEqual(s.get("game_date").value, "Jun 2050")


if __name__ == "__main__":
    unittest.main()

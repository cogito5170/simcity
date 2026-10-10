"""The observe-and-record loop (stage 1 of observe -> decide -> act -> evaluate).

Each tick: capture -> code checks -> (OCR every tick, vision LLM rarely) ->
merge + sanity check -> append one JSON line to runs/<run>/log.jsonl.
Read-only: nothing here sends input to the game.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .extractors import RoiOcrExtractor, VisionExtractor, carry_forward, merge_states, sanity_check
from .frame_check import change_ratio, check_frame
from .sources import SourceError
from .state import CityState


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Observer:
    def __init__(self, cfg: dict, source, run_dir: Path, ocr: RoiOcrExtractor | None = None,
                 vision: VisionExtractor | None = None, clock=time.time):
        self.cfg = cfg
        self.source = source
        self.run_dir = Path(run_dir)
        (self.run_dir / "frames").mkdir(parents=True, exist_ok=True)
        self.ocr = ocr
        self.vision = vision
        self.clock = clock
        self.tick_no = 0
        self.state: CityState | None = None
        self.last_saved: tuple[Image.Image, str] | None = None
        self.last_llm: tuple[Image.Image, float] | None = None
        self.llm_calls = 0
        self.tokens = {"input": 0, "output": 0}

    # -------------------------------------------------------------- policy --
    def _llm_decision(self, img: Image.Image) -> tuple[bool, str]:
        """Plain-code gate for paid calls."""
        if self.vision is None:
            return False, "llm disabled"
        lc = self.cfg["llm"]
        if self.llm_calls >= lc["max_calls_per_run"]:
            return False, f"call cap {lc['max_calls_per_run']} reached"
        if self.last_llm is None:
            return True, "first reading"
        prev_img, prev_t = self.last_llm
        wait = lc["min_interval_s"] - (self.clock() - prev_t)
        if wait > 0:
            return False, f"min interval ({wait:.0f}s left)"
        ch = change_ratio(img, prev_img)
        if ch < lc["min_change"]:
            return False, f"screen unchanged since last call (change={ch})"
        return True, f"screen changed (change={ch})"

    # ---------------------------------------------------------------- tick --
    def tick(self) -> dict:
        self.tick_no += 1
        rec: dict = {"tick": self.tick_no, "time": now_iso()}
        try:
            frame = self.source.grab()
        except SourceError as e:
            rec.update(status="no_frame", reason=str(e))
            return self._finish(rec, CityState())
        img = frame.image
        rec["frame"] = {k: v for k, v in frame.meta.items() if k != "time"}
        prev_img = self.last_saved[0] if self.last_saved else None
        chk = check_frame(img, prev_img)
        rec["check"] = chk.to_dict()

        warnings = []
        if frame.meta.get("foreground") is False:
            warnings.append("game window not in foreground: capture may include overlapping windows")

        if chk.blank:
            rec.update(status="blank_frame",
                       reason="frame is (nearly) uniform/black: game minimized, loading, or fullscreen "
                              "DirectDraw output not capturable — try windowed mode")
            st = CityState()
            st.warnings += warnings
            return self._finish(rec, st)

        if self.last_saved is None or chk.change is None or chk.change >= self.cfg["save_frame_min_change"]:
            name = f"frames/{self.tick_no:06d}.png"
            img.save(self.run_dir / name)
            self.last_saved = (img, name)
        rec["frame_file"] = self.last_saved[1]

        readings = []
        if self.ocr is not None:
            readings.append(self.ocr.extract(img))
        call, why = self._llm_decision(img)
        rec["llm"] = {"called": call, "reason": why}
        if call:
            readings.append(self.vision.extract(img))
            self.llm_calls += 1
            self.last_llm = (img, self.clock())
            if self.vision.last_result is not None:
                u = self.vision.last_result.usage()
                rec["llm"]["usage"] = u
                self.tokens["input"] += u["input_tokens"] or 0
                self.tokens["output"] += u["output_tokens"] or 0
        rec["status"] = "ok"
        st = merge_states(*readings) if readings else CityState()
        st.warnings += warnings
        return self._finish(rec, st)

    def _finish(self, rec: dict, st: CityState) -> dict:
        st = sanity_check(st, self.state)
        st = carry_forward(st, self.state)
        self.state = st
        rec["state"] = st.to_dict()
        with open(self.run_dir / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        (self.run_dir / "latest_state.json").write_text(
            json.dumps({"tick": rec["tick"], "time": rec["time"], **st.to_dict()}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return rec

    # ---------------------------------------------------------------- loop --
    def run(self, interval_s: float, max_ticks: int | None = None, on_tick=None) -> None:
        try:
            while max_ticks is None or self.tick_no < max_ticks:
                if getattr(self.source, "exhausted", False):
                    break
                t0 = time.time()
                rec = self.tick()
                if on_tick:
                    on_tick(rec)
                if getattr(self.source, "exhausted", False) or (max_ticks and self.tick_no >= max_ticks):
                    break
                time.sleep(max(0.0, interval_s - (time.time() - t0)))
        except KeyboardInterrupt:
            pass
        finally:
            (self.run_dir / "summary.json").write_text(json.dumps({
                "ticks": self.tick_no, "llm_calls": self.llm_calls, "tokens": self.tokens, "ended": now_iso(),
            }, indent=2), encoding="utf-8")

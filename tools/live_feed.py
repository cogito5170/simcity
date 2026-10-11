"""Follow a running Micropolis mayor run and write live snapshots for the live page.

    python tools/live_feed.py RUN_DIR OUT.json [--every-months 12]

Prints one line per snapshot ("update 1912-01", "finished 2027-12"), so a watcher can push
OUT.json to wherever the page reads it. Reads only what the run already writes:
months.jsonl, reviews.jsonl, live_frames.u16 (every month's map, appended) and,
at the end, summary.json.

Each snapshot carries every month since the previous one as playable frames: the first
map in full and, for each later month, only the tiles that changed. The page plays them
back until the next snapshot arrives, so the city keeps moving month by month.
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

KPI = ["population", "funds", "cash_flow", "res_pop", "com_pop", "ind_pop", "res_valve", "com_valve", "ind_valve",
       "labor_base", "crime_avg", "pollution_avg", "land_value_avg", "traffic_avg", "police_pct", "fire_pct",
       "road_pct", "stadiums", "seaports", "airports", "powered_zones", "unpowered_zones", "fire_tiles", "flood_tiles"]
FRAME_KPI = ["population", "funds", "res_valve", "com_valve", "ind_valve", "labor_base"]
NAMES = {"place:residential": "주거", "place:commercial": "상업", "place:industrial": "공업", "place:police": "경찰서",
         "place:fire": "소방서", "power:coal": "석탄 발전소", "power:nuclear": "원자력 발전소",
         "special:stadium": "경기장", "special:seaport": "항구", "special:airport": "공항", "repair": "전력선·도로 수리"}


def _b64(a: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=dtype).tobytes()).decode()


class Follower:
    """Reads months.jsonl and live_frames.u16 (one 120x100 little-endian uint16 map per month,
    appended by the run) incrementally."""

    W, H = 120, 100

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.months: list[dict] = []
        self.maps: list[np.ndarray] = []
        self._pos = 0
        self._buf = ""

    def poll(self):
        p = self.run_dir / "months.jsonl"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
            self._buf += chunk
            *lines, self._buf = self._buf.split("\n")
            self.months += [json.loads(x) for x in lines if x.strip()]
        fp = self.run_dir / "live_frames.u16"
        size = self.W * self.H * 2
        if fp.exists():
            with open(fp, "rb") as f:
                f.seek(len(self.maps) * size)
                while True:
                    b = f.read(size)
                    if len(b) < size:
                        break
                    self.maps.append((np.frombuffer(b, "<u2").reshape(self.H, self.W) & 0x3FF).astype(np.uint16))

    def map_at(self, i: int) -> np.ndarray | None:
        if not self.maps:
            return None
        return self.maps[min(i, len(self.maps) - 1)]


def frames(f: Follower, start: int, end: int) -> dict | None:
    """Months [start, end] as a full first map plus per-month changed tiles."""
    base = f.map_at(start)
    if base is None:
        return None
    prev, diffs = base, []
    for i in range(start + 1, end + 1):
        cur = f.map_at(i)
        idx = np.flatnonzero(cur != prev)
        diffs.append(_b64((idx.astype(np.uint32) << 10) | cur.ravel()[idx], "<u4"))
        prev = cur
    ms = f.months[start:end + 1]
    return {"start": start, "w": int(base.shape[1]), "h": int(base.shape[0]), "base": _b64(base, "<u2"),
            "diffs": diffs, "dates": [m["date"] for m in ms], "tax": [m["tax_rate"] for m in ms],
            "kpi": {k: [round(float(m["stats"].get(k) or 0), 2) for m in ms] for k in FRAME_KPI}}


def snapshot(f: Follower, start: int, run_dir: Path, target_years: int) -> dict:
    months = f.months
    reviews = [json.loads(x) for x in (run_dir / "reviews.jsonl").read_text(encoding="utf-8").splitlines()
               if x.strip()] if (run_dir / "reviews.jsonl").exists() else []
    last = months[-1]
    s = last["stats"]
    events = []
    for m in months[-36:]:
        for a in m["actions"]:
            if a["status"] == "done":
                kind = a["action"].split(":")[0]
                label = NAMES.get(a["action"]) or ("재건: " + NAMES.get("place:" + a["action"].split(":")[-1], a["action"])
                                                   if kind == "rebuild" else a["action"])
                events.append({"date": m["date"], "text": label})
        for al in m.get("alerts", []):
            events.append({"date": m["date"], "text": "경보: " + al, "alert": True})
    revs = [{"date": r["date"], "reasoning": r.get("reasoning", [])[:4], "expected": r.get("expected", {}),
             "strategy": {k: r["strategy"].get(k) for k in (
                 "tax_rate", "zone_mix", "reserve_funds", "max_builds_per_month", "land_use", "road_funding",
                 "police_funding", "fire_funding", "repair_roads", "balance_jobs", "save_for_specials",
                 "build_specials")}} for r in reviews[-3:]]
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "finished" if (run_dir / "summary.json").exists() else "running",
        "run": run_dir.name, "month_index": last["month"], "months_total": target_years * 12,
        "date": last["date"], "tax_rate": last["tax_rate"],
        "kpi": {k: s.get(k) for k in KPI},
        "series": {"date0": months[0]["date"],
                   "population": [int(m["stats"]["population"]) for m in months],
                   "funds": [int(m["stats"]["funds"]) for m in months],
                   "labor_base": [round(m["stats"].get("labor_base", 0), 2) for m in months]},
        "reviews_total": len(reviews),
        "llm_cost_usd": round(sum((r.get("usage") or {}).get("cost_usd") or 0 for r in reviews), 2),
        "reviews": revs,
        "events": events[-14:][::-1],
        "frames": frames(f, start, len(months) - 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("out")
    ap.add_argument("--every-months", type=int, default=12)
    ap.add_argument("--years", type=int, default=128)
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()
    run_dir, out = Path(a.run_dir), Path(a.out)
    f = Follower(run_dir)
    f.poll()
    start = max(0, len(f.months) - a.every_months)   # joining a run already under way: send its last year
    sent = start
    while True:
        f.poll()
        done = (run_dir / "summary.json").exists()
        n = len(f.months)
        if n and (done or n - sent >= a.every_months) and n > sent:
            snap = snapshot(f, sent, run_dir, a.years)
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, out)
            sent = n
            print(("finished " if done else "update ") + snap["date"], flush=True)
        if done and n == sent:
            return
        time.sleep(a.poll)


if __name__ == "__main__":
    sys.exit(main())

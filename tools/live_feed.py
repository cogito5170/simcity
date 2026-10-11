"""Follow a running Micropolis mayor run and write a live snapshot for the live page.

    python tools/live_feed.py RUN_DIR OUT.json [--every-months 12]

Prints one line per snapshot ("update 1912-01", "finished 2027-12"), so a watcher can push
OUT.json to wherever the page reads it. Reads only what the run already writes:
months.jsonl, reviews.jsonl, live_tiles.npy and, at the end, summary.json.
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
NAMES = {"place:residential": "주거", "place:commercial": "상업", "place:industrial": "공업", "place:police": "경찰서",
         "place:fire": "소방서", "power:coal": "석탄 발전소", "power:nuclear": "원자력 발전소",
         "special:stadium": "경기장", "special:seaport": "항구", "special:airport": "공항", "repair": "전력선·도로 수리"}


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:   # the run may be mid-write on the last line
            break
    return out


def snapshot(run_dir: Path, target_years: int) -> dict | None:
    months = _lines(run_dir / "months.jsonl")
    if not months:
        return None
    reviews = _lines(run_dir / "reviews.jsonl")
    last = months[-1]
    s = last["stats"]
    try:
        tiles = np.load(run_dir / "live_tiles.npy") & 0x3FF
    except (OSError, ValueError):
        tiles = None
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
    revs = []
    for r in reviews[-3:]:
        revs.append({"date": r["date"], "reasoning": r.get("reasoning", [])[:4], "expected": r.get("expected", {}),
                     "strategy": {k: r["strategy"].get(k) for k in (
                         "tax_rate", "zone_mix", "reserve_funds", "max_builds_per_month", "land_use",
                         "road_funding", "police_funding", "fire_funding", "repair_roads", "balance_jobs",
                         "save_for_specials", "build_specials")}})
    summary = run_dir / "summary.json"
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "finished" if summary.exists() else "running",
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
        "map": None if tiles is None else {
            "w": int(tiles.shape[1]), "h": int(tiles.shape[0]),
            "b64": base64.b64encode(tiles.astype("<u2").tobytes()).decode()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("out")
    ap.add_argument("--every-months", type=int, default=12)
    ap.add_argument("--years", type=int, default=128)
    ap.add_argument("--poll", type=float, default=3.0)
    a = ap.parse_args()
    run_dir, out = Path(a.run_dir), Path(a.out)
    sent = -a.every_months
    while True:
        done = (run_dir / "summary.json").exists()
        n = sum(1 for _ in open(run_dir / "months.jsonl", encoding="utf-8")) if (run_dir / "months.jsonl").exists() else 0
        if n and (done or n - sent >= a.every_months):
            snap = snapshot(run_dir, a.years)
            if snap:
                tmp = out.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, out)
                sent = n
                print(("finished " if done else "update ") + snap["date"], flush=True)
        if done:
            return
        time.sleep(a.poll)


if __name__ == "__main__":
    sys.exit(main())

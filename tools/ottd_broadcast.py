"""Film the CEO's company in short video segments for the live page.

    python tools/ottd_broadcast.py RUN_DIR PAYLOAD_DIR [--seconds 90 --fps 3]

Each segment: pick a line to watch (the newest one first, then take turns), take `shotat` screenshots
of it for --seconds at --fps and encode an H.264 MP4 (uploaded to the page's asset store by the watcher;
the page's database only holds each clip's metadata). Also writes state.json (company, lines, latest
decision, money history). Prints one line per segment for the watcher:

    ready <seq> mp4=<path> delete=<metadata doc id that fell out of the window>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mayor.openttd.admin import Admin  # noqa: E402

MAX_B64 = 1_400_000    # keep each clip under ~1 MB so viewers load it quickly
KEEP = 4


def write_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_state(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _fit_zoom(dx: int, dy: int, w: int, h: int) -> int:
    """Smallest zoom (2 = normal, 3-5 = zoomed out 2x/4x/8x) at which a dx x dy tile box fits w x h pixels.
    An isometric box spans about (dx+dy)*32 px across and (dx+dy)*16 px down at normal zoom."""
    for z in (2, 3, 4, 5):
        s = 2 ** (z - 2)
        if (dx + dy) * 32 / s <= w and (dx + dy) * 16 / s <= h:
            return z
    return 5


def clusters(lines: list) -> list[list]:
    """Group lines that share a town or lie within 60 tiles of each other."""
    groups: list[list] = []
    for ln in lines:
        pts = [tuple(ln["a_xy"]), tuple(ln["b_xy"])]
        near = [g for g in groups if any(abs(p[0] - q[0]) + abs(p[1] - q[1]) <= 60
                                         for l2 in g for q in (tuple(l2["a_xy"]), tuple(l2["b_xy"])) for p in pts)]
        merged = [ln] + [x for g in near for x in g]
        groups = [g for g in groups if g not in near] + [merged]
    return groups


def pick_target(state: dict, seq: int, shown: dict) -> dict:
    """Mostly zoomed-out views of the whole network, so the structure is visible:
    a new line gets one close-up first; then each group of lines in turn, and every third shot the whole map."""
    lines = state.get("lines") or []
    if not lines:
        return {"label": "지도 전체", "x": 128, "y": 128, "zoom": 5, "w": 1920, "h": 1080, "line": None, "mode": "map"}
    newest = max(lines, key=lambda ln: ln["id"])
    if newest["id"] not in shown:
        shown[newest["id"]] = seq
        x, y = newest.get("mid") or newest["a_xy"]
        return {"label": f"새 노선 {newest['id']}: {newest['a']} – {newest['b']}", "x": x, "y": y, "zoom": 2,
                "w": 1280, "h": 720, "line": newest["id"], "mode": "new"}
    if seq % 3 == 0:
        return {"label": "지도 전체 (분홍 표지가 회사 정류장)", "x": 128, "y": 128, "zoom": 5, "w": 1920, "h": 1080,
                "line": None, "mode": "map"}
    groups = sorted(clusters(lines), key=lambda g: -len(g))
    g = groups[(seq // 3) % len(groups)]
    xs = [p[0] for ln in g for p in (ln["a_xy"], ln["b_xy"])]
    ys = [p[1] for ln in g for p in (ln["a_xy"], ln["b_xy"])]
    dx, dy = max(xs) - min(xs) + 6, max(ys) - min(ys) + 6
    towns = sorted({ln["a"] for ln in g} | {ln["b"] for ln in g})
    return {"label": f"노선망 {len(g)}개 노선: {', '.join(towns[:5])}{' 외' if len(towns) > 5 else ''}",
            "x": (min(xs) + max(xs)) // 2, "y": (min(ys) + max(ys)) // 2, "zoom": max(3, _fit_zoom(dx, dy, 1920, 1080)),
            "w": 1920, "h": 1080, "line": None, "mode": "network"}


def history(run_dir: Path, max_points: int = 400) -> list:
    try:
        rows = [json.loads(x) for x in (run_dir / "history.jsonl").read_text(encoding="utf-8").splitlines() if x]
    except OSError:
        return []
    step = max(1, len(rows) // max_points)
    return [{k: r[k] for k in ("date", "money", "value", "vehicles", "profit")} for r in rows[::step]]


def state_doc(run_dir: Path, state: dict, target: dict, seq: int) -> dict:
    lr = state.get("last_review") or {}
    dec = lr.get("decision") or {}
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "obs": state.get("obs"), "lines": state.get("lines", []), "events": (state.get("events") or [])[-14:][::-1],
        "ceo": state.get("ceo"), "reviews_total": state.get("reviews_total", 0),
        "last_review": {"date": lr.get("date"), "reasoning": dec.get("reasoning", [])[:5],
                        "actions": dec.get("actions", []), "results": lr.get("results", []),
                        "expected": dec.get("expected", {}), "source": dec.get("source"),
                        "candidates": [{k: c[k] for k in ("id", "a", "b", "a_pop", "b_pop", "distance",
                                                         "new_road_tiles", "est_cost_2_buses")}
                                       for c in (lr.get("candidates") or [])][:6]} if lr else None,
        "history": history(run_dir), "camera": target, "segment": seq,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("payload")
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--fps", type=int, default=3)
    ap.add_argument("--shots", default="/home/user/ottd-run/screenshot")
    ap.add_argument("--crf", type=int, default=30)
    a = ap.parse_args()
    run_dir, out, shots = Path(a.run_dir), Path(a.payload), Path(a.shots)
    out.mkdir(parents=True, exist_ok=True)
    adm = Admin(password="mayor", name="camera", gamescript=False)
    seq, shown = 0, {}
    while True:
        seq += 1
        state = load_state(run_dir)
        target = pick_target(state, seq, shown)
        frames = shots / f"cam{seq:05d}"
        frames.mkdir(parents=True, exist_ok=True)
        game_from = (state.get("obs") or {}).get("date")
        n, t0 = a.seconds * a.fps, time.time()
        for i in range(n):
            adm.rcon(f"shotat {target['x']} {target['y']} {target.get('w', 960)} {target.get('h', 540)} {target['zoom']} "
                     f"cam{seq:05d}/f{i:04d}")
            time.sleep(max(0.0, (i + 1) / a.fps - (time.time() - t0)))
        time.sleep(1.5)                                   # let the last screenshot finish writing
        mp4 = out / f"seg_{seq}.mp4"
        # under 0.5% of pixels change per frame, so one keyframe per segment keeps it small;
        # raise crf until the clip is small enough for viewers to load quickly
        crf = a.crf
        while True:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(frames / "f%04d.png"),
                            "-c:v", "libx264", "-preset", "medium", "-tune", "animation", "-crf", str(crf),
                            "-pix_fmt", "yuv420p", "-g", "9999", "-movflags", "+faststart", str(mp4)], check=True)
            if mp4.stat().st_size * 4 / 3 <= MAX_B64 or crf >= 44:
                break
            crf += 4
        shutil.rmtree(frames, ignore_errors=True)
        state = load_state(run_dir)
        meta = {"seq": seq, "label": target["label"], "line": target["line"], "crf": crf,
                "game_from": game_from, "game_to": (state.get("obs") or {}).get("date"),
                "seconds": a.seconds, "bytes": mp4.stat().st_size,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        write_json(out / f"seg_{seq}.json", meta)
        write_json(out / "state.json", state_doc(run_dir, state, target, seq))
        print(f"ready {seq} mp4={mp4} delete=s{seq - KEEP:05d}", flush=True)


if __name__ == "__main__":
    main()

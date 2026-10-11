"""Film the CEO's company in short video segments for the live page.

    python tools/ottd_broadcast.py RUN_DIR PAYLOAD_DIR [--seconds 90 --fps 3]

Each segment: pick a line to watch (the newest one first, then take turns), take `shotat` screenshots
of it for --seconds at --fps, encode an H.264 MP4, and split it base64-encoded into documents of at most
~240 KB (the page's database limit is 256 KB per document). Also writes state.json (company, lines,
latest decision, money history). Prints one line per segment for the watcher:

    ready <seq> parts=<n> delete=<doc ids of the segment that fell out of the window>
"""

from __future__ import annotations

import argparse
import base64
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

CHUNK = 240_000
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


def pick_target(state: dict, seq: int, shown: dict) -> dict:
    lines = state.get("lines") or []
    if not lines:
        return {"label": "회사 설립 준비 중", "x": 128, "y": 128, "zoom": 2, "line": None}
    newest = max(lines, key=lambda ln: ln["id"])
    if newest["id"] not in shown:              # a new line gets the next two segments: overview, then a stop
        ln, mode = newest, "overview"
        shown[ln["id"]] = 1
    elif shown.get(newest["id"]) == 1:
        ln, mode = newest, "stop"
        shown[ln["id"]] = 2
    else:                                      # then take turns: overview of one line, close-up of the next
        ln = lines[(seq // 2) % len(lines)]
        mode = "overview" if seq % 2 else "stop"
    label = f"노선 {ln['id']}: {ln['a']} – {ln['b']}"
    if mode == "stop":
        x, y = ln["stops"][seq % 2]
        town = ln["a"] if seq % 2 == 0 else ln["b"]
        return {"label": f"{label} · {town} 정류장", "x": x, "y": y, "zoom": 0, "line": ln["id"], "mode": mode}
    x, y = ln.get("mid") or ((ln["a_xy"][0] + ln["b_xy"][0]) // 2, (ln["a_xy"][1] + ln["b_xy"][1]) // 2)
    span = ln.get("span") or max(abs(ln["a_xy"][0] - ln["b_xy"][0]), abs(ln["a_xy"][1] - ln["b_xy"][1]))
    return {"label": f"{label} · 전체", "x": x, "y": y, "zoom": 1 if span <= 26 else 2, "line": ln["id"], "mode": mode}


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
    seq, shown, parts_of = 0, {}, {}
    while True:
        seq += 1
        state = load_state(run_dir)
        target = pick_target(state, seq, shown)
        frames = shots / f"cam{seq:05d}"
        frames.mkdir(parents=True, exist_ok=True)
        game_from = (state.get("obs") or {}).get("date")
        n, t0 = a.seconds * a.fps, time.time()
        for i in range(n):
            adm.rcon(f"shotat {target['x']} {target['y']} 960 540 {target['zoom']} cam{seq:05d}/f{i:04d}")
            time.sleep(max(0.0, (i + 1) / a.fps - (time.time() - t0)))
        time.sleep(1.5)                                   # let the last screenshot finish writing
        mp4 = out / f"seg_{seq}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(frames / "f%04d.png"),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(a.crf), "-pix_fmt", "yuv420p",
                        "-g", str(a.fps * 10), "-movflags", "+faststart", str(mp4)], check=True)
        shutil.rmtree(frames, ignore_errors=True)
        b64 = base64.b64encode(mp4.read_bytes()).decode()
        chunks = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
        state = load_state(run_dir)
        meta = {"seq": seq, "parts": len(chunks), "label": target["label"], "line": target["line"],
                "game_from": game_from, "game_to": (state.get("obs") or {}).get("date"),
                "seconds": a.seconds, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        for k, c in enumerate(chunks):
            write_json(out / f"seg_{seq}_{k}.json", meta | {"part": k, "b64": c})
        write_json(out / "state.json", state_doc(run_dir, state, target, seq))
        parts_of[seq] = len(chunks)
        old = seq - KEEP
        delete = [f"s{old:05d}p{k}" for k in range(parts_of.pop(old, 0))]
        print(f"ready {seq} parts={len(chunks)} delete={','.join(delete) or '-'}", flush=True)


if __name__ == "__main__":
    main()

"""Command line: python -m mayor <doctor|windows|calibrate|observe|report>"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import winenv
from .config import load_config
from .extractors import RoiOcrExtractor, VisionExtractor
from .extractors.roi_ocr import roi_box, tesseract_available
from .frame_check import check_frame
from .llm import DEFAULT_MODELS, make_llm
from .observer import Observer
from .sources import SourceError, make_source
from .state import CONFLICT, OBSERVED, REJECTED, STALE


def _source(cfg):
    return make_source(cfg["source"], cfg["window_title_pattern"], cfg["window_exe"], cfg["capture_method"])


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ------------------------------------------------------------------ doctor --
def cmd_doctor(cfg, args) -> int:
    out_dir = Path(cfg["runs_dir"]) / f"doctor-{_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rep: dict = {"platform": platform.platform(), "python": sys.version.split()[0],
                 "pillow": Image.__version__, "is_windows": winenv.IS_WINDOWS}
    print(f"platform   : {rep['platform']}\npython     : {rep['python']}\npillow     : {rep['pillow']}")

    if winenv.IS_WINDOWS:
        cands = winenv.find_game_windows(cfg["window_title_pattern"], cfg["window_exe"])
        rep["game_windows"] = [w.to_dict() for w in cands]
        if cands:
            for w in cands:
                print(f"game window: '{w.title}' exe={w.exe} size={w.size} minimized={w.minimized} foreground={w.foreground}")
        else:
            print("game window: NOT FOUND (is SimCity 3000 running? see `python -m mayor windows`)")
    else:
        print("game window: (not Windows — window discovery unavailable)")

    methods = ["rect", "hwnd"] if (winenv.IS_WINDOWS and cfg["source"] == "window") else [cfg["capture_method"]]
    rep["capture"] = {}
    for m in methods:
        try:
            frame = make_source(cfg["source"], cfg["window_title_pattern"], cfg["window_exe"], m).grab()
            chk = check_frame(frame.image)
            name = f"capture_{m}.png"
            frame.image.save(out_dir / name)
            rep["capture"][m] = {"ok": True, "file": name,
                                 "meta": {k: v for k, v in frame.meta.items() if k != "time"}, **chk.to_dict()}
            verdict = "BLANK — likely not capturable this way" if chk.blank else "looks like real content"
            print(f"capture[{m}] : {chk.width}x{chk.height} mean={chk.mean} std={chk.stddev} -> {verdict}")
            print(f"               saved {out_dir / name} — open it and confirm it shows the game")
        except (SourceError, RuntimeError, OSError) as e:
            rep["capture"][m] = {"ok": False, "error": str(e)}
            print(f"capture[{m}] : FAILED ({e})")
    good = [m for m, r in rep["capture"].items() if r.get("ok") and not r.get("blank")]
    if len(methods) > 1:
        print(f"suggestion : set \"capture_method\": \"{good[0]}\" in config.json" if good else
              "suggestion : no method gave a real image — try windowed mode (docs/ENVIRONMENT.md)")

    ok, status = tesseract_available()
    rep["ocr"] = {"available": ok, "status": status}
    print(f"ocr        : {status}")
    keys = {p: bool(os.environ.get(k)) for p, k in
            [("anthropic", "ANTHROPIC_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("openai", "OPENAI_API_KEY")]}
    keys["gemini"] = keys["gemini"] or bool(os.environ.get("GOOGLE_API_KEY"))
    rep["api_keys"] = keys
    print("api keys   : " + ", ".join(f"{p}={'set' if v else 'missing'}" for p, v in keys.items()))
    (out_dir / "doctor.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report     : {out_dir / 'doctor.json'}")
    return 0


# ----------------------------------------------------------------- install --
def cmd_install(cfg, args) -> int:
    from .install_info import inspect_install
    try:
        rep = inspect_install(args.folder)
    except FileNotFoundError as e:
        print(e)
        return 1
    print(f"folder  : {rep.root}\nexe     : {rep.exe}\nedition : {rep.edition_guess}")
    if rep.modules:
        print(f"\n{'module':<34} {'size':>11} {'built':<10} gzcom")
        for m in rep.modules:
            built = m.pe.built if m.pe else (m.error or "?")
            print(f"{m.path[-34:]:<34} {m.size:>11,} {built:<10} {'yes' if m.gzcom else ''}")
    print("\nfindings:")
    for f in rep.findings:
        print(f"  - {f}")
    out_dir = Path(cfg["runs_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"install-{_stamp()}.json"
    out.write_text(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport  : {out}  (contains file hashes only, no game data)")
    return 0


# ----------------------------------------------------------------- windows --
def cmd_windows(cfg, args) -> int:
    if not winenv.IS_WINDOWS:
        print("only available on Windows")
        return 1
    for w in sorted(winenv.list_windows(), key=lambda w: w.exe.lower()):
        mark = "*" if (w.exe.lower() in winenv.CANDIDATE_EXES) else " "
        print(f"{mark} {w.exe:<28} {w.size[0]:>5}x{w.size[1]:<5} {'min ' if w.minimized else ''}{w.title}")
    print("\n* = candidate game exe name. Put the real title/exe into config.json if detection is wrong.")
    return 0


# --------------------------------------------------------------- calibrate --
def draw_grid(img: Image.Image, step: float = 0.05) -> Image.Image:
    g = img.convert("RGB").copy()
    d = ImageDraw.Draw(g)
    font = ImageFont.load_default(size=max(12, img.height // 40))
    n = round(1 / step)
    for i in range(1, n):
        f = i * step
        x, y = round(f * img.width), round(f * img.height)
        major = i % 2 == 0
        color = (255, 0, 255) if major else (0, 255, 255)
        d.line([(x, 0), (x, img.height)], fill=color, width=1)
        d.line([(0, y), (img.width, y)], fill=color, width=1)
        if major:
            for pos in ((x + 2, 2), (2, y + 2)):
                box = d.textbbox(pos, f"{f:.2f}", font=font)
                d.rectangle(box, fill=(0, 0, 0))
                d.text(pos, f"{f:.2f}", fill=(255, 255, 0), font=font)
    return g


def cmd_calibrate(cfg, args) -> int:
    out_dir = Path(cfg["runs_dir"]) / f"calibrate-{_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        img = _source(cfg).grab().image
    except (SourceError, RuntimeError, OSError) as e:
        print(f"capture failed: {e}")
        return 1
    img.save(out_dir / "frame.png")
    draw_grid(img).save(out_dir / "grid.png")
    print(f"saved {out_dir / 'frame.png'} and grid.png (lines every 0.05 of width/height)")
    if cfg["rois"]:
        boxed = img.convert("RGB").copy()
        d = ImageDraw.Draw(boxed)
        for name, roi in cfg["rois"].items():
            box = roi_box(img, roi)
            img.crop(box).save(out_dir / f"roi_{name}.png")
            d.rectangle(box, outline=(255, 0, 0), width=2)
            d.text((box[0], max(0, box[1] - 12)), name, fill=(255, 0, 0))
        boxed.save(out_dir / "rois.png")
        print(f"saved rois.png and roi_<field>.png crops for {list(cfg['rois'])} — check each crop holds just that value")
    else:
        print('no ROIs configured yet: read fractions off grid.png and add e.g. "rois": {"funds": [0.10, 0.95, 0.22, 0.99]} to config.json')
    return 0


# ----------------------------------------------------------------- observe --
def _fmt(f: dict) -> str:
    v, s = f["value"], f["status"]
    if s == OBSERVED:
        return str(v)
    if s == STALE:
        return f"{v}*"
    return {CONFLICT: "!conflict", REJECTED: "!rejected"}.get(s, "?")


COLS = [("view", 14), ("game_date", 10), ("funds", 10), ("population", 10),
        ("demand_residential", 8), ("demand_commercial", 8), ("demand_industrial", 8)]


def _row(rec: dict) -> str:
    f = rec["state"]["fields"]
    cells = [f"{_fmt(f[c])[:w]:<{w}}" for c, w in COLS]
    extra = rec.get("status", "")
    if rec.get("llm", {}).get("called"):
        extra += " +llm"
    return f"{rec['tick']:>4} {rec['time'][11:19]} " + " ".join(cells) + f"  {extra}"


def _header() -> str:
    return "tick time     " + " ".join(f"{c[:w]:<{w}}" for c, w in COLS)


def cmd_observe(cfg, args) -> int:
    if args.source:
        cfg["source"] = args.source
    if args.extract is not None:
        cfg["extract"] = [x for x in args.extract.split(",") if x and x != "none"]
    if args.provider:
        cfg["llm"]["provider"] = args.provider
    if args.model:
        cfg["llm"]["model"] = args.model
    if args.interval is not None:
        cfg["interval_s"] = args.interval
    run_dir = Path(cfg["runs_dir"]) / f"run-{_stamp()}"
    source = _source(cfg)

    ocr = RoiOcrExtractor(cfg["rois"], cfg["tesseract_cmd"]) if "ocr" in cfg["extract"] and cfg["rois"] else None
    if "ocr" in cfg["extract"] and not cfg["rois"]:
        print("note: OCR requested but no ROIs configured (run `calibrate`); OCR disabled")
    elif ocr and not ocr.ok:
        print(f"note: OCR unavailable — {ocr.status}")
    vision = None
    if "llm" in cfg["extract"]:
        lc = cfg["llm"]
        vision = VisionExtractor(make_llm(lc["provider"], lc["model"], response_file=lc.get("mock_response_file")),
                                 lc["max_width"])
        print(f"vision: {lc['provider']}/{lc['model'] or DEFAULT_MODELS.get(lc['provider'])} "
              f"(≤1 call per {lc['min_interval_s']}s, only on screen change, cap {lc['max_calls_per_run']})")

    obs = Observer(cfg, source, run_dir, ocr=ocr, vision=vision)
    with open(run_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"recording to {run_dir}  (Ctrl+C to stop; read-only, no input is sent to the game)")
    print(_header())

    def show(rec):
        print(_row(rec))
        for w in rec["state"]["warnings"]:
            print(f"      ! {w}")
        if rec.get("reason") and rec.get("status") != "ok":
            print(f"      - {rec['reason']}")

    obs.run(cfg["interval_s"], 1 if args.once else args.max_ticks, on_tick=show)
    print(f"done: {obs.tick_no} ticks, {obs.llm_calls} vision calls, tokens {obs.tokens}. Log: {run_dir / 'log.jsonl'}")
    return 0


# ------------------------------------------------------------------ report --
def cmd_report(cfg, args) -> int:
    path = Path(args.run)
    log = path / "log.jsonl" if path.is_dir() else path
    if not log.exists():
        print(f"no log at {log}")
        return 1
    print(_header())
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            print(_row(json.loads(line)))
    print("\nlegend: value = observed now, value* = stale (last seen earlier), ? = unknown, !conflict / !rejected")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mayor", description="SimCity 3000 AI Mayor — observer prototype")
    p.add_argument("--config", help="config JSON (default: ./config.json if present)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check environment, find the game window, test one capture")
    sub.add_parser("windows", help="list all visible windows (Windows only)")
    i = sub.add_parser("install", help="inspect the game's install folder (edition, GZCOM plugin support)")
    i.add_argument("folder", help='e.g. "C:\\Program Files\\Maxis\\SimCity 3000"')
    sub.add_parser("calibrate", help="save a frame + coordinate grid (+ ROI crops) to set up OCR regions")
    o = sub.add_parser("observe", help="observe the city and record its state")
    o.add_argument("--source", help="window | screen | file:PATH")
    o.add_argument("--extract", help="comma list: ocr,llm  (or 'none')")
    o.add_argument("--provider", help="anthropic | gemini | openai | mock")
    o.add_argument("--model")
    o.add_argument("--interval", type=float)
    o.add_argument("--once", action="store_true")
    o.add_argument("--max-ticks", type=int)
    r = sub.add_parser("report", help="print the state timeline of a run")
    r.add_argument("run", help="run directory or log.jsonl")
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    return {"doctor": cmd_doctor, "windows": cmd_windows, "install": cmd_install, "calibrate": cmd_calibrate,
            "observe": cmd_observe, "report": cmd_report}[args.cmd](cfg, args)

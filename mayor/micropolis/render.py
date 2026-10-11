"""Turn a run into pictures: one frame per game month, an MP4 growth video, key frames."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HUD_H = 76
_FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf"]


def _font(size: int):
    for p in _FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def crop_box(tiles: np.ndarray, margin: int = 5) -> tuple[int, int, int, int]:
    """Smallest box (y0, y1, x0, x1) holding every non-dirt tile of any frame, plus a margin."""
    used = np.any(np.asarray(tiles) >= 44, axis=0)   # rubble/flood/fire/roads/power/buildings, not dirt/water/trees
    if not used.any():
        h, w = used.shape
        return 0, h, 0, w
    ys, xs = np.where(used)
    h, w = used.shape
    return (max(0, ys.min() - margin), min(h, ys.max() + 1 + margin),
            max(0, xs.min() - margin), min(w, xs.max() + 1 + margin))


def _event_text(month: dict) -> str:
    done = [a for a in month["actions"] if a["status"] == "done"]
    names = {"place:residential": "R zone", "place:commercial": "C zone", "place:industrial": "I zone",
             "place:police": "police", "place:fire": "fire station", "power:coal": "coal plant",
             "power:nuclear": "nuclear plant", "special:stadium": "STADIUM", "special:seaport": "SEAPORT",
             "special:airport": "AIRPORT"}
    parts = [names.get(a["action"], a["action"]) for a in done]
    text = ("built: " + ", ".join(parts)) if parts else ""
    if month.get("reviewed"):
        text = (text + "   " if text else "") + f"[strategy review: tax {month['tax_rate']}%]"
    return text


def frame(tiles_mod, tiles: np.ndarray, box, month: dict) -> Image.Image:
    y0, y1, x0, x1 = box
    city = Image.fromarray(tiles_mod.render_tiles(tiles[y0:y1, x0:x1], 16))
    w = max(city.width, 640)
    img = Image.new("RGB", (w + (w % 2), city.height + HUD_H + ((city.height + HUD_H) % 2)), (24, 26, 32))
    img.paste(city, ((w - city.width) // 2, 0))
    d = ImageDraw.Draw(img)
    s = month["stats"]
    big, small = _font(22), _font(15)
    y = city.height + 8
    d.text((12, y), month["date"], fill=(255, 255, 255), font=big)
    d.text((130, y + 2), f"pop {int(s['population']):,}   funds ${int(s['funds']):,}   tax {month['tax_rate']}%",
           fill=(220, 220, 220), font=small)
    # R/C/I demand bars (engine valves, -1..1)
    bx = img.width - 150
    for i, (k, col, vmax) in enumerate((("res_valve", (80, 200, 90), 2000), ("com_valve", (90, 140, 230), 1500),
                                       ("ind_valve", (230, 200, 70), 1500))):
        v = max(-1.0, min(1.0, s[k] / vmax))
        cx, mid = bx + i * 40, y + 28
        d.rectangle([cx, mid - 1, cx + 24, mid + 1], fill=(90, 90, 90))
        d.rectangle([cx, min(mid, mid - v * 24), cx + 24, max(mid, mid - v * 24)], fill=col)
        d.text((cx + 7, y + 52), "RCI"[i], fill=(200, 200, 200), font=small)
    d.text((12, y + 36), _event_text(month)[:110], fill=(255, 210, 120), font=small)
    return img


def video_fps(months: int, base: int = 8, max_seconds: int = 90) -> int:
    """8 fps (a game year in 1.5 s) unless that would make the video longer than ~90 s."""
    return max(base, -(-months // max_seconds))


def render_run(run, out_dir: Path, fps: int | None = None) -> dict:
    """Write video.mp4 (if ffmpeg exists), poster.png and yearly keyframes into out_dir.

    Frames are streamed to ffmpeg one by one, so a 100-year run does not hold
    1,200+ full-size images in memory."""
    from .engine import load_modules
    _, tiles_mod = load_modules()
    out_dir.mkdir(parents=True, exist_ok=True)
    kdir = out_dir / "keyframes"
    kdir.mkdir(exist_ok=True)
    tiles = np.stack(run.tiles)
    box = crop_box(tiles)
    fps = fps or video_fps(len(run.months))
    ffmpeg = shutil.which("ffmpeg")
    proc = None
    keyframes = []
    img = None
    for i, month in enumerate(run.months):
        img = frame(tiles_mod, tiles[i], box, month)
        if ffmpeg and proc is None:
            w, h = img.size
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                   "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
                   "-movflags", "+faststart", str(out_dir / "video.mp4")]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        if proc:
            proc.stdin.write(img.tobytes())
        if month["date"].endswith("-01") or i == len(run.months) - 1:
            name = f"keyframes/{month['date']}.png"
            img.save(out_dir / name)
            keyframes.append({"date": month["date"], "file": name})
    img.save(out_dir / "poster.png")
    result = {"keyframes": keyframes, "poster": "poster.png", "video": None, "size": img.size, "fps": fps}
    if proc:
        for _ in range(fps * 2):   # pause on the final city
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
        if proc.wait() == 0:
            result["video"] = "video.mp4"
    return result

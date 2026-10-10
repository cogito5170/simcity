"""Generate SYNTHETIC test frames (not real SimCity 3000 screenshots).

They only exercise the pipeline: change detection, blank detection, ROI
cropping, logging. The HUD layout here is invented; real ROIs must be
calibrated on a real screenshot.

    python tools/make_mock_frames.py OUT_DIR
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ('$' because Pillow's default font has no '§' glyph.)
# Invented HUD positions for the mock frames (fractions of 800x600).
MOCK_ROIS = {
    "game_date": [0.01, 0.935, 0.17, 0.99],
    "funds": [0.19, 0.935, 0.40, 0.99],
    "population": [0.42, 0.935, 0.64, 0.99],
}


def city_frame(date: str, funds: str, pop: str, seed: int, size=(800, 600)) -> Image.Image:
    w, h = size
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (70, 120, 60))
    d = ImageDraw.Draw(img)
    for _ in range(140):  # "buildings"
        x, y = rnd.randrange(0, w - 30), rnd.randrange(0, int(h * 0.9) - 30)
        c = rnd.choice([(60, 160, 60), (60, 90, 200), (200, 190, 60), (150, 150, 150)])
        d.rectangle([x, y, x + rnd.randrange(8, 30), y + rnd.randrange(8, 30)], fill=c)
    d.rectangle([0, int(h * 0.93), w, h], fill=(30, 30, 40))
    font = ImageFont.load_default(size=20)
    for (name, roi), text in zip(MOCK_ROIS.items(), [date, funds, pop]):
        d.text((roi[0] * w + 4, roi[1] * h + 6), text, fill=(240, 240, 240), font=font)
    d.text((w * 0.70, h * 0.945), "SYNTHETIC", fill=(255, 80, 80), font=font)
    return img


def make_frames(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    frames = [
        city_frame("Jan 2050", "$20,000", "0", seed=1),
        city_frame("Jan 2050", "$20,000", "0", seed=1),        # identical -> no new save
        city_frame("Jun 2050", "$14,250", "1,240", seed=2),    # changed
        Image.new("RGB", (800, 600), (0, 0, 0)),                # black: fullscreen-capture failure
        city_frame("Feb 2051", "-$3,100", "2,980", seed=3),
    ]
    paths = []
    for i, f in enumerate(frames, 1):
        p = out / f"{i:03d}.png"
        f.save(p)
        paths.append(p)
    return paths


if __name__ == "__main__":
    for p in make_frames(Path(sys.argv[1] if len(sys.argv) > 1 else "samples/synthetic")):
        print(p)

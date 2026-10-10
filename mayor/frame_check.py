"""Cheap, code-only checks on a captured frame (no AI needed).

- is the frame blank/black? (typical symptom of capturing an exclusive
  fullscreen DirectDraw game with GDI, or a minimized window)
- how much did it change since a reference frame? (used to skip redundant
  saves and redundant paid vision calls)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from PIL import Image, ImageChops, ImageStat

_THUMB = (160, 120)


def _thumb(img: Image.Image) -> Image.Image:
    return img.convert("L").resize(_THUMB, Image.BILINEAR)


@dataclass
class FrameCheck:
    width: int
    height: int
    mean: float
    stddev: float
    blank: bool
    change: float | None  # fraction of thumbnail pixels that changed; None if no reference

    def to_dict(self) -> dict:
        return asdict(self)


def check_frame(img: Image.Image, previous: Image.Image | None = None,
                blank_mean: float = 8.0, blank_std: float = 3.0) -> FrameCheck:
    t = _thumb(img)
    st = ImageStat.Stat(t)
    mean, std = st.mean[0], st.stddev[0]
    blank = mean < blank_mean or std < blank_std
    change = change_ratio(img, previous) if previous is not None else None
    return FrameCheck(img.width, img.height, round(mean, 2), round(std, 2), blank, change)


def change_ratio(a: Image.Image, b: Image.Image, pixel_threshold: int = 16) -> float:
    diff = ImageChops.difference(_thumb(a), _thumb(b))
    hist = diff.histogram()
    changed = sum(hist[pixel_threshold:])
    return round(changed / (_THUMB[0] * _THUMB[1]), 4)

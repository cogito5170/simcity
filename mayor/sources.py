"""Where frames come from.

- WindowSource: the game window's client area on the Windows desktop
- ScreenSource: the whole (virtual) screen
- FileSource:   a PNG/JPG or a folder of them, for offline replay and tests
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import winenv


@dataclass
class Frame:
    image: Image.Image
    meta: dict = field(default_factory=dict)


class SourceError(RuntimeError):
    """Capturing is not possible right now (window missing, minimized...). Not fatal for a loop."""


class FileSource:
    def __init__(self, path: str, loop: bool = False):
        p = Path(path)
        if p.is_dir():
            self.files = sorted(f for f in p.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"))
        else:
            self.files = [p]
        if not self.files:
            raise SourceError(f"no images in {path}")
        self.loop = loop
        self.i = 0

    @property
    def exhausted(self) -> bool:
        return not self.loop and self.i >= len(self.files)

    def grab(self) -> Frame:
        if self.exhausted:
            raise StopIteration
        f = self.files[self.i % len(self.files)]
        self.i += 1
        img = Image.open(f).convert("RGB")
        return Frame(img, {"source": "file", "path": str(f)})


def _grab_bbox(bbox: tuple[int, int, int, int] | None) -> Image.Image:
    from PIL import ImageGrab  # imported lazily: needs a display on Linux

    return ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")


class ScreenSource:
    exhausted = False

    def grab(self) -> Frame:
        winenv.make_dpi_aware()
        return Frame(_grab_bbox(None), {"source": "screen", "time": time.time()})


class WindowSource:
    """Captures the game's client area.

    method "rect": a screen grab of that rectangle — if another window covers the
    game, that window is captured (frame meta records `foreground`).
    method "hwnd": Pillow asks Windows for the window's own image; may work when
    covered, may be black for DirectDraw output. `doctor` tests both.
    """

    exhausted = False
    METHODS = ("rect", "hwnd")

    def __init__(self, title_pattern: str = winenv.DEFAULT_TITLE_PATTERN, exe: str | None = None,
                 method: str = "rect"):
        if method not in self.METHODS:
            raise ValueError(f"capture method must be one of {self.METHODS}")
        self.title_pattern = title_pattern
        self.exe = exe
        self.method = method  # rect: screen pixels of the client area; hwnd: ask Windows for the window's own image
        self.win: winenv.WindowInfo | None = None

    def _locate(self) -> winenv.WindowInfo:
        w = winenv.refresh(self.win) if self.win else None
        if w is None:
            found = winenv.find_game_windows(self.title_pattern, self.exe)
            if not found:
                raise SourceError(f"no window matching /{self.title_pattern}/ or known exe names; "
                                  "run `python -m mayor windows` to see what is open")
            if len(found) > 1:
                found.sort(key=lambda x: x.size[0] * x.size[1], reverse=True)
            w = found[0]
        self.win = w
        return w

    def grab(self) -> Frame:
        w = self._locate()
        if w.minimized:
            raise SourceError("game window is minimized")
        cw, ch = w.size
        if cw <= 0 or ch <= 0:
            raise SourceError(f"game window has empty client area {w.client_rect}")
        if self.method == "hwnd":
            from PIL import ImageGrab
            try:
                img = ImageGrab.grab(window=w.hwnd).convert("RGB")
            except TypeError as e:  # Pillow too old for window=
                raise SourceError(f"capture method 'hwnd' needs a newer Pillow ({e})") from e
        else:
            img = _grab_bbox(w.client_rect)
        return Frame(img, {"source": "window", "method": self.method, "title": w.title, "exe": w.exe,
                           "hwnd": w.hwnd, "rect": w.client_rect, "foreground": w.foreground, "time": time.time()})


def make_source(spec: str, title_pattern: str = winenv.DEFAULT_TITLE_PATTERN, exe: str | None = None,
                method: str = "rect"):
    if spec == "window":
        return WindowSource(title_pattern, exe, method)
    if spec == "screen":
        return ScreenSource()
    if spec.startswith("file:"):
        return FileSource(spec[5:])
    raise ValueError(f"unknown source {spec!r} (use window | screen | file:PATH)")

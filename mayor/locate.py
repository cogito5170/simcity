"""Find where SimCity 3000 lives on this PC, and anything that hints at it.

Read-only. Collects "clues" rather than assuming one install layout:
  game_exe  SC3.exe / SC3U.exe
  shortcut  .lnk whose name mentions the game (Start menu, Desktop), with its target on Windows
  exe_like  other .exe whose name mentions the game (setup programs, renamed launchers)
  archive   .iso/.zip/... whose name mentions the game (downloaded but maybe not installed)
  readme    small text file whose content mentions the game (e.g. the Korean Mediasoft README)
  folder    folder whose name mentions the game
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .install_info import EDITION_BY_EXE

NAME_HINT = re.compile(r"sim\s*city|심시티|^sc3|maxis", re.I)
README_MARKERS = [b"mediasoft.co.kr/simcity3000", b"SimCity 3000", "심시티 3000".encode("cp949"),
                  "심시티 3000".encode("utf-8")]
ARCHIVE_EXT = {".iso", ".zip", ".7z", ".rar", ".bin", ".cue", ".mdf", ".mds", ".nrg", ".img"}
SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "winsxs", "node_modules", ".git",
             "appdata", "$windows.~bt", "$windows.~ws", "msocache", "recovery", "perflogs"}
# Cloud placeholders (OneDrive etc.): opening them would download the file. Never read those.
_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000  # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
KIND_ORDER = ["game_exe", "shortcut", "readme", "exe_like", "archive", "folder"]


@dataclass
class Clue:
    kind: str
    path: str
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanResult:
    clues: list[Clue]
    roots: list[str]
    dirs_scanned: int
    seconds: float
    complete: bool

    def by_kind(self, kind: str) -> list[Clue]:
        return [c for c in self.clues if c.kind == kind]


def _is_readme_candidate(name: str, size: int) -> bool:
    n = name.lower()
    return size < 256_000 and (n.startswith("readme") or n.startswith("read me") or n.endswith(".txt")
                               or "추가설명" in n or "읽어" in n)


def scan(roots: list[tuple[Path, int]], time_budget_s: float = 90.0, max_readme_reads: int = 4000,
         max_clues: int = 300) -> ScanResult:
    """Depth-limited, permission-tolerant walk over (root, max_depth) pairs."""
    t0 = time.monotonic()
    clues: list[Clue] = []
    seen_dirs: set[str] = set()
    readme_reads = 0
    dirs = 0
    complete = True
    for root, max_depth in roots:
        stack = [(root, 0)]
        while stack:
            if time.monotonic() - t0 > time_budget_s or len(clues) >= max_clues:
                complete = False
                stack.clear()
                break
            d, depth = stack.pop()
            key = os.path.normcase(os.path.abspath(d))
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            dirs += 1
            for e in entries:
                try:
                    name = e.name
                    if e.is_dir(follow_symlinks=False):
                        if NAME_HINT.search(name):
                            clues.append(Clue("folder", e.path))
                        if depth < max_depth and name.lower() not in SKIP_DIRS:
                            stack.append((Path(e.path), depth + 1))
                        continue
                    if not e.is_file():
                        continue
                    low = name.lower()
                    suffix = os.path.splitext(low)[1]
                    if low in EDITION_BY_EXE:
                        clues.append(Clue("game_exe", e.path, EDITION_BY_EXE[low]))
                    elif suffix == ".lnk" and NAME_HINT.search(name):
                        clues.append(Clue("shortcut", e.path))
                    elif suffix == ".exe" and NAME_HINT.search(name):
                        clues.append(Clue("exe_like", e.path))
                    elif suffix in ARCHIVE_EXT and NAME_HINT.search(name):
                        clues.append(Clue("archive", e.path, f"{e.stat().st_size:,} bytes"))
                    elif (readme_reads < max_readme_reads and _is_readme_candidate(name, e.stat().st_size)
                          and not getattr(e.stat(), "st_file_attributes", 0) & _CLOUD_ONLY):
                        readme_reads += 1
                        with open(e.path, "rb") as f:
                            data = f.read()
                        hit = next((m for m in README_MARKERS if m in data), None)
                        if hit is not None:
                            clues.append(Clue("readme", e.path, "mentions the game"))
                except OSError:
                    continue
    clues.sort(key=lambda c: (KIND_ORDER.index(c.kind), c.path.lower()))
    return ScanResult(clues, [str(r) for r, _ in roots], dirs, round(time.monotonic() - t0, 1), complete)


# ------------------------------------------------------------ Windows bits --
def _fixed_and_cd_drives() -> list[Path]:
    if sys.platform != "win32":
        return []
    import ctypes
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    out = []
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(65 + i)}:\\"
            if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) in (3, 5):  # fixed, CD-ROM
                out.append(Path(root))
    return out


def default_roots() -> list[tuple[Path, int]]:
    env = os.environ
    cand: list[tuple[str | None, int]] = [
        (env.get("ProgramData") and os.path.join(env["ProgramData"], r"Microsoft\Windows\Start Menu"), 5),
        (env.get("APPDATA") and os.path.join(env["APPDATA"], r"Microsoft\Windows\Start Menu"), 5),
        (env.get("PUBLIC") and os.path.join(env["PUBLIC"], "Desktop"), 2),
        (env.get("ProgramFiles(x86)"), 4),
        (env.get("ProgramFiles"), 4),
        (env.get("USERPROFILE"), 5),
    ]
    roots = [(Path(p), depth) for p, depth in cand if p and os.path.isdir(p)]
    roots += [(d, 4) for d in _fixed_and_cd_drives()]
    return roots


def resolve_shortcut(path: str) -> str | None:
    """Target of a .lnk via the Windows Script Host (Windows only)."""
    if sys.platform != "win32":
        return None
    p = path.replace("'", "''")
    cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
           f"(New-Object -ComObject WScript.Shell).CreateShortcut('{p}').TargetPath")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.decode("utf-8", "replace").strip()
    return out or None


def add_shortcut_targets(result: ScanResult, resolver=resolve_shortcut) -> None:
    """Fill shortcut targets; a shortcut pointing at SC3(U).exe also yields a game_exe clue."""
    extra = []
    known = {os.path.normcase(c.path) for c in result.by_kind("game_exe")}
    for c in result.by_kind("shortcut"):
        target = resolver(c.path)
        if not target:
            continue
        c.detail = f"-> {target}"
        name = os.path.basename(target).lower()
        if name in EDITION_BY_EXE and os.path.normcase(target) not in known:
            exists = os.path.isfile(target)
            extra.append(Clue("game_exe", target, EDITION_BY_EXE[name] + ("" if exists else " (shortcut target missing!)")))
            known.add(os.path.normcase(target))
    result.clues = sorted(result.clues + extra, key=lambda c: (KIND_ORDER.index(c.kind), c.path.lower()))


def install_folder_for(exe_path: str) -> Path:
    exe = Path(exe_path)
    return exe.parent.parent if exe.parent.name.lower() == "apps" else exe.parent

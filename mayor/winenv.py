"""Windows desktop inspection via ctypes (no pywin32 needed).

Read-only: lists windows, finds the game window, reports its client-area
rectangle in screen pixels. Never sends input to any window.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import asdict, dataclass

IS_WINDOWS = sys.platform == "win32"

# Candidate identifiers. These are *guesses* to narrow the list; the
# `windows` command shows everything so the real title/exe can be confirmed.
DEFAULT_TITLE_PATTERN = r"sim\s*city\s*3000"
CANDIDATE_EXES = {"sc3u.exe", "sc3.exe", "simcity 3000.exe"}


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe: str
    client_rect: tuple[int, int, int, int]  # left, top, right, bottom in screen pixels
    minimized: bool
    foreground: bool

    @property
    def size(self) -> tuple[int, int]:
        l, t, r, b = self.client_rect
        return r - l, b - t

    def to_dict(self) -> dict:
        d = asdict(self)
        d["size"] = self.size
        return d


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    _dpi_done = False

    def make_dpi_aware() -> None:
        """So window rects and screenshots use the same (physical) pixels."""
        global _dpi_done
        if _dpi_done:
            return
        _dpi_done = True
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    def _exe_name(pid: int) -> str:
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(h)

    def _client_rect(hwnd) -> tuple[int, int, int, int]:
        rc = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom

    def list_windows() -> list[WindowInfo]:
        make_dpi_aware()
        fg = user32.GetForegroundWindow()
        out: list[WindowInfo] = []

        def cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append(WindowInfo(
                hwnd=int(hwnd), title=buf.value, pid=pid.value, exe=_exe_name(pid.value),
                client_rect=_client_rect(hwnd), minimized=bool(user32.IsIconic(hwnd)),
                foreground=(hwnd == fg),
            ))
            return True

        user32.EnumWindows(_EnumWindowsProc(cb), 0)
        return out

    def refresh(win: WindowInfo) -> WindowInfo | None:
        """Re-read the rect/state of a window found earlier (it may have moved or closed)."""
        for w in list_windows():
            if w.hwnd == win.hwnd:
                return w
        return None

else:
    def make_dpi_aware() -> None:
        pass

    def list_windows() -> list[WindowInfo]:
        raise RuntimeError("window discovery only works on Windows; use --source file:PATH elsewhere")

    def refresh(win: WindowInfo) -> WindowInfo | None:
        raise RuntimeError("window discovery only works on Windows")


def find_game_windows(title_pattern: str = DEFAULT_TITLE_PATTERN, exe: str | None = None) -> list[WindowInfo]:
    rx = re.compile(title_pattern, re.I)
    exes = {exe.lower()} if exe else CANDIDATE_EXES
    return [w for w in list_windows() if rx.search(w.title) or w.exe.lower() in exes]

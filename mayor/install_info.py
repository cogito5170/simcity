"""Read-only inspection of a SimCity 3000 install folder.

Answers, from the files themselves rather than from assumptions:
- which edition is this? (SC3U.exe = Unlimited, SC3.exe = original — by file name)
- does it use Maxis's GZCOM framework? (modules exporting GZDllGetGZCOMDirector)
- does the exe look like it scans for plugin DLLs? (strings such as "*.dll", "PlugIn")
- exact build fingerprints (size, PE timestamp, sha256) to compare with known builds

Nothing is modified. Works on any OS (pure Python PE parsing).
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

GZCOM_EXPORT = "GZDllGetGZCOMDirector"
EDITION_BY_EXE = {"sc3u.exe": "SimCity 3000 Unlimited", "sc3.exe": "SimCity 3000 (original)"}
# Byte patterns seen in SC3U.exe by the gta-mode reverse engineering notes (MODLOG.md).
PLUGIN_SCAN_HINTS = [b"*.dll", b"PlugIn", b"addon-enabled", b"%PluginDirectory%"]
MACHINES = {0x14C: "x86 (32-bit)", 0x8664: "x64", 0xAA64: "arm64"}


class PEError(ValueError):
    pass


@dataclass
class PEInfo:
    machine: str
    timestamp: int
    built: str
    is_dll: bool
    exports: list[str] = field(default_factory=list)


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def parse_pe(data: bytes) -> PEInfo:
    """Minimal PE header + export-name parser."""
    try:
        if data[:2] != b"MZ":
            raise PEError("not an MZ executable")
        pe = _u32(data, 0x3C)
        if data[pe:pe + 4] != b"PE\0\0":
            raise PEError("no PE signature")
        coff = pe + 4
        machine, nsec = _u16(data, coff), _u16(data, coff + 2)
        ts, opt_size, chars = _u32(data, coff + 4), _u16(data, coff + 16), _u16(data, coff + 18)
        opt = coff + 20
        magic = _u16(data, opt)
        dd = opt + (96 if magic == 0x10B else 112)  # data directories (PE32 / PE32+)
        exp_rva = _u32(data, dd) if opt_size > dd - opt else 0
        sections = []
        for i in range(nsec):
            s = opt + opt_size + 40 * i
            vsize, va, rsize, rptr = _u32(data, s + 8), _u32(data, s + 12), _u32(data, s + 16), _u32(data, s + 20)
            sections.append((va, max(vsize, rsize), rptr))

        def off(rva: int) -> int:
            for va, size, rptr in sections:
                if va <= rva < va + size:
                    return rva - va + rptr
            raise PEError(f"rva {rva:#x} outside sections")

        exports: list[str] = []
        if exp_rva:
            e = off(exp_rva)
            n_names, names_rva = _u32(data, e + 24), _u32(data, e + 32)
            names = off(names_rva)
            for i in range(min(n_names, 10000)):
                p = off(_u32(data, names + 4 * i))
                exports.append(data[p:data.index(b"\0", p)].decode("ascii", "replace"))
    except (struct.error, IndexError) as e:
        raise PEError(f"truncated or malformed PE: {e}") from e
    built = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "?"
    return PEInfo(MACHINES.get(machine, hex(machine)), ts, built, bool(chars & 0x2000), exports)


@dataclass
class ModuleInfo:
    path: str
    size: int
    sha256: str
    pe: PEInfo | None
    error: str | None = None
    gzcom: bool = False
    plugin_scan_hints: list[str] = field(default_factory=list)


def inspect_module(path: Path, root: Path, scan_hints: bool = False) -> ModuleInfo:
    data = path.read_bytes()
    m = ModuleInfo(str(path.relative_to(root)), len(data), hashlib.sha256(data).hexdigest(), None)
    try:
        m.pe = parse_pe(data)
        m.gzcom = GZCOM_EXPORT in m.pe.exports
    except PEError as e:
        m.error = str(e)
    if scan_hints:
        m.plugin_scan_hints = [h.decode() for h in PLUGIN_SCAN_HINTS if h in data]
    return m


@dataclass
class InstallReport:
    root: str
    exe: str | None
    edition_guess: str
    exe_dir: str | None
    modules: list[ModuleInfo]
    findings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _walk_for_exes(root: Path, max_depth: int) -> list[Path]:
    """Depth-limited search that skips folders it may not read (rglob would walk everything)."""
    hits: list[Path] = []
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_file() and e.name.lower() in EDITION_BY_EXE:
                    hits.append(Path(e.path))
                elif e.is_dir(follow_symlinks=False) and depth < max_depth:
                    stack.append((Path(e.path), depth + 1))
            except OSError:
                continue
    return hits


def find_game_exe(root: Path, max_depth: int = 3) -> Path | None:
    hits = _walk_for_exes(root, max_depth)
    hits.sort(key=lambda p: (p.name.lower() != "sc3u.exe", len(p.parts), str(p)))
    return hits[0] if hits else None


def default_search_roots() -> list[Path]:
    """Usual install locations on Windows (plus whatever the environment says)."""
    roots: list[str] = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        if os.environ.get(var):
            roots.append(os.environ[var])
    for drive in ("C:", "D:", "E:"):
        roots += [drive + "\\Games", drive + "\\Maxis", drive + "\\SimCity 3000", drive + "\\SimCity 3000 Unlimited"]
    seen, out = set(), []
    for r in roots:
        p = Path(r)
        if p.is_dir() and str(p).lower() not in seen:
            seen.add(str(p).lower())
            out.append(p)
    return out


def search_installs(roots: list[Path] | None = None, max_depth: int = 4) -> list[Path]:
    """Find game folders (the folder above Apps/, or the exe's folder) under the given roots."""
    found: list[Path] = []
    for root in roots if roots is not None else default_search_roots():
        for exe in _walk_for_exes(root, max_depth):
            folder = exe.parent.parent if exe.parent.name.lower() == "apps" else exe.parent
            if folder not in found:
                found.append(folder)
    return found


def inspect_install(root: str | Path) -> InstallReport:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"not a folder: {root}")
    exe = find_game_exe(root)
    findings: list[str] = []
    if exe is None:
        return InstallReport(str(root), None, "unknown", None, [],
                             ["no SC3.exe / SC3U.exe within 3 folder levels; point at the game's install folder"])
    exe_dir = exe.parent
    edition = EDITION_BY_EXE[exe.name.lower()]
    findings.append(f"edition by exe name: {edition} ({exe.relative_to(root)})")

    modules = [inspect_module(exe, root, scan_hints=True)]
    for p in sorted(exe_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".dll":
            modules.append(inspect_module(p, root))
    m_exe = modules[0]
    dlls = modules[1:]
    gz = [m for m in dlls if m.gzcom]

    if m_exe.pe:
        findings.append(f"exe: {m_exe.pe.machine}, PE timestamp {m_exe.pe.built}, {m_exe.size:,} bytes")
    if exe_dir.name.lower() == "apps":
        findings.append("exe lives in an 'Apps' folder (same layout as SC3U)")
    else:
        findings.append(f"exe folder is '{exe_dir.name}', not 'Apps'")
    if m_exe.gzcom or gz:
        findings.append(f"GZCOM framework present: exe exports {GZCOM_EXPORT}={m_exe.gzcom}, "
                        f"{len(gz)}/{len(dlls)} DLLs next to it export it")
    else:
        findings.append("no module exports GZDllGetGZCOMDirector: GZCOM plugin route unlikely for this build")
    if m_exe.plugin_scan_hints:
        findings.append(f"exe contains plugin-scan strings {m_exe.plugin_scan_hints} (suggestive, not proof)")
    else:
        findings.append("exe does not contain the plugin-scan strings seen in SC3U.exe")
    findings.append("proof requires loading a test plugin (stage 1.5); these are file-level indications only")
    return InstallReport(str(root), str(exe.relative_to(root)), edition, str(exe_dir.relative_to(root)), modules,
                         findings)

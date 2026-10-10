"""Install inspection against real 32-bit PE files built on the fly (clang + lld-link).

The fixtures are tiny stand-ins with the same *shape* as a game install
(exe + DLLs exporting GZDllGetGZCOMDirector); they are not game files.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mayor.install_info import PEError, inspect_install, parse_pe, search_installs

HAVE_TOOLS = bool(shutil.which("clang") and shutil.which("lld-link"))

EXE_C = 'const char *h[] = {"*.dll", "PlugIn"};\n' \
        "__declspec(dllexport) void *GZDllGetGZCOMDirector(void) { return (void*)h; }\n" \
        "int mainCRTStartup(void) { return 0; }\n"
GZ_C = "__declspec(dllexport) void *GZDllGetGZCOMDirector(void) { return 0; }\n" \
       "__declspec(dllexport) int Other(void) { return 1; }\n"
PLAIN_C = "__declspec(dllexport) int Foo(void) { return 1; }\n"


def build(out_dir: Path, name: str, src: str, dll: bool) -> Path:
    c = out_dir / (name + ".c")
    c.write_text(src)
    obj = out_dir / (name + ".o")
    subprocess.run(["clang", "--target=i686-pc-windows-msvc", "-O1", "-c", str(c), "-o", str(obj)], check=True)
    out = out_dir / name
    link = ["lld-link", "/nologo", "/machine:x86", f"/out:{out}", str(obj)]
    link += ["/dll", "/noentry"] if dll else ["/entry:mainCRTStartup", "/subsystem:console"]
    subprocess.run(link, check=True)
    c.unlink()
    obj.unlink()
    return out


@unittest.skipUnless(HAVE_TOOLS, "needs clang + lld-link to build PE fixtures")
class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.unlimited = root / "SimCity 3000 Unlimited"
        apps = cls.unlimited / "Apps"
        apps.mkdir(parents=True)
        build(apps, "SC3U.exe", EXE_C, dll=False)
        build(apps, "SIMCITY.DLL", GZ_C, dll=True)
        build(apps, "PLAIN.DLL", PLAIN_C, dll=True)
        (apps / "README.TXT").write_text("x")
        # an "original" layout without GZCOM exports or plugin strings
        cls.original = root / "SimCity 3000"
        orig_dir = cls.original / "SC3"
        orig_dir.mkdir(parents=True)
        build(orig_dir, "SC3.exe", "int mainCRTStartup(void) { return 0; }\n", dll=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_parse_exports(self):
        info = parse_pe((self.unlimited / "Apps" / "SIMCITY.DLL").read_bytes())
        self.assertEqual(info.machine, "x86 (32-bit)")
        self.assertTrue(info.is_dll)
        self.assertEqual(sorted(info.exports), ["GZDllGetGZCOMDirector", "Other"])

    def test_unlimited_layout(self):
        rep = inspect_install(self.unlimited)
        self.assertEqual(rep.edition_guess, "SimCity 3000 Unlimited")
        self.assertEqual(rep.exe_dir, "Apps")
        by_name = {Path(m.path).name: m for m in rep.modules}
        self.assertTrue(by_name["SC3U.exe"].gzcom)
        self.assertEqual(by_name["SC3U.exe"].plugin_scan_hints, ["*.dll", "PlugIn"])
        self.assertTrue(by_name["SIMCITY.DLL"].gzcom)
        self.assertFalse(by_name["PLAIN.DLL"].gzcom)
        self.assertNotIn("README.TXT", by_name)
        text = "\n".join(rep.findings)
        self.assertIn("1/2 DLLs", text)
        self.assertIn("not proof", text)

    def test_original_layout_without_gzcom(self):
        rep = inspect_install(self.original)
        self.assertEqual(rep.edition_guess, "SimCity 3000 (original)")
        text = "\n".join(rep.findings)
        self.assertIn("unlikely", text)
        self.assertIn("not 'Apps'", text)


class NoToolTests(unittest.TestCase):
    def test_not_pe(self):
        with self.assertRaises(PEError):
            parse_pe(b"hello")
        with self.assertRaises(PEError):
            parse_pe(b"MZ" + b"\0" * 10)

    def test_no_exe(self):
        with tempfile.TemporaryDirectory() as d:
            rep = inspect_install(d)
            self.assertIsNone(rep.exe)
            self.assertIn("no SC3.exe", rep.findings[0])

    def test_search_installs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Maxis" / "SimCity 3000 Unlimited" / "Apps").mkdir(parents=True)
            (root / "Maxis" / "SimCity 3000 Unlimited" / "Apps" / "SC3U.exe").write_bytes(b"")
            (root / "Old" / "SC3").mkdir(parents=True)
            (root / "Old" / "SC3" / "sc3.EXE").write_bytes(b"")
            (root / "a" / "b" / "c" / "d" / "e").mkdir(parents=True)
            (root / "a" / "b" / "c" / "d" / "e" / "SC3.exe").write_bytes(b"")  # deeper than max_depth
            found = sorted(str(p.relative_to(root)) for p in search_installs([root], max_depth=4))
            self.assertEqual(found, [str(Path("Maxis/SimCity 3000 Unlimited")), str(Path("Old/SC3"))])
            self.assertEqual(search_installs([root / "nope"]), [])

    def test_missing_folder(self):
        with self.assertRaises(FileNotFoundError):
            inspect_install("/nonexistent/sc3")


if __name__ == "__main__":
    unittest.main()

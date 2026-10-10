import json
import unittest
from unittest import mock

from PIL import Image

from mayor.extractors.vision import VisionExtractor, build_prompt, parse_response, png_bytes
from mayor.llm import LLMError, make_llm
from mayor.llm.mock import MockLLM
from mayor.state import OBSERVED, REJECTED, UNKNOWN

GOOD = {"fields": {
    "view": {"value": "city map", "visible": True, "evidence": "map fills screen"},
    "funds": {"value": "§14,250", "visible": True, "evidence": "bottom bar"},
    "population": {"value": None, "visible": False, "evidence": "not shown"},
    "demand_residential": {"value": "banana", "visible": True, "evidence": ""},
    "visible_problems": {"value": ["fire near harbor"], "visible": True, "evidence": "smoke"},
    "made_up_field": {"value": 1},
}, "notes": "budget panel partially covers HUD"}


class ParseTests(unittest.TestCase):
    def test_parse_good(self):
        s = parse_response("```json\n" + json.dumps(GOOD) + "\n```", "llm:t")
        self.assertEqual(s.get("funds").value, 14250)
        self.assertEqual(s.get("funds").status, OBSERVED)
        self.assertEqual(s.get("population").status, UNKNOWN)
        self.assertEqual(s.get("demand_residential").status, REJECTED)
        self.assertEqual(s.get("visible_problems").value, ["fire near harbor"])
        self.assertEqual(s.get("game_date").status, UNKNOWN)  # absent -> unknown, not invented
        self.assertTrue(any("budget panel" in w for w in s.warnings))

    def test_parse_garbage_never_raises(self):
        s = parse_response("I think the city is doing great!", "llm:t")
        self.assertTrue(all(f.status == UNKNOWN for f in s.fields.values()))
        self.assertTrue(s.warnings)

    def test_prompt_lists_every_field(self):
        p = build_prompt()
        for name in ("funds", "population", "demand_industrial", "visible_problems"):
            self.assertIn(name, p)

    def test_downscale(self):
        from io import BytesIO
        data = png_bytes(Image.new("RGB", (2048, 1536)), 1024)
        self.assertEqual(Image.open(BytesIO(data)).size, (1024, 768))

    def test_extractor_with_mock(self):
        llm = MockLLM(response=GOOD)
        s = VisionExtractor(llm).extract(Image.new("RGB", (800, 600), (50, 90, 50)))
        self.assertEqual(s.get("funds").value, 14250)
        self.assertEqual(len(llm.calls), 1)


class ProviderRequestTests(unittest.TestCase):
    """Check request shapes and response parsing without network."""

    def _run(self, provider, response):
        captured = {}

        def fake_post(url, body, headers, timeout=60.0):
            captured.update(url=url, body=body, headers=headers)
            return response, 0.1

        llm = make_llm(provider, api_key="k")
        mod = __import__(f"mayor.llm.{llm.name}", fromlist=["post_json"])
        with mock.patch.object(mod, "post_json", fake_post):
            res = llm.ask("hi", b"\x89PNG")
        return res, captured

    def test_anthropic(self):
        res, c = self._run("anthropic", {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 5, "output_tokens": 2}})
        self.assertEqual((res.text, res.input_tokens), ("{}", 5))
        self.assertEqual(c["headers"]["x-api-key"], "k")
        self.assertEqual(c["body"]["messages"][0]["content"][0]["type"], "image")

    def test_gemini(self):
        res, c = self._run("gemini", {"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "usageMetadata": {"promptTokenCount": 9}})
        self.assertEqual((res.text, res.input_tokens), ("{}", 9))
        self.assertIn(":generateContent", c["url"])
        self.assertIn("inline_data", c["body"]["contents"][0]["parts"][0])

    def test_openai(self):
        res, c = self._run("openai", {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 3}})
        self.assertEqual((res.text, res.input_tokens), ("{}", 3))
        self.assertTrue(c["body"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_missing_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(LLMError):
                make_llm("openai").ask("x")

    def test_unknown_provider(self):
        with self.assertRaises(ValueError):
            make_llm("nope")


class ClaudeCodeProviderTests(unittest.TestCase):
    """`claude -p` provider against a fake CLI (no network, no sign-in)."""

    def _fake_cli(self, d, payload, code=0):
        import os
        import stat
        path = os.path.join(d, "claude")
        with open(path, "w") as f:
            f.write("#!/bin/sh\ncat > " + os.path.join(d, "stdin.txt") + "\necho \"$@\" > " + os.path.join(d, "args.txt")
                    + "\ncat <<'EOF'\n" + payload + "\nEOF\nexit " + str(code) + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def test_success_and_request_shape(self):
        import tempfile
        from mayor.llm.claude_code import ClaudeCodeLLM
        with tempfile.TemporaryDirectory() as d:
            cli = self._fake_cli(d, json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                                "result": "{\"tax_rate\": 8}", "usage": {"input_tokens": 3,
                                                "cache_read_input_tokens": 7, "output_tokens": 5},
                                                "modelUsage": {"claude-x": {}}}))
            res = ClaudeCodeLLM(binary=cli).ask("hello mayor")
            self.assertEqual((res.text, res.model, res.input_tokens, res.output_tokens), ('{"tax_rate": 8}', "claude-x", 10, 5))
            args = open(f"{d}/args.txt").read()
            self.assertIn("-p --output-format json --tools", args)   # headless, tools disabled
            self.assertEqual(open(f"{d}/stdin.txt").read(), "hello mayor")

    def test_errors(self):
        import tempfile
        from mayor.llm.claude_code import ClaudeCodeLLM
        with tempfile.TemporaryDirectory() as d:
            cli = self._fake_cli(d, json.dumps({"subtype": "error_max_turns", "is_error": True, "result": "nope"}))
            with self.assertRaises(LLMError):
                ClaudeCodeLLM(binary=cli).ask("x")
            cli = self._fake_cli(d, "not json", code=1)
            with self.assertRaises(LLMError):
                ClaudeCodeLLM(binary=cli).ask("x")
        with self.assertRaises(LLMError):
            ClaudeCodeLLM(binary="/nonexistent/claude").ask("x")
        with self.assertRaises(LLMError):
            ClaudeCodeLLM(binary="/bin/true").ask("x", png=b"img")


if __name__ == "__main__":
    unittest.main()

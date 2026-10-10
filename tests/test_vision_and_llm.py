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


if __name__ == "__main__":
    unittest.main()

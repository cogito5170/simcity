"""Offline stand-in: returns a canned response. For tests and dry runs (no cost)."""

from __future__ import annotations

import json
from pathlib import Path

from .base import LLMResult, VisionLLM

_DEFAULT = {"view": {"value": "city map", "visible": True, "evidence": "mock"}}


class MockLLM(VisionLLM):
    name = "mock"
    model = "mock"

    def __init__(self, response_file: str | None = None, response: dict | str | None = None):
        if response is not None:
            self.text = response if isinstance(response, str) else json.dumps(response)
        elif response_file:
            self.text = Path(response_file).read_text(encoding="utf-8")
        else:
            self.text = json.dumps({"fields": _DEFAULT})
        self.calls: list[str] = []

    def ask(self, prompt: str, png: bytes | None = None, max_tokens: int = 1024) -> LLMResult:
        self.calls.append(prompt)
        return LLMResult(self.text, "mock", 0, 0, 0.0)

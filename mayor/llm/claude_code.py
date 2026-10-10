"""Use the local Claude Code CLI (`claude -p`) as the model: no API key, uses the CLI's own sign-in.

Runs headless with every tool disabled, from a scratch directory, so the CLI
only answers the prompt (it cannot read or change files).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time

from .base import LLMError, LLMResult, VisionLLM


class ClaudeCodeLLM(VisionLLM):
    name = "claude-code"

    def __init__(self, model: str | None = None, binary: str = "claude", timeout_s: float = 300.0):
        self.model = model or "default"
        self.binary = binary
        self.timeout_s = timeout_s

    def ask(self, prompt: str, png: bytes | None = None, max_tokens: int = 1024) -> LLMResult:
        if png is not None:
            raise LLMError("claude-code provider is text-only here; use anthropic/gemini/openai for images")
        exe = shutil.which(self.binary)
        if not exe:
            raise LLMError("Claude Code CLI not found: install it and sign in (`claude`), or pick another provider")
        cmd = [exe, "-p", "--output-format", "json", "--tools", ""]
        if self.model != "default":
            cmd += ["--model", self.model]
        t0 = time.time()
        with tempfile.TemporaryDirectory() as scratch:
            try:
                r = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, cwd=scratch,
                                   timeout=self.timeout_s)
            except subprocess.TimeoutExpired as e:
                raise LLMError(f"claude -p timed out after {self.timeout_s:.0f}s") from e
        try:
            data = json.loads(r.stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError as e:
            err = (r.stderr or r.stdout).decode("utf-8", "replace")[:300]
            raise LLMError(f"claude -p failed (exit {r.returncode}): {err}") from e
        if data.get("is_error") or data.get("subtype") != "success":
            raise LLMError(f"claude -p error: {str(data.get('result') or data)[:300]}")
        u = data.get("usage") or {}
        models = list((data.get("modelUsage") or {}).keys())
        res = LLMResult(data.get("result") or "", models[0] if models else self.model,
                        (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                        + (u.get("cache_creation_input_tokens") or 0), u.get("output_tokens"), time.time() - t0, data)
        return res

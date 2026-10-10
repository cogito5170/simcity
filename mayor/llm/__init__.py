"""Provider-agnostic vision LLM access.

The rest of the system only sees `VisionLLM.ask(prompt, png_bytes) -> LLMResult`.
Swapping Claude / Gemini / OpenAI is a config change, not a code change.
Providers talk plain HTTPS via urllib, so no vendor SDK is required.
"""

from __future__ import annotations

import os

from .base import LLMError, LLMResult, VisionLLM

# Defaults favour the cheapest vision-capable tier of each vendor. Model ids
# change often: override with --model or config "llm.model" if one is retired.
DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "gemini": "gemini-2.5-flash-lite",
    "openai": "gpt-4.1-mini",
    "mock": "mock",
    "claude-code": None,   # the CLI's own default model; pass --model to choose
}


def make_llm(provider: str, model: str | None = None, **kw) -> VisionLLM:
    provider = provider.lower()
    model = model or DEFAULT_MODELS.get(provider)
    if provider in ("anthropic", "claude"):
        from .anthropic import AnthropicLLM
        return AnthropicLLM(model, api_key=kw.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"))
    if provider in ("gemini", "google"):
        from .gemini import GeminiLLM
        return GeminiLLM(model, api_key=kw.get("api_key") or os.environ.get("GEMINI_API_KEY")
                         or os.environ.get("GOOGLE_API_KEY"))
    if provider == "openai":
        from .openai import OpenAILLM
        return OpenAILLM(model, api_key=kw.get("api_key") or os.environ.get("OPENAI_API_KEY"))
    if provider in ("claude-code", "claude_code", "claudecode"):
        from .claude_code import ClaudeCodeLLM
        return ClaudeCodeLLM(model)
    if provider == "mock":
        from .mock import MockLLM
        return MockLLM(kw.get("response_file"))
    raise ValueError(f"unknown LLM provider {provider!r} (anthropic | gemini | openai | claude-code | mock)")


__all__ = ["DEFAULT_MODELS", "LLMError", "LLMResult", "VisionLLM", "make_llm"]

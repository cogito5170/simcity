from __future__ import annotations

from .base import LLMError, LLMResult, VisionLLM, b64, post_json, require_key


class AnthropicLLM(VisionLLM):
    name = "anthropic"
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str | None):
        self.model = model
        self.api_key = api_key

    def ask(self, prompt: str, png: bytes | None = None, max_tokens: int = 1024) -> LLMResult:
        key = require_key(self.api_key, "ANTHROPIC_API_KEY")
        content: list[dict] = []
        if png is not None:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(png)}})
        content.append({"type": "text", "text": prompt})
        body = {"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
        data, dt = post_json(self.url, body, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        try:
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected Anthropic response: {str(data)[:300]}") from e
        u = data.get("usage", {})
        return LLMResult(text, data.get("model", self.model), u.get("input_tokens"), u.get("output_tokens"), dt, data)

from __future__ import annotations

from .base import LLMError, LLMResult, VisionLLM, b64, post_json, require_key


class GeminiLLM(VisionLLM):
    name = "gemini"

    def __init__(self, model: str, api_key: str | None):
        self.model = model
        self.api_key = api_key

    def ask(self, prompt: str, png: bytes | None = None, max_tokens: int = 1024) -> LLMResult:
        key = require_key(self.api_key, "GEMINI_API_KEY")
        parts: list[dict] = []
        if png is not None:
            parts.append({"inline_data": {"mime_type": "image/png", "data": b64(png)}})
        parts.append({"text": prompt})
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        data, dt = post_json(url, body, {"x-goog-api-key": key})
        try:
            parts_out = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts_out)
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected Gemini response: {str(data)[:300]}") from e
        u = data.get("usageMetadata", {})
        return LLMResult(text, self.model, u.get("promptTokenCount"), u.get("candidatesTokenCount"), dt, data)

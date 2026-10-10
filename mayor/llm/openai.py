from __future__ import annotations

from .base import LLMError, LLMResult, VisionLLM, b64, post_json, require_key


class OpenAILLM(VisionLLM):
    name = "openai"
    url = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model: str, api_key: str | None):
        self.model = model
        self.api_key = api_key

    def ask(self, prompt: str, png: bytes | None = None, max_tokens: int = 1024) -> LLMResult:
        key = require_key(self.api_key, "OPENAI_API_KEY")
        content: list[dict] = [{"type": "text", "text": prompt}]
        if png is not None:
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64(png)}})
        body = {"model": self.model, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}]}
        data, dt = post_json(self.url, body, {"authorization": f"Bearer {key}"})
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected OpenAI response: {str(data)[:300]}") from e
        u = data.get("usage", {})
        return LLMResult(text, data.get("model", self.model), u.get("prompt_tokens"), u.get("completion_tokens"), dt, data)

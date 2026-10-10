from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float = 0.0
    raw: dict = field(default_factory=dict, repr=False)

    def usage(self) -> dict:
        return {"model": self.model, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "latency_s": round(self.latency_s, 2)}


class VisionLLM:
    name = "base"
    model = ""

    def ask(self, prompt: str, png: bytes | None = None, max_tokens: int = 1024) -> LLMResult:
        raise NotImplementedError


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def post_json(url: str, body: dict, headers: dict, timeout: float = 60.0) -> tuple[dict, float]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), time.time() - t0
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise LLMError(f"HTTP {e.code} from {url.split('?')[0]}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMError(f"request to {url.split('?')[0]} failed: {e}") from e


def require_key(key: str | None, env_name: str) -> str:
    if not key:
        raise LLMError(f"no API key: set {env_name}")
    return key

"""Vision-model reader: asks a (cheap) multimodal LLM to read the screenshot.

The prompt asks only for what is *visible*, with evidence, and null otherwise.
Code — not the model — then validates types and assigns statuses.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone

from PIL import Image

from ..llm import LLMError, LLMResult, VisionLLM
from ..state import (DEMAND_LEVELS, FIELD_SPECS, OBSERVED, REJECTED, SPEC_BY_NAME, UNKNOWN, CityState, Field,
                     normalize_value)

PROMPT = """You are the eyes of an AI city mayor. The image is a screenshot that is supposed to show the PC game SimCity 3000.
Report ONLY what you can actually read or see in this image. Do not use typical game values, do not guess hidden numbers,
do not infer from what the game usually shows. If something is not visible, cut off, or unreadable, give null.

Return one JSON object, no prose, with this shape:
{{"fields": {{ "<field>": {{"value": <value or null>, "visible": <true|false>, "evidence": "<where on screen / exact text seen>"}} , ... }},
  "notes": "<anything uncertain about the image itself, e.g. blurry, menu open, not the game>"}}

Fields:
{fields}

Rules:
- Numbers: copy the digits as displayed (keep minus signs); do not round or convert units.
- demand_* values must be one of {levels} or null; give null if no demand indicator is visible.
- visible_problems: list of short strings, [] if none are visible, null if the map is not visible.
"""


def build_prompt() -> str:
    lines = [f"- {s.name} ({s.kind}): {s.description}" for s in FIELD_SPECS]
    return PROMPT.format(fields="\n".join(lines), levels=DEMAND_LEVELS)


def png_bytes(img: Image.Image, max_width: int = 1024) -> bytes:
    """Downscale before sending: fewer image tokens = lower cost. 1024px keeps HUD text legible at 800x600-1280x1024."""
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def extract_json(text: str) -> dict:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            return json.loads(t[start:end + 1])
        raise


def parse_response(text: str, source: str, confidence: float = 0.6) -> CityState:
    """Turn model output into a CityState. Malformed output -> all unknown + warning, never an exception."""
    st = CityState()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        data = extract_json(text)
    except (json.JSONDecodeError, ValueError):
        st.warnings.append(f"{source}: response was not JSON: {text[:120]!r}")
        return st
    fields = data.get("fields", data) if isinstance(data, dict) else {}
    if not isinstance(fields, dict):
        st.warnings.append(f"{source}: 'fields' is not an object")
        return st
    for name, item in fields.items():
        if name not in SPEC_BY_NAME:
            continue
        if not isinstance(item, dict):
            item = {"value": item, "visible": item is not None, "evidence": ""}
        raw, visible, evidence = item.get("value"), item.get("visible", True), str(item.get("evidence", ""))[:200]
        if raw is None or visible is False:
            st.set(name, Field(status=UNKNOWN, source=source, evidence=evidence or "not visible"))
            continue
        value, err = normalize_value(name, raw)
        if err:
            st.set(name, Field(status=REJECTED, source=source, evidence=f"{err}; {evidence}"))
        else:
            st.set(name, Field(value, OBSERVED, confidence, source, evidence, now))
    notes = data.get("notes") if isinstance(data, dict) else None
    if notes:
        st.warnings.append(f"{source} notes: {str(notes)[:200]}")
    return st


class VisionExtractor:
    name = "llm"

    def __init__(self, llm: VisionLLM, max_width: int = 1024):
        self.llm = llm
        self.max_width = max_width
        self.prompt = build_prompt()
        self.last_result: LLMResult | None = None

    def extract(self, img: Image.Image) -> CityState:
        source = f"llm:{self.llm.name}/{self.llm.model}"
        try:
            self.last_result = self.llm.ask(self.prompt, png_bytes(img, self.max_width))
            from ..llm.ledger import record
            record("screen_read", self.last_result.usage(), self.llm.name)
        except LLMError as e:
            self.last_result = None
            st = CityState()
            st.warnings.append(f"{source} failed: {e}")
            return st
        return parse_response(self.last_result.text, source)

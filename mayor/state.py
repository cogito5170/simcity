"""City state model that keeps "what we saw" apart from "what we don't know".

Every value carries a status:
  observed  - read from the current frame (OCR or vision model), with evidence
  stale     - read from an earlier frame and carried forward (see observed_at)
  conflict  - two readers disagreed; value withheld, both readings kept in evidence
  rejected  - a reading failed a sanity check; value withheld
  unknown   - nothing reliable was read
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

OBSERVED, STALE, CONFLICT, REJECTED, UNKNOWN = "observed", "stale", "conflict", "rejected", "unknown"

DEMAND_LEVELS = ["strong_negative", "negative", "neutral", "positive", "strong_positive"]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str  # "int" | "text" | "level" | "list"
    description: str


# The fields we try to read. Descriptions go into the vision prompt, so they
# describe *what to look for* without asserting where the game draws it.
FIELD_SPECS: list[FieldSpec] = [
    FieldSpec("view", "text", "What is on screen: e.g. 'city map', 'budget window', 'main menu', 'dialog', 'loading', 'not the game'."),
    FieldSpec("game_date", "text", "In-game date exactly as displayed (e.g. 'Mar 2051')."),
    FieldSpec("funds", "int", "City treasury amount as displayed (may be negative)."),
    FieldSpec("population", "int", "City population as displayed."),
    FieldSpec("demand_residential", "level", "Residential demand from the RCI demand indicator, if visible."),
    FieldSpec("demand_commercial", "level", "Commercial demand from the RCI demand indicator, if visible."),
    FieldSpec("demand_industrial", "level", "Industrial demand from the RCI demand indicator, if visible."),
    FieldSpec("game_speed", "text", "Game speed or paused state, only if explicitly indicated on screen."),
    FieldSpec("ticker_message", "text", "Text of any news ticker / advisor / alert message visible."),
    FieldSpec("visible_problems", "list", "Problems directly visible on the map (fires, heavy traffic, pollution, abandoned buildings...)."),
]
SPEC_BY_NAME = {s.name: s for s in FIELD_SPECS}


@dataclass
class Field:
    value: Any = None
    status: str = UNKNOWN
    confidence: float = 0.0
    source: str = ""
    evidence: str = ""
    observed_at: str | None = None

    @property
    def known(self) -> bool:
        return self.status in (OBSERVED, STALE) and self.value is not None


@dataclass
class CityState:
    fields: dict[str, Field] = field(default_factory=lambda: {s.name: Field() for s in FIELD_SPECS})
    warnings: list[str] = field(default_factory=list)

    def get(self, name: str) -> Field:
        return self.fields[name]

    def set(self, name: str, f: Field) -> None:
        if name not in SPEC_BY_NAME:
            raise KeyError(f"unknown field {name!r}")
        self.fields[name] = f

    def to_dict(self) -> dict:
        return {"fields": {k: asdict(v) for k, v in self.fields.items()}, "warnings": list(self.warnings)}

    @classmethod
    def from_dict(cls, d: dict) -> "CityState":
        st = cls()
        for k, v in d.get("fields", {}).items():
            if k in SPEC_BY_NAME:
                st.fields[k] = Field(**v)
        st.warnings = list(d.get("warnings", []))
        return st

    def summary(self) -> str:
        """One line per field: value or '?' with status. Used for logs and later as LLM context."""
        lines = []
        for name, f in self.fields.items():
            shown = f.value if f.known else "?"
            lines.append(f"{name:<20} {str(shown):<28} [{f.status}{', ' + f.source if f.source else ''}]")
        return "\n".join(lines)


# ---------------------------------------------------------------- parsing ---

_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1"})


def parse_int(text: Any) -> int | None:
    """Parse a displayed number like '§12,345', '-§1,200', '12.345', '  4 321 '.

    Returns None rather than guessing when the text does not look like one number.
    """
    if isinstance(text, bool):
        return None
    if isinstance(text, int):
        return text
    if isinstance(text, float):
        return int(text) if text.is_integer() else None
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s:
        return None
    negative = bool(re.match(r"^\s*[-−(]", s))
    # Drop currency symbols / labels before applying OCR digit fixes, so that
    # 'Pop' or '§' do not turn into digits.
    s = re.sub(r"[§$€£₩−()\-+]", " ", s)
    # Drop label tokens like 'Pop:' or 'Funds' (anything with a non digit-like char).
    tokens = [t for t in re.split(r"[\s:=]+", s) if t]
    digitish = re.compile(r"[\dOolI|,.]+")
    while tokens and not digitish.fullmatch(tokens[0]):
        tokens.pop(0)
    while tokens and not digitish.fullmatch(tokens[-1]):
        tokens.pop()
    s = " ".join(tokens).strip(",.")
    if not re.search(r"\d", s):  # letters alone ('O', 'l') are not a number
        return None
    s = s.translate(_DIGIT_FIXES)
    if not re.fullmatch(r"\d{1,3}([,. ]\d{3})+|\d+", s):
        return None
    n = int(re.sub(r"[,. ]", "", s))
    return -n if negative else n


def normalize_level(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    t = text.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"high": "strong_positive", "strong": "strong_positive", "up": "positive", "low": "negative",
               "none": "neutral", "zero": "neutral", "down": "negative", "very_negative": "strong_negative",
               "very_positive": "strong_positive"}
    t = aliases.get(t, t)
    return t if t in DEMAND_LEVELS else None


def normalize_value(name: str, raw: Any) -> tuple[Any, str | None]:
    """Coerce a raw reading to the field's kind. Returns (value, error)."""
    kind = SPEC_BY_NAME[name].kind
    if raw is None:
        return None, None
    if kind == "int":
        v = parse_int(raw)
        return (v, None) if v is not None else (None, f"not a number: {raw!r}")
    if kind == "level":
        v = normalize_level(raw)
        return (v, None) if v is not None else (None, f"not a demand level: {raw!r}")
    if kind == "list":
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return None, f"not a list: {raw!r}"
        return [str(x).strip() for x in raw if str(x).strip()], None
    s = str(raw).strip()
    return (s or None), None

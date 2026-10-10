"""Combine readings and keep the record honest. Plain code, no AI."""

from __future__ import annotations

import re
from dataclasses import replace

from ..state import CONFLICT, OBSERVED, REJECTED, STALE, UNKNOWN, CityState, Field

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun",
                                       "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_game_date(text) -> tuple[int, int] | None:
    """'Mar 2051' / 'March 2051' / '1 Jan 2050' -> (2051, 3). None if not recognizable."""
    if not isinstance(text, str):
        return None
    y = re.search(r"\b(1[89]\d\d|2\d\d\d)\b", text)
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\b", text, re.I)
    if not y or not m:
        return None
    return int(y.group(1)), _MONTHS[m.group(1).lower()]


def _same(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def merge_states(*states: CityState) -> CityState:
    """Field-wise merge. Agreement raises confidence; disagreement becomes a visible conflict."""
    out = CityState()
    for st in states:
        out.warnings.extend(st.warnings)
    for name in out.fields:
        seen = [st.fields[name] for st in states]
        obs = [f for f in seen if f.status == OBSERVED and f.value is not None]
        if not obs:
            # keep the most informative non-observation (rejected says more than unknown)
            pick = next((f for f in seen if f.status == REJECTED), None) or next(
                (f for f in seen if f.evidence), Field())
            out.fields[name] = pick
            continue
        first = obs[0]
        if all(_same(f.value, first.value) for f in obs[1:]):
            conf = max(f.confidence for f in obs) + (0.2 if len(obs) > 1 else 0.0)
            out.fields[name] = replace(first, confidence=round(min(conf, 1.0), 2),
                                       source="+".join(f.source for f in obs))
        else:
            out.fields[name] = Field(status=CONFLICT, source="+".join(f.source for f in obs),
                                     evidence="; ".join(f"{f.source}={f.value!r}" for f in obs))
            out.warnings.append(f"{name}: readers disagree ({out.fields[name].evidence})")
    return out


def carry_forward(current: CityState, previous: CityState | None) -> CityState:
    """Fill fields we could not read now with the last known value, marked STALE (never OBSERVED)."""
    if previous is None:
        return current
    for name, f in current.fields.items():
        p = previous.fields[name]
        if f.status == UNKNOWN and p.known:
            current.fields[name] = replace(p, status=STALE)
    return current


def sanity_check(state: CityState, previous: CityState | None) -> CityState:
    """Reject impossible values; warn (but keep) surprising ones."""
    pop = state.fields["population"]
    if pop.status == OBSERVED and isinstance(pop.value, int) and not (0 <= pop.value <= 50_000_000):
        state.fields["population"] = Field(status=REJECTED, source=pop.source, evidence=f"implausible population {pop.value}")
        state.warnings.append(f"population rejected: {pop.value}")

    date = state.fields["game_date"]
    if date.status == OBSERVED and parse_game_date(date.value) is None:
        state.warnings.append(f"game_date {date.value!r} not in a recognized 'Mon YYYY' form")

    if previous is None:
        return state
    pdate, cdate = previous.fields["game_date"], state.fields["game_date"]
    if pdate.known and cdate.status == OBSERVED:
        a, b = parse_game_date(pdate.value), parse_game_date(cdate.value)
        if a and b and b < a:
            state.warnings.append(f"game date went backwards {pdate.value} -> {cdate.value} (another city loaded?)")
    ppop, cpop = previous.fields["population"], state.fields["population"]
    if ppop.known and cpop.status == OBSERVED and isinstance(ppop.value, int) and ppop.value > 1000:
        if abs(cpop.value - ppop.value) / ppop.value > 0.5:
            state.warnings.append(f"population jumped {ppop.value} -> {cpop.value}; check the reading")
    return state

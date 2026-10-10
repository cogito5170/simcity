"""Periodic strategy review: rules (free) or an LLM, plus evaluation of past predictions.

Each review records what was decided, why, what it expects to happen, and what
it is unsure about. The next review scores those expectations against what the
engine actually reported, and the LLM sees that track record (learning loop).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .strategy import LIMITS, Strategy

TRACKED = ("population", "funds", "crime_avg", "pollution_avg", "traffic_avg", "land_value_avg", "city_score")
FLAT_BAND = 0.03  # |relative change| below 3% counts as "flat"


@dataclass
class Review:
    date: str
    source: str                      # "rules" | "llm:<provider>/<model>" | "rules (llm failed)"
    strategy: dict
    reasoning: list[str]
    expected: dict                   # metric -> up | down | flat
    uncertainties: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    stats_at_review: dict = field(default_factory=dict)
    evaluation_of_previous: dict | None = None
    notes: list[str] = field(default_factory=list)
    usage: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def direction(before: float, after: float) -> str:
    rel = (after - before) / max(abs(before), 1.0)
    return "up" if rel > FLAT_BAND else "down" if rel < -FLAT_BAND else "flat"


def evaluate(prev: Review, now_stats: dict) -> dict:
    """Score the previous review's expectations against measured outcomes."""
    rows, hits = [], 0
    for metric, exp in prev.expected.items():
        if metric not in TRACKED or metric not in prev.stats_at_review:
            continue
        actual = direction(prev.stats_at_review[metric], now_stats[metric])
        ok = actual == exp
        hits += ok
        rows.append({"metric": metric, "expected": exp, "actual": actual,
                     "before": prev.stats_at_review[metric], "after": now_stats[metric], "hit": ok})
    return {"since": prev.date, "score": round(hits / len(rows), 2) if rows else None, "rows": rows}


@dataclass
class ReviewContext:
    date: str
    stats: dict
    history: list[dict]              # monthly stats, oldest first (includes "date")
    strategy: Strategy
    planner_summary: dict
    past_reviews: list[Review]
    recent_failures: list[str]


def _delta(history: list[dict], key: str, months: int) -> float | None:
    if len(history) <= months:
        return None
    return history[-1][key] - history[-1 - months][key]


# ------------------------------------------------------------------ rules --
class RuleReviewer:
    name = "rules"

    def review(self, ctx: ReviewContext) -> Review:
        s, st = Strategy(**ctx.strategy.to_dict()), ctx.stats
        why, unsure = [], []
        d_funds = _delta(ctx.history, "funds", 12)
        recent = ctx.history[-12:]
        stalled = sum(1 for h in recent if h["funds"] < s.reserve_funds) / max(len(recent), 1)
        if stalled >= 0.5 and s.tax_rate < 10:
            s.tax_rate += 1
            why.append(f"최근 {len(recent)}개월 중 {stalled:.0%} 동안 자금이 예비 자금({s.reserve_funds}) 아래라 "
                       f"건설이 멈춤: 세율을 {s.tax_rate}%로 올려 재원 확보")
        elif st["funds"] > 3 * s.reserve_funds + 5000 and s.tax_rate > 6:
            s.tax_rate -= 1
            why.append(f"자금 여유({st['funds']:.0f})가 커서 세율을 {s.tax_rate}%로 내려 성장 유도")
        if st["funds"] > 8000 and s.max_builds_per_month < 4:
            s.max_builds_per_month += 1
            why.append(f"자금 {st['funds']:.0f}: 월 건설 한도를 {s.max_builds_per_month}로 늘림")
        elif stalled >= 0.5 and s.max_builds_per_month > 1:
            s.max_builds_per_month -= 1
            why.append(f"자금 부족으로 월 건설 한도를 {s.max_builds_per_month}로 줄임")
        if st["crime_avg"] > 100 and s.zones_per_police > 8:
            s.zones_per_police = max(8, s.zones_per_police - 4)
            why.append(f"평균 범죄 {st['crime_avg']:.0f}: 경찰서를 {s.zones_per_police}구역당 1개로 늘림")
        elif 0 < st["crime_avg"] < 50 and st["population"] > 0 and s.zones_per_police < 30:
            s.zones_per_police += 4
            why.append(f"범죄가 낮아({st['crime_avg']:.0f}) 경찰서 기준을 {s.zones_per_police}구역당 1개로 완화")
        if st["pollution_avg"] > 80 and s.zone_mix["industrial"] > 0.15:
            s.zone_mix["industrial"] = round(s.zone_mix["industrial"] - 0.05, 3)
            s.zone_mix["commercial"] = round(s.zone_mix["commercial"] + 0.05, 3)
            why.append(f"오염 {st['pollution_avg']:.0f}: 공업 비중을 {s.zone_mix['industrial']}로 낮춤")
        if st["traffic_avg"] > 100:
            unsure.append("교통 혼잡이 높지만 현재 실행기에는 도로 확충 수단이 없음")
        if not why:
            why.append("뚜렷한 문제 신호가 없어 기존 전략 유지")
        any_demand = max(st["res_valve"], st["com_valve"], st["ind_valve"]) > 0
        expected = {"population": "up" if any_demand else "flat",
                    "funds": "up" if s.tax_rate >= ctx.strategy.tax_rate else "down",
                    "crime_avg": "down" if s.zones_per_police < ctx.strategy.zones_per_police else "up"}
        unsure.append("규칙 기반 판단: 여러 지표의 상호작용은 고려하지 않음")
        return Review(ctx.date, self.name, s.to_dict(), why, expected, unsure, stats_at_review=dict(st))


# -------------------------------------------------------------------- LLM --
PROMPT = """You are the mayor of a city in Micropolis (the open-source SimCity Classic engine).
Once per review you set the STRATEGY for the coming period. A plain-code executor then works every month:
it builds a power plant first if zones are unpowered, adds police/fire stations by your ratios, then zones
the type with the best (your zone_mix weight x current engine demand), up to max_builds_per_month, never
spending below reserve_funds. It cannot build roads beyond the fixed grid, parks, rail or airports.

Facts verified in this engine (by our own tests, not assumptions):
- Roads do not carry power; zones pass power to touching zones. One coal plant powered ~48 zones in a test;
  unpowered zones never develop.
- In a small test city after 5 years, tax 0% -> pop 2560, 7% -> 2280, 15% -> 820, 20% -> 320.
  Crime rose with population when there were no police stations.
- Costs: zone 100, police/fire 500, coal plant 3000, nuclear 5000, stadium 5000, seaport 3000, airport 10000.
  Taxes are collected yearly; nothing is built while funds are below reserve_funds.
- Engine rule (source code): residential demand is held at 0 when res_pop > 500 and there is no stadium;
  industrial when ind_pop > 70 and no seaport; commercial when com_pop > 100 and no airport.
  With build_specials=true the executor buys the missing one when this happens.
- land_use "separated" puts new industry in its own district away from housing. Over 6 test seeds it was
  more stable but peaked lower than "mixed"; neither was clearly better.

MEASURED NOW (exact engine values, date {date}):
{stats}

TRENDS (computed by code from monthly history):
{trends}

CITY LAYOUT STATE: {planner}
CURRENT STRATEGY: {strategy}
RECENT FAILED ACTIONS: {failures}

YOUR PAST REVIEWS AND HOW THEIR PREDICTIONS TURNED OUT:
{past}

Decide the strategy for the next period. Separate what is measured from what you assume.
Return ONE JSON object, no prose:
{{"strategy": {{"tax_rate": int {tax}, "zone_mix": {{"residential": w, "commercial": w, "industrial": w}},
  "reserve_funds": int {reserve}, "zones_per_police": int {police}, "zones_per_fire": int {fire},
  "power_type": "coal"|"nuclear", "max_builds_per_month": int {builds}, "build_specials": true|false,
  "land_use": "mixed"|"separated"}},
 "reasoning": ["short points in Korean, citing the measured numbers you rely on"],
 "alternatives": [{{"option": "Korean", "why_not": "Korean"}}],
 "expected": {{"population": "up|down|flat", "funds": "...", "crime_avg": "...", "pollution_avg": "...", "traffic_avg": "..."}},
 "uncertainties": ["Korean: what you do not know and how it could be checked"]}}
"""


def _fmt_stats(st: dict) -> str:
    return "\n".join(f"- {k}: {v:g}" for k, v in st.items() if k != "date")


def _fmt_trends(history: list[dict]) -> str:
    lines = []
    for key in ("population", "funds", "crime_avg", "pollution_avg", "traffic_avg", "land_value_avg"):
        parts = []
        for m in (3, 12):
            d = _delta(history, key, m)
            if d is not None:
                parts.append(f"{m}m {d:+g}")
        lines.append(f"- {key}: " + (", ".join(parts) if parts else "not enough history"))
    return "\n".join(lines)


def _fmt_past(reviews: list[Review], now_stats: dict, n: int = 3) -> str:
    if not reviews:
        return "(none yet)"
    out = []
    for r in reviews[-n:]:
        ev = r.evaluation_of_previous
        line = f"- {r.date} [{r.source}] strategy={json.dumps(r.strategy, ensure_ascii=False)} expected={r.expected}"
        if ev and ev.get("rows"):
            line += f"\n  (it scored the review before it: {ev['score']})"
        out.append(line)
    last = reviews[-1]
    ev = evaluate(last, now_stats)
    out.append(f"- outcome of the latest review ({last.date}) so far: score {ev['score']}; " +
               "; ".join(f"{r['metric']}: expected {r['expected']}, actual {r['actual']}" for r in ev["rows"]))
    return "\n".join(out)


class LLMReviewer:
    def __init__(self, llm, fallback: RuleReviewer | None = None):
        self.llm = llm
        self.fallback = fallback or RuleReviewer()
        self.name = f"llm:{llm.name}/{llm.model}"

    def build_prompt(self, ctx: ReviewContext) -> str:
        r = {k: f"{lo}-{hi}" for k, (lo, hi) in LIMITS.items()}
        return PROMPT.format(
            date=ctx.date, stats=_fmt_stats(ctx.stats), trends=_fmt_trends(ctx.history),
            planner=json.dumps(ctx.planner_summary), strategy=json.dumps(ctx.strategy.to_dict()),
            failures="; ".join(ctx.recent_failures[-5:]) or "none", past=_fmt_past(ctx.past_reviews, ctx.stats),
            tax=r["tax_rate"], reserve=r["reserve_funds"], police=r["zones_per_police"],
            fire=r["zones_per_fire"], builds=r["max_builds_per_month"])

    def review(self, ctx: ReviewContext) -> Review:
        from ..extractors.vision import extract_json
        from ..llm import LLMError
        try:
            res = self.llm.ask(self.build_prompt(ctx), None, max_tokens=1500)
            data = extract_json(res.text)
            if not isinstance(data, dict) or not isinstance(data.get("strategy"), dict):
                raise ValueError("no 'strategy' object in reply")
        except (LLMError, ValueError) as e:
            rv = self.fallback.review(ctx)
            rv.source = "rules (llm failed)"
            rv.notes.append(f"LLM review failed: {str(e)[:200]}")
            return rv
        strat, notes = Strategy.from_dict(data["strategy"], base=ctx.strategy)
        expected = {k: v for k, v in (data.get("expected") or {}).items()
                    if k in TRACKED and v in ("up", "down", "flat")}
        as_list = lambda v: [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])
        alts = [a for a in (data.get("alternatives") or []) if isinstance(a, dict)]
        return Review(ctx.date, self.name, strat.to_dict(), as_list(data.get("reasoning")), expected,
                      as_list(data.get("uncertainties")), alts, dict(ctx.stats), notes=notes, usage=res.usage())

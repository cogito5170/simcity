"""Learning across runs: code-measured digests of past runs, benchmark tables, and the
mayor's own reflections, packed into a lessons file the next mayor reads.

Everything numeric here is computed from run logs or fresh engine runs. Reflections are
written by the LLM and are passed on explicitly labelled as hypotheses.
"""

from __future__ import annotations

import json
import statistics
import tempfile
from pathlib import Path

from .review import Review
from .run import run_mayor
from .strategy import Strategy


def _load(run_dir: Path):
    months = [json.loads(x) for x in (run_dir / "months.jsonl").read_text(encoding="utf-8").splitlines()]
    rp = run_dir / "reviews.jsonl"
    reviews = [json.loads(x) for x in rp.read_text(encoding="utf-8").splitlines()] if rp.exists() else []
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return months, reviews, summary


def run_digest(run_dir: str | Path) -> dict:
    """What happened in one run, measured from its logs (no interpretation)."""
    months, reviews, summ = _load(Path(run_dir))
    pops = [m["stats"]["population"] for m in months]
    peak_i = max(range(len(pops)), key=pops.__getitem__)
    after_peak_min = min(pops[peak_i:])
    stalled = sum(1 for m in months
                  if any(a["status"] == "skipped" for a in m["actions"]) and not any(a["ok"] for a in m["actions"]))
    low_funds = next((m["date"] for m in months[12:] if m["stats"]["funds"] < 1000), None)
    taxes = [m["tax_rate"] for m in months]
    strat = [r["strategy"] for r in reviews]
    scores = [r["evaluation_of_previous"]["score"] for r in reviews
              if r.get("evaluation_of_previous") and r["evaluation_of_previous"].get("score") is not None]
    return {
        "run": Path(run_dir).name, "seed": summ["seed"], "years": summ["years"], "reviewer": summ.get("reviewer"),
        "final_population": pops[-1], "peak_population": pops[peak_i], "peak_date": months[peak_i]["date"],
        "drop_after_peak_pct": round(100 * (1 - after_peak_min / pops[peak_i]), 1) if pops[peak_i] else 0.0,
        "mean_population": round(statistics.mean(pops)),
        "months_build_stalled_for_money": stalled, "first_month_funds_below_1000": low_funds,
        "mean_tax_first_3_years": round(statistics.mean(taxes[:36]), 1), "mean_tax": round(statistics.mean(taxes), 1),
        "min_reserve": min((s["reserve_funds"] for s in strat), default=None),
        "max_builds_per_month": max((s["max_builds_per_month"] for s in strat), default=None),
        "land_use_used": sorted({s.get("land_use", "mixed") for s in strat}),
        "reviews": len(reviews), "prediction_hit_rate": round(statistics.mean(scores), 2) if scores else None,
        "built": {k: summ.get("planner", {}).get(k) for k in ("residential", "commercial", "industrial", "police", "fire")},
        "mean_residential_share_target": round(statistics.mean(st["zone_mix"]["residential"] for st in strat), 2)
        if strat else None,
    }


class _Fixed:
    name = "fixed"

    def __init__(self, s: Strategy):
        self.s = s

    def review(self, ctx):
        return Review(ctx.date, "fixed", self.s.to_dict(), ["fixed"], {}, stats_at_review=dict(ctx.stats))


def benchmark_table(seeds, years: int = 20, taxes=(5, 7, 9, 11), engine_dir: str | None = None) -> list[dict]:
    """Fixed strategies (default knobs, one tax each) on the given seeds, measured in the engine."""
    rows = []
    for tax in taxes:
        finals, peaks, stalls = [], [], []
        for seed in seeds:
            with tempfile.TemporaryDirectory() as d:
                r = run_mayor(years=years, seed=seed, reviewer=_Fixed(Strategy(tax_rate=tax)), out_dir=d,
                              engine_dir=engine_dir)
                dg = run_digest(r.run_dir)
            finals.append(dg["final_population"])
            peaks.append(dg["peak_population"])
            stalls.append(dg["months_build_stalled_for_money"])
        rows.append({"strategy": f"fixed, tax {tax}%, reserve 3000, 2 builds/month, mixed",
                     "seeds": list(seeds), "final_mean": round(statistics.mean(finals)),
                     "final_median": round(statistics.median(finals)), "peak_mean": round(statistics.mean(peaks)),
                     "months_stalled_mean": round(statistics.mean(stalls), 1)})
    return rows


GRID = {"tax_rate": (5, 7, 9), "residential_share": (0.4, 0.5, 0.6, 0.7), "reserve_funds": (1000, 3000),
        "max_builds_per_month": (2, 4), "land_use": ("mixed", "separated")}


def grid_strategy(cfg: dict) -> Strategy:
    r = cfg["residential_share"]
    return Strategy(tax_rate=cfg["tax_rate"], reserve_funds=cfg["reserve_funds"],
                    max_builds_per_month=cfg["max_builds_per_month"], land_use=cfg["land_use"],
                    zone_mix={"residential": r, "commercial": round((1 - r) / 2, 3), "industrial": round((1 - r) / 2, 3)})


def benchmark_grid(seeds, years: int = 20, grid: dict = GRID, engine_dir: str | None = None) -> list[dict]:
    """Every combination of the knobs, each on every seed. Returns one row per combination."""
    import itertools
    keys = list(grid)
    rows = []
    for values in itertools.product(*(grid[k] for k in keys)):
        cfg = dict(zip(keys, values))
        finals, means, peaks = [], [], []
        for seed in seeds:
            with tempfile.TemporaryDirectory() as d:
                r = run_mayor(years=years, seed=seed, reviewer=_Fixed(grid_strategy(cfg)), out_dir=d,
                              engine_dir=engine_dir)
            pops = [m["stats"]["population"] for m in r.months]
            finals.append(pops[-1])
            means.append(statistics.mean(pops))
            peaks.append(max(pops))
        rows.append({**cfg, "seeds": list(seeds), "mean_pop": round(statistics.mean(means)),
                     "mean_pop_min_seed": round(min(means)), "mean_pop_max_seed": round(max(means)),
                     "final_mean": round(statistics.mean(finals)), "peak_mean": round(statistics.mean(peaks))})
    return rows


def main_effects(rows: list[dict], grid: dict = GRID, metric: str = "mean_pop") -> dict:
    """Average of `metric` for each value of each knob, over all other knob settings."""
    return {k: {str(v): round(statistics.mean(r[metric] for r in rows if r[k] == v)) for v in vals}
            for k, vals in grid.items()}


REFLECT_PROMPT = """You were the AI mayor of a Micropolis city for {years} game years. Below is what the logs
measured (computed by code) and a condensed log of your strategy reviews. Reflect honestly.

MEASURED OUTCOME:
{digest}

BENCHMARKS (fixed strategies measured by code on other seeds):
{bench}

YOUR REVIEW LOG (date: strategy | first reason | score of the previous prediction):
{log}

Return ONE JSON object, Korean text:
{{"mistakes": ["what went wrong, citing the numbers"],
 "worked": ["what helped, citing the numbers"],
 "rules_for_next_time": ["concrete, checkable rules for the strategy knobs"],
 "open_questions": ["what is still unknown and how to test it"]}}
Mark anything you cannot support with the numbers above as a hypothesis ("가설:")."""


def _condensed_log(reviews: list[dict]) -> str:
    lines = []
    for r in reviews:
        s = r["strategy"]
        ev = (r.get("evaluation_of_previous") or {}).get("score")
        why = (r.get("reasoning") or [""])[0][:140]
        lines.append(f"- {r['date']}: tax {s['tax_rate']} reserve {s['reserve_funds']} builds {s['max_builds_per_month']} "
                     f"land_use {s.get('land_use')} | {why} | prev score {ev}")
    return "\n".join(lines)


def reflect(llm, run_dir: str | Path, bench: list[dict]) -> dict:
    from ..extractors.vision import extract_json
    months, reviews, summ = _load(Path(run_dir))
    dg = run_digest(run_dir)
    prompt = REFLECT_PROMPT.format(years=summ["years"], digest=json.dumps(dg, ensure_ascii=False, indent=1),
                                   bench=json.dumps(bench, ensure_ascii=False, indent=1), log=_condensed_log(reviews))
    res = llm.ask(prompt, None, max_tokens=1500)
    from ..llm.ledger import record
    record("reflection", res.usage(), llm.name, Path(run_dir).name)
    data = extract_json(res.text)
    as_list = lambda v: [str(x) for x in v] if isinstance(v, list) else []
    return {k: as_list(data.get(k)) for k in ("mistakes", "worked", "rules_for_next_time", "open_questions")} | {
        "by": res.model, "usage": res.usage()}


def build_lessons(run_dirs, bench_seeds, llm=None, years: int = 20, engine_dir: str | None = None,
                  grid: bool = True, reflect_on=None) -> dict:
    """grid=True: v2 lessons (every knob varied). reflect_on: run dirs to reflect on (default: all)."""
    bench = benchmark_table(bench_seeds, years, engine_dir=engine_dir)
    out = {"version": 2 if grid else 1, "benchmarks": bench}
    if grid:
        rows = benchmark_grid(bench_seeds, years, engine_dir=engine_dir)
        ranked = sorted(rows, key=lambda r: -r["mean_pop"])
        out["grid"] = {"knobs": {k: list(v) for k, v in GRID.items()}, "metric": "20-year mean population",
                       "main_effects": main_effects(rows), "best": ranked[:5], "worst": ranked[-3:],
                       "rows": rows}
    reflect_set = {str(Path(d)) for d in (reflect_on if reflect_on is not None else run_dirs)}
    past = []
    for d in run_dirs:
        entry = {"digest": run_digest(d)}
        if llm is not None and str(Path(d)) in reflect_set:
            entry["reflection"] = reflect(llm, d, out.get("grid", {}).get("best", bench))
        past.append(entry)
    out["past_runs"] = past
    return out


def format_lessons(lessons: dict, max_rules: int = 12) -> str:
    """Prompt text: measured facts first, reflections clearly marked as hypotheses."""
    out = ["Benchmarks measured by code (fixed strategies, other seeds, 20 years):"]
    for b in lessons.get("benchmarks", []):
        out.append(f"- {b['strategy']}: final mean {b['final_mean']}, median {b['final_median']}, "
                   f"peak mean {b['peak_mean']}, months with builds stalled for money {b['months_stalled_mean']}")
    g = lessons.get("grid")
    if g:
        out.append(f"Knob experiment measured by code ({len(g['rows'])} fixed strategies x seeds "
                   f"{g['rows'][0]['seeds']}, 20 years; metric = {g['metric']}).")
        out.append("Average of the metric for each knob value, over all other settings (main effects):")
        for k, eff in g["main_effects"].items():
            out.append(f"- {k}: " + ", ".join(f"{v} -> {m}" for v, m in eff.items()))
        cfg_txt = lambda r: (f"tax {r['tax_rate']}%, residential {r['residential_share']}, reserve {r['reserve_funds']}, "
                             f"builds {r['max_builds_per_month']}/month, {r['land_use']}: mean pop {r['mean_pop']} "
                             f"(seed range {r['mean_pop_min_seed']}-{r['mean_pop_max_seed']}), final {r['final_mean']}")
        out.append("Best combinations:")
        out += [f"- {cfg_txt(r)}" for r in g["best"]]
        out.append("Worst combinations:")
        out += [f"- {cfg_txt(r)}" for r in g["worst"]]
        out.append("Seed ranges are wide: the same strategy can do very differently in another city.")
    runs = lessons.get("past_runs", [])
    if runs:
        out.append("Your past runs, measured by code:")
        for r in runs:
            d = r["digest"]
            out.append(f"- seed {d['seed']}: final {d['final_population']:.0f}, peak {d['peak_population']:.0f} "
                       f"({d['peak_date']}), drop after peak {d['drop_after_peak_pct']}%, mean tax first 3y "
                       f"{d['mean_tax_first_3_years']}, min reserve {d['min_reserve']}, max builds/month "
                       f"{d['max_builds_per_month']}, funds first <1000 at {d['first_month_funds_below_1000']}, "
                       f"months stalled for money {d['months_build_stalled_for_money']}"
                       + (f", built R/C/I {d['built']['residential']}/{d['built']['commercial']}/{d['built']['industrial']}"
                          f", mean residential target {d['mean_residential_share_target']}" if d.get("built") else ""))
        rules = [x for r in runs for x in (r.get("reflection") or {}).get("rules_for_next_time", [])]
        if rules:
            out.append("Rules a previous mayor wrote after reflecting on those runs (HYPOTHESES, check them against "
                       "the measurements before relying on them):")
            seen = []
            for x in rules:
                if x not in seen:
                    seen.append(x)
            out += [f"- {x}" for x in seen[:max_rules]]
    return "\n".join(out)

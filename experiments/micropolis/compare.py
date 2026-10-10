"""Compare mayors on the same seeds: fixed strategies and the rule mayor (run here, free)
against existing LLM runs (read from their run directories).

    python experiments/micropolis/compare.py --seeds 1 2 3 4 \
        --llm "Claude Code" runs/a/micropolis-* --llm "Claude Code + lessons" runs/b/micropolis-*
"""

import argparse
import json
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mayor.micropolis.review import Review, RuleReviewer  # noqa: E402
from mayor.micropolis.run import run_mayor  # noqa: E402
from mayor.micropolis.strategy import Strategy  # noqa: E402


class Fixed:
    name = "fixed"

    def __init__(self, strategy):
        self.strategy = strategy

    def review(self, ctx):
        return Review(ctx.date, "fixed", self.strategy.to_dict(), ["fixed"], {}, stats_at_review=dict(ctx.stats))


def summarize(months, reviews):
    pops = [m["stats"]["population"] for m in months]
    scores = [r["evaluation_of_previous"]["score"] for r in reviews
              if r.get("evaluation_of_previous") and r["evaluation_of_previous"].get("score") is not None]
    return {"final": pops[-1], "peak": max(pops), "mean": statistics.mean(pops),
            "hit": statistics.mean(scores) if scores else None, "reviews": len(reviews)}


def run_baseline(make_reviewer, seed, years):
    with tempfile.TemporaryDirectory() as d:
        r = run_mayor(years=years, seed=seed, reviewer=make_reviewer(), out_dir=d)
        return summarize(r.months, [x.to_dict() for x in r.reviews])


def load_run(path: Path):
    months = [json.loads(x) for x in (path / "months.jsonl").read_text(encoding="utf-8").splitlines()]
    reviews = [json.loads(x) for x in (path / "reviews.jsonl").read_text(encoding="utf-8").splitlines()]
    summ = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    return summ["seed"], summ["years"], summarize(months, reviews), summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--llm", nargs="+", action="append", default=[], metavar=("LABEL", "RUN_DIR"),
                    help="a label followed by run directories; repeat for several groups")
    a = ap.parse_args()
    rows = {"고정 전략 (세율 7%)": lambda: Fixed(Strategy()),
            "고정 전략 (세율 9%)": lambda: Fixed(Strategy(tax_rate=9)),
            "규칙 기반 재검토": RuleReviewer}
    table = {name: {s: run_baseline(mk, s, a.years) for s in a.seeds} for name, mk in rows.items()}
    usage = {}
    for label, *dirs in a.llm:
        group, calls, toks, cost = {}, [], [], []
        for p in dirs:
            seed, years, summ, raw = load_run(Path(p))
            if seed in a.seeds and years == a.years:
                group[seed] = summ
                calls.append(raw.get("llm_calls") or 0)
                toks.append((raw.get("tokens") or {}).get("input", 0) + (raw.get("tokens") or {}).get("output", 0))
                cost.append(raw.get("llm_cost_usd"))
        table[label] = group
        if calls:
            known = [c for c in cost if c]
            usage[label] = (statistics.mean(calls), statistics.mean(toks), statistics.mean(known) if known else None)
    print("| 시장 | " + " | ".join(f"seed {s}" for s in a.seeds) + " | 평균 최종 | 중앙값 | 평균 정점 | 20년 평균 인구 | 예측 적중률 |")
    print("|---|" + "---|" * (len(a.seeds) + 5))
    for name, per in table.items():
        cells = [f"{per[s]['final']:,.0f}" if s in per else "—" for s in a.seeds]
        got = [per[s] for s in a.seeds if s in per]
        hits = [g["hit"] for g in got if g["hit"] is not None]
        print(f"| {name} | " + " | ".join(cells) +
              f" | {statistics.mean(g['final'] for g in got):,.0f} | {statistics.median(g['final'] for g in got):,.0f}"
              f" | {statistics.mean(g['peak'] for g in got):,.0f} | {statistics.mean(g['mean'] for g in got):,.0f}"
              f" | {f'{statistics.mean(hits):.0%}' if hits else '—'} |")
    if usage:
        print("\n| LLM 그룹 | 실행당 호출 | 실행당 토큰 | 실행당 정가 환산 |\n|---|---|---|---|")
        for label, (c, t, usd) in usage.items():
            print(f"| {label} | {c:.0f} | {t:,.0f} | {'—' if usd is None else f'${usd:.2f}'} |")


if __name__ == "__main__":
    main()

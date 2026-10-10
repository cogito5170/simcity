"""Compare mayors on the same seeds: fixed strategies and the rule mayor (run here, free)
against existing LLM runs (read from their run directories).

    python experiments/micropolis/compare.py --seeds 1 2 3 4 --llm-runs runs/micropolis-*
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
    ap.add_argument("--llm-runs", nargs="*", default=[])
    a = ap.parse_args()
    rows = {"고정 전략 (세율 7%)": lambda: Fixed(Strategy()),
            "고정 전략 (세율 9%)": lambda: Fixed(Strategy(tax_rate=9)),
            "규칙 기반 재검토": RuleReviewer}
    table = {name: {s: run_baseline(mk, s, a.years) for s in a.seeds} for name, mk in rows.items()}
    llm = {}
    for p in a.llm_runs:
        seed, years, summ, raw = load_run(Path(p))
        if seed in a.seeds and years == a.years:
            llm[seed] = summ
            reviews = [json.loads(x) for x in (Path(p) / "reviews.jsonl").read_text(encoding="utf-8").splitlines()]
            llm[seed]["fallbacks"] = sum("failed" in r["source"] for r in reviews)
    if llm:
        table["Claude Code 시장"] = llm
    print(f"| 시장 | " + " | ".join(f"seed {s}" for s in a.seeds) + " | 평균 최종 | 평균 정점 | 20년 평균 인구 | 예측 적중률 |")
    print("|---|" + "---|" * (len(a.seeds) + 4))
    for name, per in table.items():
        cells = [f"{per[s]['final']:,.0f}" if s in per else "—" for s in a.seeds]
        got = [per[s] for s in a.seeds if s in per]
        hits = [g["hit"] for g in got if g["hit"] is not None]
        print(f"| {name} | " + " | ".join(cells) +
              f" | {statistics.mean(g['final'] for g in got):,.0f} | {statistics.mean(g['peak'] for g in got):,.0f}"
              f" | {statistics.mean(g['mean'] for g in got):,.0f} | {f'{statistics.mean(hits):.0%}' if hits else '—'} |")


if __name__ == "__main__":
    main()

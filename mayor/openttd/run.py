"""Run the transport-company CEO on a live OpenTTD server.

    python -m mayor.openttd.run --provider claude-code --review-months 2 --until 1960-01-01

The server must be running with the LLMBridge GameScript (see tools/openttd_server.sh).
Every ~10 s the loop records the company's state (state.json, history.jsonl) for viewers;
every --review-months game months the CEO decides and the executor builds.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .admin import Admin
from .ceo import Executor, LLMCEO, RuleCEO, direction, execute, observe
from .game import Game


def months_between(a: str, b: str) -> int:
    ya, ma, _ = map(int, a.split("-"))
    yb, mb, _ = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def write_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def run(ceo, until: str, review_months: int, out_dir: Path, poll: float = 10.0, password: str = "mayor"):
    run_dir = out_dir / f"openttd-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True)
    if hasattr(ceo, "run_name"):
        ceo.run_name = run_dir.name
    a = Admin(password=password, name="ceo")
    g = Game(a, company=0)
    if not any(c["id"] == 0 for c in g.call("info").get("companies", [])):
        a.rcon("start_ai IdleAI")
        time.sleep(3)
    ex = Executor(g, run_dir)
    reviews, last_review_date = [], None
    towns = g.towns()
    print(f"run dir {run_dir}; {len(towns)} towns; CEO {getattr(ceo, 'name', 'rules')}", flush=True)
    while True:
        obs = observe(ex)
        with open(run_dir / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: obs[k] for k in ("date", "money", "loan", "value", "vehicles")} |
                               {"profit": sum(ln["profit_this_year"] for ln in obs["lines"])}) + "\n")
        if last_review_date is None or months_between(last_review_date, obs["date"]) >= review_months:
            towns = g.towns()
            cands = ex.candidates(towns)
            if reviews:   # score the previous review's predictions now that its period is over
                prev = reviews[-1]
                actual = {"money_next_review": direction(prev["obs"]["money"], obs["money"]),
                          "total_profit_next_review": direction(
                              sum(ln["profit_this_year"] for ln in prev["obs"]["lines"]),
                              sum(ln["profit_this_year"] for ln in obs["lines"]))}
                exp = prev["decision"].get("expected") or {}
                rows = [{"metric": k, "expected": exp[k], "actual": v, "hit": exp[k] == v}
                        for k, v in actual.items() if exp.get(k) in ("up", "down", "flat")]
                prev["evaluation"] = {"rows": rows, "score": (sum(r["hit"] for r in rows) / len(rows)) if rows else None}
            t0 = time.time()
            decision = ceo.decide(obs, cands, [{"date": r["date"], "actions": r["decision"].get("actions"),
                                                "results": r["results"], "evaluation": r.get("evaluation")}
                                               for r in reviews])
            results = execute(ex, decision, obs, cands)
            rec = {"date": obs["date"], "obs": obs, "candidates": [{k: v for k, v in c.items() if k != "_path"}
                                                                   for c in cands],
                   "decision": decision, "results": results, "seconds": round(time.time() - t0, 1)}
            reviews.append(rec)
            with open(run_dir / "reviews.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            last_review_date = obs["date"]
            print(f"{obs['date']} review ({decision.get('source')}): {json.dumps(decision.get('actions'), ensure_ascii=False)}"
                  f" -> {[r.get('ok') for r in results]}", flush=True)
            obs = observe(ex)
        write_json(run_dir / "state.json", {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "obs": obs,
            "lines": [{"id": ln.id, "a": ln.a, "b": ln.b, "a_xy": ln.a_xy, "b_xy": ln.b_xy, "stops": ln.stops,
                       "depot": ln.depot, "built": ln.built, "mid": ln.mid, "span": ln.span} for ln in ex.lines],
            "vehicles": g.vehicles(), "events": ex.events[-20:],
            "last_review": None if not reviews else {k: reviews[-1][k] for k in ("date", "decision", "results")} |
                           {"candidates": reviews[-1]["candidates"]},
            "reviews_total": len(reviews), "ceo": getattr(ceo, "name", "rules")})
        if obs["date"] >= until:
            print("reached", until, flush=True)
            return run_dir
        time.sleep(poll)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="rules", help="rules | claude-code | anthropic | gemini | openai")
    ap.add_argument("--model")
    ap.add_argument("--review-months", type=int, default=2)
    ap.add_argument("--until", default="1960-01-01")
    ap.add_argument("--out", default="runs")
    a = ap.parse_args()
    if a.provider == "rules":
        ceo = RuleCEO()
    else:
        from ..llm import make_llm
        ceo = LLMCEO(make_llm(a.provider, a.model))
    run(ceo, a.until, a.review_months, Path(a.out))


if __name__ == "__main__":
    main()

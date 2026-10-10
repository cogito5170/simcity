"""Append-only record of every LLM call: who asked, why, how many tokens, what it would cost.

One JSON line per call in runs/llm_usage.jsonl (or $MAYOR_USAGE_LEDGER). `python -m mayor usage`
summarises it. cost_usd is what the provider reports (for `claude -p` this is a list-price
estimate; a Claude subscription is not billed per token but the calls count toward its limits).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path("runs") / "llm_usage.jsonl"


def ledger_path() -> Path:
    return Path(os.environ.get("MAYOR_USAGE_LEDGER") or DEFAULT_PATH)


def record(purpose: str, usage: dict, provider: str, run: str | None = None) -> None:
    entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "purpose": purpose,
             "provider": provider, "run": run, **usage}
    path = ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # usage tracking must never break a run


def totals(entries: list[dict]) -> dict:
    out = {"calls": len(entries), "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0,
           "cost_known_calls": 0}
    for e in entries:
        out["input_tokens"] += e.get("input_tokens") or 0
        out["output_tokens"] += e.get("output_tokens") or 0
        out["latency_s"] += e.get("latency_s") or 0
        if e.get("cost_usd") is not None:
            out["cost_usd"] += e["cost_usd"]
            out["cost_known_calls"] += 1
    out["cost_usd"] = round(out["cost_usd"], 4)
    out["latency_s"] = round(out["latency_s"], 1)
    return out


def read(path: Path | None = None) -> list[dict]:
    path = path or ledger_path()
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarize(entries: list[dict], by: str) -> dict[str, dict]:
    groups = defaultdict(list)
    for e in entries:
        groups[str(e.get(by))].append(e)
    return {k: totals(v) for k, v in sorted(groups.items())}

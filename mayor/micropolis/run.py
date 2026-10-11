"""The mayor loop: observe -> (review) -> act -> verify -> record, once per game month."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .engine import City
from .planner import Planner, choose_origin, slot_order
from .review import Review, ReviewContext, RuleReviewer, evaluate
from .strategy import labor_base, Strategy, execute_month


@dataclass
class RunResult:
    run_dir: Path
    months: list[dict]
    reviews: list[Review]
    tiles: list = field(repr=False, default_factory=list)


def _alerts(stats: dict, prev: dict | None) -> list[str]:
    out = []
    if stats["unpowered_zones"] > 0 and prev and prev["unpowered_zones"] > 0:
        out.append(f"unpowered zones for 2+ months: {stats['unpowered_zones']:.0f}")
    if prev and stats["funds"] < 1000 <= prev["funds"]:
        out.append("funds dropped below 1000")
    if prev and prev["population"] > 2000 and stats["population"] < prev["population"] * 0.9:
        out.append("population fell more than 10% in a month")
    if stats.get("zones_lost_this_month", 0) >= 2 or stats.get("fire_tiles", 0) >= 20:
        out.append(f"disaster damage: {stats.get('zones_lost_this_month', 0):.0f} zones lost, "
                   f"{stats.get('fire_tiles', 0):.0f} tiles burning")
    return out


def run_mayor(years: int = 20, seed: int = 0, reviewer=None, review_every: int = 12, out_dir: str | Path = "runs",
              engine_dir: str | None = None, start_funds: int | None = None, on_month=None,
              terrain: bool = False, disasters: bool = False) -> RunResult:
    city = City(seed=seed, engine_dir_path=engine_dir, terrain=terrain, disasters=disasters)
    if start_funds is not None:
        city.e.set_funds(start_funds)
    ox, oy, water_in_core = choose_origin(city.tiles()) if terrain else (None, None, 0)
    planner = Planner(city, ox=ox, oy=oy) if terrain else Planner(city)
    reviewer = reviewer or RuleReviewer()
    strategy = Strategy()
    base = Path(out_dir) / f"micropolis-{datetime.now().strftime('%Y%m%d-%H%M%S')}-s{seed}"
    run_dir, n = base, 1
    while run_dir.exists():          # never append to another run's logs
        n += 1
        run_dir = base.with_name(f"{base.name}-{n}")
    run_dir.mkdir(parents=True)
    if hasattr(reviewer, "llm"):
        reviewer.run_name = run_dir.name   # tags this run's calls in the usage ledger
    months, reviews, tiles, failures = [], [], [], []
    months_since_power = 99
    unpowered_streak = 0
    last_review_month = -review_every
    (run_dir / "saves").mkdir(exist_ok=True)

    for m in range(years * 12):
        damage = planner.audit()      # disasters: what we built and no longer exists
        stats = city.stats()
        stats["zones_lost_this_month"] = float(damage["zones_lost"])
        stats["roads_lost_this_month"] = float(damage["roads_lost"] + damage["wires_lost"])
        stats["labor_base"] = round(labor_base(stats), 2)
        date = str(city.date)
        prev = months[-1]["stats"] if months else None
        alerts = _alerts(stats, prev)
        history = [dict(mm["stats"], date=mm["date"]) for mm in months] + [dict(stats, date=date)]

        # strategy review: on schedule, or early (>= 3 months later) when something is wrong
        due = m - last_review_month >= review_every or (alerts and m - last_review_month >= 3 and m > 0)
        if due:
            ctx = ReviewContext(date, stats, history[-36:], strategy, planner_summary(planner), reviews, failures)
            rv = reviewer.review(ctx)
            if reviews:
                rv.evaluation_of_previous = evaluate(reviews[-1], stats)
            strategy, _ = Strategy.from_dict(rv.strategy, base=strategy)
            reviews.append(rv)
            last_review_month = m
            with open(run_dir / "reviews.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rv.to_dict(), ensure_ascii=False) + "\n")

        unpowered_streak = unpowered_streak + 1 if stats["unpowered_zones"] > 0 else 0
        actions = execute_month(planner, strategy, stats, months_since_power, unpowered_streak,
                                damaged=planner.needs_repair())
        months_since_power = 0 if any(a.action.startswith("power") and a.ok for a in actions) else months_since_power + 1
        failures += [f"{date} {a.action}: {a.detail}" for a in actions if a.status == "failed"]
        rec_damage = damage if (damage["zones_lost"] or damage["plants_lost"] or damage["specials_lost"]
                                 or damage["roads_lost"] or damage["wires_lost"]) else None
        rec = {"month": m, "date": date, "stats": stats, "alerts": alerts, "reviewed": bool(due), "damage": rec_damage,
               "actions": [a.to_dict() for a in actions], "tax_rate": strategy.tax_rate}
        months.append(rec)
        with open(run_dir / "months.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tiles.append(city.tiles().astype(np.uint16))
        np.save(run_dir / "live_tiles.tmp.npy", tiles[-1])           # current map, for live viewers
        os.replace(run_dir / "live_tiles.tmp.npy", run_dir / "live_tiles.npy")
        if on_month:
            on_month(rec)
        if m % 60 == 59:
            city.save(run_dir / "saves" / f"{date}.cty")
        city.advance_month()

    city.save(run_dir / "saves" / "final.cty")
    final = {"date": str(city.date), "stats": city.stats(), "planner": planner_summary(planner),
             "reviewer": getattr(reviewer, "name", "rules"), "seed": seed, "years": years,
             "terrain": terrain, "disasters": disasters,
             "llm_calls": sum(1 for r in reviews if r.usage),
             "tokens": {"input": sum((r.usage or {}).get("input_tokens") or 0 for r in reviews),
                        "output": sum((r.usage or {}).get("output_tokens") or 0 for r in reviews)},
             "llm_cost_usd": round(sum((r.usage or {}).get("cost_usd") or 0 for r in reviews), 4),
             "llm_seconds": round(sum((r.usage or {}).get("latency_s") or 0 for r in reviews), 1)}
    (run_dir / "summary.json").write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    np.savez_compressed(run_dir / "tiles.npz", tiles=np.stack(tiles))
    return RunResult(run_dir, months, reviews, tiles)


def planner_summary(p: Planner) -> dict:
    free = sum(1 for s in slot_order(conn_every=p.conn_every, ox=p.ox, oy=p.oy)
               if s not in p.used_slots and s not in p.blocked)
    return {"residential": p.count("residential"), "commercial": p.count("commercial"),
            "industrial": p.count("industrial"), "police": p.count("police"), "fire": p.count("fire"),
            "power_plants": [k for _, _, k in p.plants], "free_slots": free, "blocked_slots": len(p.blocked),
            "road_spacing": p.conn_every, "road_spacing_locked": bool(p.used_slots),
            "origin": [p.ox, p.oy], "zones_lost_to_disasters": len(p.destroyed)}

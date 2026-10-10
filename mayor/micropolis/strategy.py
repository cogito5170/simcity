"""Strategy (decided rarely, by the reviewer) and the monthly executor (plain code).

The reviewer — rules or an LLM — only sets these knobs. Turning them into
concrete builds every month is ordinary code, so the LLM is called about once
per game year instead of every month.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .engine import demand_caps
from .planner import ActionResult, Planner

LIMITS = {
    "tax_rate": (0, 20),
    "reserve_funds": (0, 50000),
    "zones_per_police": (4, 200),
    "zones_per_fire": (4, 200),
    "max_builds_per_month": (0, 8),
}
VALVE_MAX = {"residential": 2000.0, "commercial": 1500.0, "industrial": 1500.0}


@dataclass
class Strategy:
    tax_rate: int = 7
    zone_mix: dict = field(default_factory=lambda: {"residential": 0.6, "commercial": 0.2, "industrial": 0.2})
    reserve_funds: int = 3000
    zones_per_police: int = 20
    zones_per_fire: int = 25
    power_type: str = "coal"
    max_builds_per_month: int = 2
    build_specials: bool = True   # stadium / seaport / airport when the engine caps demand without them
    land_use: str = "mixed"       # "mixed" | "separated" (industry in its own district, away from housing)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict, base: "Strategy | None" = None) -> tuple["Strategy", list[str]]:
        """Build a valid strategy from untrusted input (e.g. LLM JSON). Returns (strategy, notes about fixes)."""
        s = Strategy(**(base or Strategy()).to_dict())
        notes = []
        for k, (lo, hi) in LIMITS.items():
            if k in d:
                try:
                    v = int(round(float(d[k])))
                except (TypeError, ValueError):
                    notes.append(f"{k}: ignored non-number {d[k]!r}")
                    continue
                if not lo <= v <= hi:
                    notes.append(f"{k}: {v} clamped to [{lo}, {hi}]")
                    v = min(max(v, lo), hi)
                setattr(s, k, v)
        if isinstance(d.get("zone_mix"), dict):
            mix = {}
            for z in VALVE_MAX:
                try:
                    mix[z] = max(0.0, float(d["zone_mix"].get(z, 0)))
                except (TypeError, ValueError):
                    mix[z] = 0.0
            total = sum(mix.values())
            if total > 0:
                s.zone_mix = {z: round(v / total, 3) for z, v in mix.items()}
            else:
                notes.append("zone_mix: all zero, kept previous")
        if d.get("land_use") in ("mixed", "separated"):
            s.land_use = d["land_use"]
        elif "land_use" in d:
            notes.append(f"land_use: {d['land_use']!r} not mixed/separated, kept {s.land_use}")
        if isinstance(d.get("build_specials"), bool):
            s.build_specials = d["build_specials"]
        if d.get("power_type") in ("coal", "nuclear"):
            s.power_type = d["power_type"]
        elif "power_type" in d:
            notes.append(f"power_type: {d['power_type']!r} not coal/nuclear, kept {s.power_type}")
        return s, notes


def choose_zone(strategy: Strategy, stats: dict, counts: dict | None = None) -> str | None:
    """Zone type to build next, or None when no type has positive demand.

    Among types the engine reports positive demand for, pick the one furthest
    below its target share in zone_mix (ties: stronger demand).
    """
    counts = counts or {}
    total = sum(counts.get(z, 0) for z in VALVE_MAX)
    best, best_key = None, None
    for z, vmax in VALVE_MAX.items():
        demand = stats[f"{z[:3]}_valve"] / vmax          # -1..1, exact engine value
        if demand <= 0:
            continue
        share = counts.get(z, 0) / total if total else 0.0
        key = (strategy.zone_mix.get(z, 0) - share, demand)
        if best_key is None or key > best_key:
            best, best_key = z, key
    return best


def execute_month(planner: Planner, strategy: Strategy, stats: dict, months_since_power: int,
                  unpowered_streak: int = 0) -> list[ActionResult]:
    """One month of routine work. Every build is verified by the planner."""
    city = planner.city
    city.set_tax(strategy.tax_rate)
    planner.reserve = strategy.reserve_funds
    planner.districts = strategy.land_use == "separated"
    done: list[ActionResult] = []
    budget = strategy.max_builds_per_month

    # 1. power first: unpowered zones never grow (verified in the engine). New zones read as
    #    unpowered until the engine's next power scan, so only a 3-month streak means "short of power".
    need_power = not planner.plants or (unpowered_streak >= 3 and months_since_power >= 3)
    if need_power and budget > 0:
        done.append(planner.build_power(strategy.power_type))
        budget -= 1
    # 2. special buildings: without them the engine holds that demand at 0 (see engine.demand_caps)
    if strategy.build_specials:
        for kind, capped in demand_caps(stats).items():
            if capped and budget > 0 and kind not in planner.specials:
                r = planner.build_special(kind)
                done.append(r)
                budget -= 1
                break   # one big purchase per month
    # 3. services by ratio
    zones = planner.zones()
    for kind, per in (("police", strategy.zones_per_police), ("fire", strategy.zones_per_fire)):
        if budget > 0 and zones >= per and planner.count(kind) < zones // per:
            done.append(planner.place(kind))
            budget -= 1
    # 4. zones following demand
    while budget > 0:
        counts = {z: planner.count(z) for z in VALVE_MAX}
        kind = choose_zone(strategy, city.stats(), counts)
        if kind is None:
            break
        r = planner.place(kind)
        done.append(r)
        budget -= 1
        if not r.ok:   # skipped for money or failed: stop for this month
            break
    return done

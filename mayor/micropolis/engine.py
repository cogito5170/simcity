"""Thin wrapper over the Micropolis engine (gymtropolis C API, loaded via ctypes).

The engine checkout is found via $MICROPOLIS_ENGINE_DIR or third_party/gymtropolis
(see tools/setup_micropolis.sh). Only the engine module is imported, not the
gymnasium wrapper, so numpy is the only Python dependency.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_VAR = "MICROPOLIS_ENGINE_DIR"


class EngineNotFound(RuntimeError):
    pass


def engine_dir(explicit: str | None = None) -> Path:
    for cand in (explicit, os.environ.get(ENV_VAR), REPO_ROOT / "third_party" / "gymtropolis"):
        if cand and (Path(cand) / "gymtropolis" / "engine.py").exists():
            return Path(cand)
    raise EngineNotFound("Micropolis engine not found: run `bash tools/setup_micropolis.sh` "
                         f"or set {ENV_VAR} to a gymtropolis checkout")


_mods: dict = {}


def load_modules(explicit: str | None = None):
    """Import gymtropolis.engine and gymtropolis.tiles without gymtropolis/__init__ (gymnasium)."""
    if _mods:
        return _mods["engine"], _mods["tiles"]
    root = engine_dir(explicit)
    if "gymtropolis" not in sys.modules:
        pkg = types.ModuleType("gymtropolis")
        pkg.__path__ = [str(root / "gymtropolis")]
        sys.modules["gymtropolis"] = pkg
    _mods["engine"] = importlib.import_module("gymtropolis.engine")
    _mods["tiles"] = importlib.import_module("gymtropolis.tiles")
    return _mods["engine"], _mods["tiles"]


# Stats the mayor reads every month (all exact engine values).
STAT_KEYS = ("funds", "population", "res_valve", "com_valve", "ind_valve", "city_tax", "cash_flow",
             "crime_avg", "pollution_avg", "land_value_avg", "traffic_avg", "city_score",
             "powered_zones", "unpowered_zones", "police_stations", "fire_stations",
             "coal_plants", "nuclear_plants", "road_total", "assessed_value",
             "res_pop", "com_pop", "ind_pop", "stadiums", "seaports", "airports",
             "road_effect", "police_effect", "fire_effect")


def demand_caps(stats: dict) -> dict:
    """Which demand is capped for lack of a special building.

    Same rule as the engine (engine_src/message.cpp, cases 26/28/30): residential
    demand is held at 0 when res_pop > 500 without a stadium; industrial when
    ind_pop > 70 without a seaport; commercial when com_pop > 100 without an airport.
    """
    return {"stadium": stats["res_pop"] > 500 and stats["stadiums"] == 0,
            "seaport": stats["ind_pop"] > 70 and stats["seaports"] == 0,
            "airport": stats["com_pop"] > 100 and stats["airports"] == 0}


@dataclass
class GameDate:
    year: int
    month: int  # 1..12

    def __str__(self) -> str:
        return f"{self.year}-{self.month:02d}"


class City:
    """One headless city. All values are read from the engine, nothing is estimated here."""

    def __init__(self, seed: int = 0, engine_dir_path: str | None = None, disasters: bool = False):
        self.eng, self.tiles_mod = load_modules(engine_dir_path)
        self.Tool = self.eng.Tool
        self.OK = self.eng.ToolResult.OK
        self.e = self.eng.GymtropolisEngine()
        self.e.new_city(seed=seed, blank=True)
        self.e.seed(seed)
        self.e.set_disasters(disasters)
        self.e.set_auto_budget(True)
        self.e.set_auto_bulldoze(True)
        self.width, self.height = self.eng.WORLD_W, self.eng.WORLD_H

    # -- time
    def advance_month(self) -> None:
        self.e.advance(self.eng.TICKS_PER_MONTH)

    @property
    def city_time(self) -> int:
        return int(self.e.stats()["city_time"])

    @property
    def date(self) -> GameDate:
        t = self.city_time
        return GameDate(1900 + t // 48, (t % 48) // 4 + 1)

    # -- observation
    def stats(self) -> dict:
        s = self.e.stats()
        return {k: float(s[k]) for k in STAT_KEYS}

    def tiles(self):
        return self.e.tiles.copy()

    def render(self, tiles=None):
        return self.tiles_mod.render_tiles(self.tiles() if tiles is None else tiles, 16)

    # -- action
    def tool(self, tool, x: int, y: int):
        return self.e.do_tool(tool, x, y)

    def set_tax(self, rate: int) -> None:
        self.e.set_tax(int(rate))

    def save(self, path: str | Path) -> None:
        self.e.save_city(str(path))

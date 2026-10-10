"""Headless probe of the Micropolis (SimCity Classic) engine via gymtropolis's C API.

Builds a tiny city, runs 5 game years, checks determinism and compares tax rates.
Used for docs/EVAL_GAMES.md. Needs Linux/WSL, g++ and numpy (gymnasium not required):

    git clone https://github.com/elmihailol/gymtropolis && make -C gymtropolis
    GYMTROPOLIS_DIR=$PWD/gymtropolis python experiments/micropolis/probe.py
"""
import importlib
import os
import sys
import time
import types

ROOT = os.environ.get("GYMTROPOLIS_DIR", "../gymtropolis")
pkg = types.ModuleType("gymtropolis")  # load the engine module without the gymnasium wrapper
pkg.__path__ = [os.path.join(ROOT, "gymtropolis")]
sys.modules["gymtropolis"] = pkg
eng = importlib.import_module("gymtropolis.engine")
Engine, T = eng.GymtropolisEngine, eng.Tool

KEYS = ("city_time", "funds", "population", "res_valve", "com_valve", "ind_valve", "city_tax",
        "crime_avg", "pollution_avg", "land_value_avg", "traffic_avg", "city_score", "powered_zones")


def kpi(e):
    s = e.stats()
    return {k: round(float(s[k]), 1) for k in KEYS}


def build(e):
    e.do_tool(T.COAL_POWER, 21, 21)
    kinds = [T.RESIDENTIAL, T.RESIDENTIAL, T.COMMERCIAL, T.RESIDENTIAL,
             T.INDUSTRIAL, T.RESIDENTIAL, T.COMMERCIAL, T.INDUSTRIAL]
    for i, x in enumerate(range(21, 21 + 3 * len(kinds), 3)):  # zones touch each other and the plant
        e.do_tool(kinds[i], x, 25)
    for x in range(19, 47):
        e.do_tool(T.ROAD, x, 27)


def run(tax, years=5, seed=1):
    e = Engine()
    e.new_city(seed=seed, blank=True)
    e.set_disasters(False)
    e.set_auto_budget(True)
    e.set_tax(tax)
    build(e)
    t0 = time.time()
    e.advance(48 * years)  # city_time advances 48 per game year
    return kpi(e), time.time() - t0


k, dt = run(7)
print(f"tax 7%, 5 years in {dt * 1000:.0f} ms:", k)
print("determinism:", "same input -> same result" if run(7)[0] == run(7)[0] else "DIFFERENT")
for tax in (0, 7, 15, 20):
    k = run(tax)[0]
    print(f"tax={tax:>2}%  pop={k['population']:>7}  funds={k['funds']:>8}  "
          f"crime={k['crime_avg']}  pollution={k['pollution_avg']}  score={k['city_score']}")

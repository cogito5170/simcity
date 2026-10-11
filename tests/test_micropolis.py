"""Micropolis mayor: pure logic always; engine-backed tests when the engine is built
(bash tools/setup_micropolis.sh, or MICROPOLIS_ENGINE_DIR)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mayor.llm.mock import MockLLM
from mayor.micropolis.review import LLMReviewer, Review, ReviewContext, RuleReviewer, direction, evaluate
from mayor.micropolis.strategy import Strategy, choose_zone

try:
    from mayor.micropolis.engine import City, demand_caps, engine_dir
    engine_dir()
    HAVE_ENGINE = True
except Exception:  # engine not built here
    HAVE_ENGINE = False

STATS = {"funds": 5000.0, "population": 4000.0, "res_valve": 1000.0, "com_valve": 750.0, "ind_valve": -100.0,
         "city_tax": 7.0, "cash_flow": 0.0, "crime_avg": 60.0, "pollution_avg": 40.0, "land_value_avg": 70.0,
         "traffic_avg": 20.0, "city_score": 600.0, "powered_zones": 20.0, "unpowered_zones": 0.0,
         "police_stations": 1.0, "fire_stations": 1.0, "coal_plants": 1.0, "nuclear_plants": 0.0,
         "road_total": 100.0, "assessed_value": 0.0, "res_pop": 300.0, "com_pop": 20.0, "ind_pop": 10.0,
         "stadiums": 0.0, "seaports": 0.0, "airports": 0.0, "road_effect": 32.0, "police_effect": 1000.0,
         "fire_effect": 1000.0}


def ctx(**over):
    st = dict(STATS, **over)
    hist = [dict(st, date=f"1901-{m:02d}") for m in range(1, 13)]
    return ReviewContext("1902-01", st, hist, Strategy(), {"residential": 10}, [], [])


class StrategyTests(unittest.TestCase):
    def test_from_dict_clamps_and_reports(self):
        s, notes = Strategy.from_dict({"tax_rate": 55, "zone_mix": {"residential": 2, "commercial": 1, "industrial": 1},
                                       "power_type": "solar", "land_use": "separated", "max_builds_per_month": "x"})
        self.assertEqual(s.tax_rate, 20)
        self.assertEqual(s.zone_mix, {"residential": 0.5, "commercial": 0.25, "industrial": 0.25})
        self.assertEqual(s.land_use, "separated")
        self.assertEqual(s.power_type, "coal")
        self.assertEqual(len(notes), 3)  # tax clamped, power_type rejected, builds not a number

    def test_choose_zone_follows_target_share_and_demand(self):
        s = Strategy(zone_mix={"residential": 0.5, "commercial": 0.5, "industrial": 0.0})
        self.assertEqual(choose_zone(s, STATS, {"residential": 5, "commercial": 1}), "commercial")
        self.assertEqual(choose_zone(s, STATS, {"residential": 1, "commercial": 5}), "residential")
        none = dict(STATS, res_valve=-1, com_valve=-1, ind_valve=0)
        self.assertIsNone(choose_zone(s, none, {}))  # never builds against negative demand


class ReviewTests(unittest.TestCase):
    def test_direction_and_evaluate(self):
        self.assertEqual(direction(100, 110), "up")
        self.assertEqual(direction(100, 101), "flat")
        self.assertEqual(direction(0, -5), "down")
        prev = Review("1901-01", "rules", {}, [], {"population": "up", "funds": "down", "bogus": "up"},
                      stats_at_review={"population": 1000, "funds": 5000})
        ev = evaluate(prev, {"population": 1500, "funds": 5000})
        self.assertEqual([r["hit"] for r in ev["rows"]], [True, False])
        self.assertEqual(ev["score"], 0.5)

    def test_rule_reviewer_raises_tax_when_construction_stalls(self):
        rv = RuleReviewer().review(ctx(funds=500.0))
        self.assertEqual(rv.strategy["tax_rate"], 8)
        self.assertTrue(any("세율" in w for w in rv.reasoning))

    def test_llm_reviewer_applies_valid_json(self):
        reply = {"strategy": {"tax_rate": 9, "land_use": "separated"}, "reasoning": ["자금 부족"],
                 "expected": {"funds": "up", "made_up": "up"}, "uncertainties": ["모름"]}
        llm = MockLLM(response=reply)
        rv = LLMReviewer(llm).review(ctx())
        self.assertEqual((rv.strategy["tax_rate"], rv.strategy["land_use"]), (9, "separated"))
        self.assertEqual(rv.expected, {"funds": "up"})
        self.assertIn("MEASURED NOW", llm.calls[0])

    def test_llm_garbage_falls_back_to_rules(self):
        rv = LLMReviewer(MockLLM(response="I like trains")).review(ctx())
        self.assertEqual(rv.source, "rules (llm failed)")
        self.assertTrue(rv.notes)


@unittest.skipUnless(HAVE_ENGINE, "Micropolis engine not built")
class EngineTests(unittest.TestCase):
    def test_power_zones_specials(self):
        from mayor.micropolis.planner import Planner
        c = City(seed=1)
        c.e.set_funds(100000)
        p = Planner(c)
        self.assertTrue(p.build_power("coal").ok)
        self.assertTrue(p.build_special("seaport").ok)          # works without the stadium in between
        for _ in range(4):
            self.assertTrue(p.place("residential").ok)
        for _ in range(3):
            c.advance_month()
        s = c.stats()
        self.assertEqual((s["unpowered_zones"], s["seaports"]), (0, 1))
        p.reserve = 10 ** 9
        r = p.place("commercial")
        self.assertEqual(r.status, "skipped")

    def test_separated_industry_is_powered(self):
        from mayor.micropolis.planner import IND_PAIR_START, Planner
        c = City(seed=1)
        c.e.set_funds(100000)
        p = Planner(c, districts=True)
        p.build_power("coal")
        for k in ["industrial", "residential"] * 3:
            self.assertTrue(p.place(k).ok)
        for _ in range(4):
            c.advance_month()
        self.assertEqual(c.stats()["unpowered_zones"], 0)   # regression: districts must touch the spine
        self.assertTrue(all((s[0] >= IND_PAIR_START) == (k == "industrial") for s, k in p.used_slots.items()))

    def test_department_funding_has_effect(self):
        c = City(seed=9)
        if not c.has_budget_control:
            self.skipTest("engine built without the budget patch")
        from mayor.micropolis.run import run_mayor
        from mayor.micropolis.lessons import _Fixed
        with tempfile.TemporaryDirectory() as d:
            full = run_mayor(years=6, seed=9, reviewer=_Fixed(Strategy()), out_dir=Path(d) / "a").months[-1]["stats"]
            cut = run_mayor(years=6, seed=9, reviewer=_Fixed(Strategy(road_funding=50, police_funding=50)),
                            out_dir=Path(d) / "b").months[-1]["stats"]
        self.assertEqual((full["road_effect"], full["police_effect"]), (32, 1000))
        self.assertEqual((cut["road_pct"], cut["auto_budget"]), (50, 0))
        self.assertLess(cut["road_effect"], 32)        # needs mp_update_fund_effects (headless engines skip it)
        self.assertLess(cut["police_effect"], 1000)

    def test_road_spacing_locks_after_first_zone(self):
        from mayor.micropolis.planner import Planner
        c = City(seed=1)
        c.e.set_funds(100000)
        p = Planner(c)
        self.assertTrue(p.set_road_spacing(3))
        p.build_power("coal")
        for _ in range(8):
            self.assertTrue(p.place("residential").ok)
        self.assertFalse(p.set_road_spacing(6))
        self.assertEqual(p.conn_every, 3)
        for _ in range(3):
            c.advance_month()
        self.assertEqual(c.stats()["unpowered_zones"], 0)   # wire crossings follow the spacing

    def test_terrain_origin_water_and_audit(self):
        import numpy as np
        from mayor.micropolis.planner import WATER, Planner, choose_origin
        c = City(seed=17, terrain=True)
        t = c.tiles()
        self.assertGreater(((t >= WATER[0]) & (t <= WATER[1])).mean(), 0.1)   # really a terrain map
        ox, oy, _ = choose_origin(t)
        c.e.set_funds(200000)
        p = Planner(c, ox=ox, oy=oy)
        self.assertTrue(p.build_power("coal").ok)
        placed = [p.place("residential") for _ in range(20)]
        self.assertTrue(all(r.ok for r in placed))                    # water slots are skipped, not failed
        for _ in range(14):                                           # traffic animates road/power crossings
            c.advance_month()
            d = p.audit()
            self.assertEqual((d["wires_lost"], d["zones_lost"]), (0, 0))
        self.assertEqual(choose_origin(np.zeros((100, 120), dtype=int))[:2], (14, 14))   # blank map: default

    def test_first_plant_reaches_the_grid_on_terrain(self):
        from mayor.micropolis.planner import WATER, Planner, choose_origin
        c = City(seed=20, terrain=True)        # here the old origin put plant slot 0 on water
        ox, oy, _ = choose_origin(c.tiles())
        t = c.tiles()
        self.assertFalse(((t[oy - 6:oy, ox - 4:ox] >= WATER[0]) & (t[oy - 6:oy, ox - 4:ox] <= WATER[1])).any())
        c.e.set_funds(100000)
        p = Planner(c, ox=ox, oy=oy)
        self.assertTrue(p.build_power("coal").ok)
        self.assertEqual(p.plants[0][:2], p.plant_slots[0])
        for _ in range(6):
            self.assertTrue(p.place("residential").ok)
        for _ in range(4):
            c.advance_month()
        self.assertEqual(c.stats()["unpowered_zones"], 0)

    def test_lost_roads_are_rebuilt_only_with_repair_roads(self):
        from mayor.micropolis.planner import Planner
        c = City(seed=1)
        c.e.set_funds(100000)
        p = Planner(c)
        self.assertTrue(p.build_power("coal").ok)
        self.assertTrue(p.place("residential").ok)
        x, y = sorted(p.roads - p.wires)[0]
        c.tool(p.T.BULLDOZER, x, y)
        self.assertEqual(p.audit()["roads_lost"], 0)          # default: decayed roads are left alone
        self.assertFalse(p.needs_repair())
        p.track_roads = True
        self.assertEqual(p.audit()["roads_lost"], 1)
        self.assertTrue(p.needs_repair())
        self.assertTrue(p.repair().ok)
        self.assertTrue(64 <= (c.e.map_values[y, x] & 0x3FF) <= 206)
        self.assertTrue(Strategy.from_dict({"repair_roads": True})[0].repair_roads)

    def test_destroyed_zone_is_rebuilt_in_its_own_slot_without_demand(self):
        from mayor.micropolis.planner import Planner
        from mayor.micropolis.strategy import execute_month
        c = City(seed=1)
        c.e.set_funds(100000)
        p = Planner(c)
        self.assertTrue(p.build_power("coal").ok)
        r = p.place("residential")
        slot = next(iter(p.used_slots))
        cx, cy = r.where
        for dx in (-1, 0, 1):                       # wipe the zone like a fire would
            for dy in (-1, 0, 1):
                c.tool(p.T.BULLDOZER, cx + dx, cy + dy)
        self.assertEqual(p.audit()["zones_lost"], 1)
        st = Strategy(max_builds_per_month=0)        # no new building at all, and no demand needed
        done = execute_month(p, st, dict(c.stats(), res_valve=-2000, com_valve=-1500, ind_valve=-1500), 0)
        self.assertIn("rebuild:residential", [a.action for a in done if a.ok])
        self.assertEqual(p.used_slots.get(slot), "residential")
        self.assertFalse(p.lost)

    def test_balance_jobs_holds_commerce_and_industry_when_jobs_outnumber_residents(self):
        from mayor.micropolis.strategy import labor_base
        st = dict(STATS, res_pop=400, com_pop=40, ind_pop=40, res_valve=1000, com_valve=1500, ind_valve=1500)
        self.assertAlmostEqual(labor_base(st), 50 / 80)
        mix = {"residential": 0.1, "commercial": 0.45, "industrial": 0.45}
        self.assertIn(choose_zone(Strategy(zone_mix=mix), st), ("commercial", "industrial"))
        self.assertEqual(choose_zone(Strategy(zone_mix=mix, balance_jobs=True), st), "residential")
        self.assertIsNone(choose_zone(Strategy(balance_jobs=True), dict(st, res_valve=0)))

    def test_demand_caps_match_engine_rule(self):
        self.assertEqual(demand_caps(dict(STATS, res_pop=501)), {"stadium": True, "seaport": False, "airport": False})

    def test_run_and_report(self):
        from mayor.micropolis.report import make_report
        from mayor.micropolis.run import run_mayor
        with tempfile.TemporaryDirectory() as d:
            res = run_mayor(years=2, seed=3, out_dir=d)
            self.assertEqual(len(res.months), 24)
            out = make_report(res.run_dir)
            page = Path(out["index"]).read_text(encoding="utf-8")
            self.assertIn("<title>규칙 시장의 도시 기록</title>", page)
            self.assertTrue((res.run_dir / "saves" / "final.cty").exists())
            if shutil.which("ffmpeg"):
                self.assertEqual(out["video"], "video.mp4")
            frag = Path(out["artifact"]).read_text(encoding="utf-8")
            self.assertNotIn("<body", frag)
            self.assertEqual(json.loads((res.run_dir / "summary.json").read_text())["years"], 2)

    def test_deterministic(self):
        from mayor.micropolis.run import run_mayor
        with tempfile.TemporaryDirectory() as d:
            a = run_mayor(years=3, seed=5, out_dir=Path(d) / "a").months[-1]["stats"]
            b = run_mayor(years=3, seed=5, out_dir=Path(d) / "b").months[-1]["stats"]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mayor.llm.ledger import read, record, summarize, totals
from mayor.llm.mock import MockLLM
from mayor.micropolis.lessons import format_lessons
from mayor.micropolis.review import LLMReviewer

try:
    from mayor.micropolis.engine import engine_dir
    engine_dir()
    HAVE_ENGINE = True
except Exception:
    HAVE_ENGINE = False


class LedgerTests(unittest.TestCase):
    def test_record_and_summarize(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"MAYOR_USAGE_LEDGER": f"{d}/u.jsonl"}):
            record("strategy_review", {"model": "m1", "input_tokens": 100, "output_tokens": 10, "latency_s": 2.0,
                                       "cost_usd": 0.01}, "claude-code", "run-a")
            record("reflection", {"model": "m1", "input_tokens": 50, "output_tokens": 5, "latency_s": 1.0,
                                  "cost_usd": None}, "claude-code", "run-a")
            rows = read()
            self.assertEqual(len(rows), 2)
            t = totals(rows)
            self.assertEqual((t["calls"], t["input_tokens"], t["output_tokens"], t["cost_usd"], t["cost_known_calls"]),
                             (2, 150, 15, 0.01, 1))
            self.assertEqual(set(summarize(rows, "purpose")), {"strategy_review", "reflection"})

    def test_ledger_failure_never_raises(self):
        with mock.patch.dict(os.environ, {"MAYOR_USAGE_LEDGER": "/proc/nope/u.jsonl"}):
            record("x", {}, "p")

    def test_reviewer_records_calls_and_uses_lessons(self):
        from tests.test_micropolis import ctx
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"MAYOR_USAGE_LEDGER": f"{d}/u.jsonl"}):
            llm = MockLLM(response={"strategy": {"tax_rate": 9}})
            rv = LLMReviewer(llm, lessons_text="- fixed, tax 9%: final mean 13652")
            rv.run_name = "run-x"
            rv.review(ctx())
            self.assertIn("LESSONS FROM EARLIER RUNS", llm.calls[0])
            self.assertIn("tax 9%: final mean 13652", llm.calls[0])
            self.assertEqual(read()[0]["run"], "run-x")
            LLMReviewer(MockLLM(response="garbage")).review(ctx())   # unusable reply: tokens still counted
            self.assertEqual(len(read()), 2)


class LessonsFormatTests(unittest.TestCase):
    def test_format_marks_reflections_as_hypotheses(self):
        les = {"benchmarks": [{"strategy": "fixed, tax 9%", "final_mean": 1, "final_median": 2, "peak_mean": 3,
                               "months_stalled_mean": 4.0}],
               "past_runs": [{"digest": {"seed": 1, "final_population": 10, "peak_population": 20, "peak_date": "1903-01",
                                         "drop_after_peak_pct": 50.0, "mean_tax_first_3_years": 5.0, "min_reserve": 500,
                                         "max_builds_per_month": 4, "first_month_funds_below_1000": "1902-01",
                                         "months_build_stalled_for_money": 30},
                              "reflection": {"rules_for_next_time": ["세율 9% 유지", "세율 9% 유지"]}}]}
        txt = format_lessons(les)
        self.assertIn("HYPOTHESES", txt)
        self.assertEqual(txt.count("세율 9% 유지"), 1)   # deduplicated
        self.assertLess(txt.index("Benchmarks"), txt.index("HYPOTHESES"))


class GridTests(unittest.TestCase):
    def test_grid_strategy_and_main_effects(self):
        from mayor.micropolis.lessons import grid_strategy, main_effects
        st = grid_strategy({"tax_rate": 5, "residential_share": 0.6, "reserve_funds": 1000,
                            "max_builds_per_month": 2, "land_use": "separated"})
        self.assertEqual((st.zone_mix["commercial"], st.land_use, st.tax_rate), (0.2, "separated", 5))
        rows = [{"a": 1, "b": "x", "mean_pop": 10}, {"a": 1, "b": "y", "mean_pop": 20},
                {"a": 2, "b": "x", "mean_pop": 30}, {"a": 2, "b": "y", "mean_pop": 40}]
        self.assertEqual(main_effects(rows, {"a": (1, 2), "b": ("x", "y")}),
                         {"a": {"1": 15, "2": 35}, "b": {"x": 20, "y": 30}})

    def test_format_includes_grid(self):
        row = {"tax_rate": 5, "residential_share": 0.7, "reserve_funds": 3000, "max_builds_per_month": 2,
               "land_use": "mixed", "seeds": [9], "mean_pop": 100, "mean_pop_min_seed": 90, "mean_pop_max_seed": 110,
               "final_mean": 120}
        txt = format_lessons({"grid": {"rows": [row], "metric": "20-year mean population",
                                       "main_effects": {"residential_share": {"0.7": 100}}, "best": [row], "worst": [row]}})
        self.assertIn("residential_share: 0.7 -> 100", txt)
        self.assertIn("Seed ranges are wide", txt)


@unittest.skipUnless(HAVE_ENGINE, "Micropolis engine not built")
class DigestTests(unittest.TestCase):
    def test_digest_and_reflection(self):
        from mayor.micropolis.lessons import reflect, run_digest
        from mayor.micropolis.run import run_mayor
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"MAYOR_USAGE_LEDGER": f"{d}/u.jsonl"}):
            r = run_mayor(years=3, seed=2, out_dir=d)
            dg = run_digest(r.run_dir)
            self.assertEqual((dg["seed"], dg["years"], dg["reviewer"]), (2, 3, "rules"))
            self.assertGreaterEqual(dg["peak_population"], dg["final_population"])
            llm = MockLLM(response={"mistakes": ["a"], "rules_for_next_time": ["b"]})
            ref = reflect(llm, r.run_dir, [])
            self.assertEqual((ref["mistakes"], ref["rules_for_next_time"], ref["worked"]), (["a"], ["b"], []))
            self.assertIn("MEASURED OUTCOME", llm.calls[0])
            self.assertEqual(read()[0]["purpose"], "reflection")


if __name__ == "__main__":
    unittest.main()

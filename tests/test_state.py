import unittest

from mayor.extractors.merge import carry_forward, merge_states, parse_game_date, sanity_check
from mayor.state import CONFLICT, OBSERVED, REJECTED, STALE, UNKNOWN, CityState, Field, normalize_value, parse_int


def st(**fields):
    s = CityState()
    for k, v in fields.items():
        s.set(k, v)
    return s


class ParseTests(unittest.TestCase):
    def test_parse_int(self):
        cases = {"§12,345": 12345, "-§1,200": -1200, "12.345": 12345, " 4 321 ": 4321, "Pop: 1,O24": 1024,
                 "1,2,3": None, "O": None, "abc": None, "": None, "12,34": None, 7: 7, True: None, 3.0: 3, 3.5: None}
        for text, want in cases.items():
            self.assertEqual(parse_int(text), want, text)

    def test_normalize(self):
        self.assertEqual(normalize_value("demand_residential", "High"), ("strong_positive", None))
        self.assertIsNotNone(normalize_value("demand_residential", "huge")[1])
        self.assertEqual(normalize_value("visible_problems", "fire"), (["fire"], None))
        self.assertEqual(normalize_value("funds", None), (None, None))

    def test_game_date(self):
        self.assertEqual(parse_game_date("Mar 2051"), (2051, 3))
        self.assertEqual(parse_game_date("1 January 2050"), (2050, 1))
        self.assertIsNone(parse_game_date("2050"))


class MergeTests(unittest.TestCase):
    def test_agreement_raises_confidence(self):
        a = st(funds=Field(100, OBSERVED, 0.5, "ocr:funds"))
        b = st(funds=Field(100, OBSERVED, 0.6, "llm:x"))
        m = merge_states(a, b).get("funds")
        self.assertEqual((m.status, m.value, m.confidence, m.source), (OBSERVED, 100, 0.8, "ocr:funds+llm:x"))

    def test_disagreement_is_conflict_not_a_guess(self):
        m = merge_states(st(funds=Field(100, OBSERVED, 0.9, "ocr")), st(funds=Field(700, OBSERVED, 0.6, "llm")))
        self.assertEqual(m.get("funds").status, CONFLICT)
        self.assertIsNone(m.get("funds").value)
        self.assertTrue(any("disagree" in w for w in m.warnings))

    def test_one_reader_wins_over_unknown(self):
        m = merge_states(st(), st(population=Field(5, OBSERVED, 0.6, "llm")))
        self.assertEqual(m.get("population").value, 5)

    def test_carry_forward_marks_stale(self):
        prev = st(funds=Field(100, OBSERVED, 0.9, "ocr", observed_at="t0"))
        cur = carry_forward(CityState(), prev)
        f = cur.get("funds")
        self.assertEqual((f.status, f.value, f.observed_at), (STALE, 100, "t0"))
        self.assertEqual(cur.get("population").status, UNKNOWN)

    def test_carry_does_not_override_conflict(self):
        prev = st(funds=Field(100, OBSERVED, 0.9, "ocr"))
        cur = carry_forward(st(funds=Field(status=CONFLICT)), prev)
        self.assertEqual(cur.get("funds").status, CONFLICT)

    def test_sanity(self):
        s = sanity_check(st(population=Field(-5, OBSERVED, 0.9, "ocr")), None)
        self.assertEqual(s.get("population").status, REJECTED)
        prev = st(game_date=Field("Mar 2051", OBSERVED), population=Field(10000, OBSERVED))
        cur = sanity_check(st(game_date=Field("Jan 2050", OBSERVED), population=Field(30000, OBSERVED)), prev)
        self.assertTrue(any("backwards" in w for w in cur.warnings))
        self.assertTrue(any("jumped" in w for w in cur.warnings))

    def test_roundtrip(self):
        s = st(funds=Field(1, OBSERVED, 0.5, "x"))
        s.warnings.append("w")
        self.assertEqual(CityState.from_dict(s.to_dict()).to_dict(), s.to_dict())


if __name__ == "__main__":
    unittest.main()

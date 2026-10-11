"""The transport-company CEO: code lists concrete options, the CEO (LLM or rules) picks, code builds.

Every review (a few game months apart) the CEO sees the company's money, its bus lines with their
profit and waiting passengers, and candidate new lines that code has already routed and priced.
It answers with a few actions; the executor checks them against the money and builds them.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .game import Game, plan_road

ROAD_TILE_COST = 172       # measured: 5 new road tiles cost £856 in the first line (1950, temperate, default costs)
LINE_FIXED_COST = 1500     # two drive-through stops + depot, rounded up from the first line
BUS_COST = 4800            # measured: 3 MPS Regal buses + stops + depot cost £15,524
RESERVE = 5000             # never plan to spend the last £5,000


@dataclass
class Line:
    id: int
    a: str
    b: str
    a_xy: tuple
    b_xy: tuple
    stops: list
    depot: tuple
    vehicles: list = field(default_factory=list)
    built: str = ""
    cost: int = 0
    mid: tuple = ()            # middle tile of the road path (camera target)
    span: int = 0              # tiles between the two ends


def _dist(p, q):
    return abs(p["x"] - q["x"]) + abs(p["y"] - q["y"])


class Executor:
    """Turns chosen actions into game commands and remembers the company's lines."""

    def __init__(self, game: Game, run_dir: Path):
        self.g = game
        self.run_dir = run_dir
        self.lines: list[Line] = []
        self.events: list[dict] = []

    def log(self, date: str, text: str, **extra):
        self.events.append({"date": date, "text": text, **extra})
        self.events = self.events[-60:]

    # --------------------------------------------------------- candidates --
    def candidates(self, towns: list[dict], k: int = 6) -> list[dict]:
        served = {frozenset((ln.a, ln.b)) for ln in self.lines}
        big = [t for t in towns if t["pop"] >= 250]
        pairs = []
        for i, p in enumerate(big):
            for q in big[i + 1:]:
                d = _dist(p, q)
                if 12 <= d <= 60 and frozenset((p["name"], q["name"])) not in served:
                    # passengers grow with both towns and (up to a point) with distance; long roads cost more
                    score = math.sqrt(p["pop"] * q["pop"]) * min(d, 40) / (d + 15)
                    pairs.append((score, p, q, d))
        pairs.sort(key=lambda z: -z[0])
        out = []
        for score, p, q, d in pairs[:k * 2]:
            x0, y0 = max(1, min(p["x"], q["x"]) - 8), max(1, min(p["y"], q["y"]) - 8)
            w, h = min(254 - x0, abs(p["x"] - q["x"]) + 17), min(254 - y0, abs(p["y"] - q["y"]) + 17)
            tiles = self.g.map_rect(x0, y0, w, h)
            path = plan_road(tiles, (p["x"], p["y"]), (q["x"], q["y"]))
            if not path:
                continue
            new = sum(tiles.get(t) != "R" for t in path)
            out.append({"id": len(out) + 1, "a": p["name"], "b": q["name"], "a_pop": p["pop"], "b_pop": q["pop"],
                        "a_xy": (p["x"], p["y"]), "b_xy": (q["x"], q["y"]), "distance": d,
                        "road_tiles": len(path), "new_road_tiles": new,
                        "est_cost_2_buses": ROAD_TILE_COST * new + LINE_FIXED_COST + 2 * BUS_COST,
                        "_path": path})
            if len(out) >= k:
                break
        return out

    # ------------------------------------------------------------- actions --
    def new_line(self, cand: dict, buses: int, date: str) -> dict:
        before = self.g.company_info()["money"]
        road = self.g.build_path([tuple(t) for t in cand["_path"]])
        sa, sb = self.g.bus_stop(*cand["a_xy"]), self.g.bus_stop(*cand["b_xy"])
        dp = self.g.depot(*cand["a_xy"]) if sa.get("ok") else {"ok": False}
        if not (sa.get("ok") and sb.get("ok") and dp.get("ok")):
            err = sa.get("error") or sb.get("error") or dp.get("error") or "stop/depot failed"
            self.log(date, f"노선 실패: {cand['a']}–{cand['b']} ({err})", kind="fail")
            return {"ok": False, "error": err, "road": road}
        bus = self.g.buy_bus((dp["x"], dp["y"]), [(sa["x"], sa["y"]), (sb["x"], sb["y"])], buses)
        after = self.g.company_info()["money"]
        line = Line(len(self.lines) + 1, cand["a"], cand["b"], tuple(cand["a_xy"]), tuple(cand["b_xy"]),
                    [(sa["x"], sa["y"]), (sb["x"], sb["y"])], (dp["x"], dp["y"]), list(bus.get("bought", [])),
                    date, before - after, tuple(cand["_path"][len(cand["_path"]) // 2]),
                    max(abs(cand["a_xy"][0] - cand["b_xy"][0]), abs(cand["a_xy"][1] - cand["b_xy"][1])))
        self.lines.append(line)
        self.log(date, f"새 노선 {line.id}: {line.a}–{line.b}, 버스 {len(line.vehicles)}대, £{line.cost:,}",
                 kind="build", line=line.id)
        return {"ok": bus.get("ok", False), "line": line.id, "cost": line.cost, "road_failed": road["failed"][:3]}

    def add_buses(self, line_id: int, n: int, date: str) -> dict:
        line = next((ln for ln in self.lines if ln.id == line_id), None)
        if line is None:
            return {"ok": False, "error": f"no line {line_id}"}
        before = self.g.company_info()["money"]
        r = self.g.buy_bus(line.depot, line.stops, n)
        line.vehicles += r.get("bought", [])
        cost = before - self.g.company_info()["money"]
        self.log(date, f"노선 {line.id} 버스 +{len(r.get('bought', []))}대 (£{cost:,})", kind="buses", line=line.id)
        return {"ok": r.get("ok", False), "bought": len(r.get("bought", [])), "cost": cost}

    def set_loan(self, amount: int, date: str) -> dict:
        r = self.g.set_loan(amount)
        self.log(date, f"대출 {'조정' if r.get('ok') else '실패'}: £{r.get('loan', 0):,}", kind="loan")
        return r

    def save(self):
        (self.run_dir / "lines.json").write_text(json.dumps([asdict(x) for x in self.lines], ensure_ascii=False),
                                                 encoding="utf-8")


# ------------------------------------------------------------------ observe --
def observe(ex: Executor) -> dict:
    g = ex.g
    info = g.company_info()
    vehicles = {v["id"]: v for v in g.vehicles()}
    stations = {(s["x"], s["y"]): s for s in g.stations()}
    lines = []
    for ln in ex.lines:
        vs = [vehicles[v] for v in ln.vehicles if v in vehicles]
        stops = [stations.get(tuple(s), {}) for s in ln.stops]
        lines.append({"id": ln.id, "towns": f"{ln.a} – {ln.b}", "buses": len(vs), "built": ln.built, "cost": ln.cost,
                      "profit_this_year": sum(v["profit"] for v in vs), "profit_last_year": sum(v["last"] for v in vs),
                      "waiting": [s.get("waiting", 0) for s in stops], "rating": [s.get("rating", -1) for s in stops]})
    return {"date": info["date"], "money": info["money"], "loan": info["loan"], "max_loan": info["max_loan"],
            "value": info.get("value"), "vehicles": len(vehicles), "lines": lines}


# --------------------------------------------------------------------- CEOs --
PROMPT = """You run "Claude Transport", a bus company in OpenTTD 15.3 (temperate, started 1950).
Every review you choose a few ACTIONS. Plain code then builds exactly what you chose, using the game's own costs.

Facts measured in this game (not assumptions):
- New road costs about £{road} per tile; two stops + a depot about £{fixed}; a bus (MPS Regal, 31 passengers) about £{bus}.
- Buses earn per passenger delivered; income grows with distance and falls with travel time. A line's
  profit_this_year is the sum of its buses' profits since 1 January (it resets every year).
- Passengers waiting at a stop and a low station rating (0-100) mean the line needs more buses;
  very few waiting with many buses means it has too many.
- The company starts with £100,000 of which £100,000 is a loan; max loan is shown; interest is charged on the loan.
- Candidate lines below were routed by code over existing roads and buildable land (no bridges yet);
  new_road_tiles is what would be built. Lines cannot be removed yet.

NOW ({date}): money £{money:,}, loan £{loan:,} of max £{max_loan:,}, vehicles {vehicles}.
LINES: {lines}
CANDIDATE NEW LINES: {cands}
YOUR PAST REVIEWS AND WHAT HAPPENED: {past}

Actions (at most 3): {{"type":"new_line","candidate":id,"buses":1-4}}, {{"type":"add_buses","line":id,"n":1-3}},
{{"type":"set_loan","amount":int}}. An empty list means wait. Never plan to go below £{reserve:,}.
Return ONE JSON object, no prose:
{{"actions":[...], "reasoning":["short points in Korean citing the numbers above"],
 "expected":{{"money_next_review":"up|down|flat","total_profit_next_review":"up|down|flat"}},
 "uncertainties":["Korean"]}}"""


def _cands_for_prompt(cands):
    return json.dumps([{k: v for k, v in c.items() if not k.startswith("_") and k not in ("a_xy", "b_xy")}
                       for c in cands], ensure_ascii=False)


class RuleCEO:
    name = "rules"

    def decide(self, obs: dict, cands: list[dict], past: list[dict]) -> dict:
        acts = []
        budget = obs["money"] - RESERVE
        for ln in obs["lines"]:
            if max(ln["waiting"] or [0]) > 60 and budget > BUS_COST:
                acts.append({"type": "add_buses", "line": ln["id"], "n": 1})
                budget -= BUS_COST
        if cands and budget > cands[0]["est_cost_2_buses"] * 1.2 and len(acts) < 3:
            acts.append({"type": "new_line", "candidate": cands[0]["id"], "buses": 2})
        return {"actions": acts, "reasoning": ["규칙: 대기 승객 60명 초과 노선에 버스 추가, 돈이 충분하면 최상위 후보 건설"],
                "expected": {}, "uncertainties": [], "source": "rules"}


class LLMCEO:
    def __init__(self, llm, fallback=None, run_name: str | None = None):
        self.llm = llm
        self.fallback = fallback or RuleCEO()
        self.name = f"llm:{llm.name}/{llm.model}"
        self.run_name = run_name

    def prompt(self, obs, cands, past):
        return PROMPT.format(road=ROAD_TILE_COST, fixed=LINE_FIXED_COST, bus=BUS_COST, reserve=RESERVE,
                             date=obs["date"], money=obs["money"], loan=obs["loan"], max_loan=obs["max_loan"],
                             vehicles=obs["vehicles"], lines=json.dumps(obs["lines"], ensure_ascii=False) or "[]",
                             cands=_cands_for_prompt(cands), past=json.dumps(past[-3:], ensure_ascii=False) or "[]")

    def decide(self, obs, cands, past) -> dict:
        from ..extractors.vision import extract_json
        from ..llm import LLMError
        from ..llm.ledger import record
        try:
            res = self.llm.ask(self.prompt(obs, cands, past), None, max_tokens=1200)
            record("ceo_review", res.usage(), self.llm.name, self.run_name)
            data = extract_json(res.text)
            if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
                raise ValueError("no 'actions' list in reply")
            data["source"] = self.name
            data["usage"] = res.usage()
            return data
        except (LLMError, ValueError) as e:
            d = self.fallback.decide(obs, cands, past)
            d["source"] = "rules (llm failed)"
            d["uncertainties"] = [f"LLM 실패: {str(e)[:160]}"]
            return d


def execute(ex: Executor, decision: dict, obs: dict, cands: list[dict]) -> list[dict]:
    """Check each action against the money and build it. Returns one result per action."""
    results, money = [], obs["money"]
    headroom = obs["max_loan"] - obs["loan"]
    by_id = {c["id"]: c for c in cands}
    for act in decision.get("actions", [])[:3]:
        t = act.get("type")
        try:
            if t == "set_loan":
                amount = max(0, min(int(act.get("amount", obs["loan"])), obs["max_loan"]))
                r = ex.set_loan(amount, obs["date"])
                money = r.get("money", money)
            elif t == "new_line":
                c = by_id.get(int(act.get("candidate", -1)))
                buses = max(1, min(4, int(act.get("buses", 2))))
                if c is None:
                    r = {"ok": False, "error": "unknown candidate"}
                else:
                    est = ROAD_TILE_COST * c["new_road_tiles"] + LINE_FIXED_COST + buses * BUS_COST
                    r = (ex.new_line(c, buses, obs["date"]) if money - est >= RESERVE
                         else {"ok": False, "error": f"needs about £{est:,}, would go below reserve"})
                    money -= r.get("cost", 0)
            elif t == "add_buses":
                n = max(1, min(3, int(act.get("n", 1))))
                r = (ex.add_buses(int(act.get("line", -1)), n, obs["date"]) if money - n * BUS_COST >= RESERVE
                     else {"ok": False, "error": "would go below reserve"})
                money -= r.get("cost", 0)
            else:
                r = {"ok": False, "error": f"unknown action {t!r}"}
        except (TypeError, ValueError) as e:
            r = {"ok": False, "error": f"bad action: {e}"}
        results.append({"action": act, **{k: v for k, v in r.items() if k in ("ok", "error", "cost", "line", "bought")}})
    ex.save()
    return results


def direction(before: float, after: float, band: float = 0.03) -> str:
    if before == 0:
        return "flat" if after == 0 else ("up" if after > 0 else "down")
    ch = (after - before) / abs(before)
    return "up" if ch > band else "down" if ch < -band else "flat"

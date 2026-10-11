"""Talk to the LLMBridge GameScript: requests with ids, multi-part replies, and a road planner.

Every build goes through the GameScript acting as one company, so the game's own rules
and costs apply. Tile codes from the bridge's "map" op:
W water, R road, S road stop, D depot, B buildable flat, b buildable slope, X anything else.
"""

from __future__ import annotations

import heapq
import itertools
import time

from .admin import Admin

COST = {"R": 1.0, "B": 3.0, "b": 5.0}     # reuse roads; new road on slopes costs more
PASSABLE_END = set(COST) | {"S"}


class BridgeError(RuntimeError):
    pass


class Game:
    def __init__(self, admin: Admin, company: int = 0):
        self.a = admin
        self.company = company
        self._ids = itertools.count(1)

    def call(self, op: str, timeout: float = 120.0, **args) -> dict:
        """One request, one reply (lists are gathered from their parts into 'parts')."""
        rid = next(self._ids)
        self.a.to_gs({"id": rid, "op": op, **args})
        parts, end = [], time.time() + timeout
        while time.time() < end:
            m = self.a.from_gs(timeout=max(0.1, end - time.time()))
            if m is None or m.get("re") != rid:
                continue
            if "part" in m:
                parts.append(m)
                continue
            if m.get("done"):
                return {"ok": True, "parts": parts}
            return m
        raise BridgeError(f"{op}: no answer within {timeout:.0f}s")

    # ------------------------------------------------------------ queries --
    def towns(self) -> list[dict]:
        return [t for p in self.call("towns")["parts"] for t in p["towns"]]

    def vehicles(self) -> list[dict]:
        return [v for p in self.call("vehicles", company=self.company)["parts"] for v in p["vehicles"]]

    def company_info(self) -> dict:
        return self.call("company", company=self.company)

    def stations(self) -> list[dict]:
        return [s for p in self.call("stations", company=self.company)["parts"] for s in p["stations"]]

    def map_rect(self, x0: int, y0: int, w: int, h: int) -> dict[tuple[int, int], str]:
        rows = self.call("map", timeout=600, x0=x0, y0=y0, w=w, h=h)["parts"]
        return {(x0 + i, r["y"]): c for r in rows for i, c in enumerate(r["row"])}

    # ------------------------------------------------------------ actions --
    def build_path(self, path: list[tuple[int, int]]) -> dict:
        out = {"ok": True, "failed": []}
        for i in range(0, len(path), 60):          # keep each message well under 1450 bytes
            chunk = [list(p) for p in path[max(0, i - 1): i + 60]]
            r = self.call("build_path", company=self.company, path=chunk)
            out["ok"] &= r.get("ok", False)
            out["failed"] += r.get("failed", [])
            out["money"] = r.get("money")
        return out

    def bus_stop(self, x: int, y: int) -> dict:
        return self.call("bus_stop", company=self.company, x=x, y=y)

    def depot(self, x: int, y: int) -> dict:
        return self.call("depot", company=self.company, x=x, y=y)

    def set_loan(self, amount: int) -> dict:
        """Loan goes in steps of 10,000 (GetLoanInterval in a default game)."""
        return self.call("loan", company=self.company, amount=int(round(amount / 10000)) * 10000)

    def buy_bus(self, depot: tuple[int, int], stops: list[tuple[int, int]], count: int) -> dict:
        return self.call("buy_bus", company=self.company, depot=list(depot), stops=[list(s) for s in stops], count=count)


def plan_road(tiles: dict[tuple[int, int], str], start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]] | None:
    """Cheapest 4-connected path over road/buildable tiles (A*). No bridges or tunnels yet."""
    def h(p):
        return abs(p[0] - goal[0]) + abs(p[1] - goal[1])
    best = {start: 0.0}
    prev: dict = {}
    heap = [(h(start), 0.0, start)]
    while heap:
        _, g, p = heapq.heappop(heap)
        if p == goal:
            path = [p]
            while p in prev:
                p = prev[p]
                path.append(p)
            return path[::-1]
        if g > best.get(p, float("inf")):
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (p[0] + dx, p[1] + dy)
            code = tiles.get(q)
            if code is None or (code not in COST and q != goal):
                continue
            ng = g + COST.get(code, 1.0)
            if ng < best.get(q, float("inf")):
                best[q], prev[q] = ng, p
                heapq.heappush(heap, (ng + h(q), ng, q))
    return None

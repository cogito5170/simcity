"""Master-plan layout: turns "build a residential zone" into exact, verified tool calls.

Layout (tested in the engine, see docs/MICROPOLIS.md):

      plants (4x4, touching each other)
          │ power spine (wire) at x = OX-1
    ══════╪══════════════╤══════════  road  y = OY + 7p
    ║     │[Z][Z][Z][Z]..│[Z][Z]..     zone row (3x3), touches road above
    ║     │[Z][Z][Z][Z]..│[Z][Z]..     zone row, touches road below
    ══════╪══════════════╧══════════  road  y = OY + 7p + 7
    ║ vertical road x = OX-2;  │ connector road every 6 slots, wired across

Zones pass power to touching zones; roads do not, hence the wire spine and the
wire crossings on connector roads. Every zone touches a road.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OX, OY = 14, 14          # first slot's left/top corner
PERIOD = 7               # road, 3 zone rows... road
CONN_EVERY = 6           # a connector road column after every 6 slots
BLOCK_COLS = 12          # columns filled per pair of rows before moving down
MAX_PAIRS = 11
IND_PAIR_START = 7       # separated land use: row pairs 7.. are the industrial district
ROW_CENTERS = (2, 5)     # zone-row centre offsets inside a pair
PLANT_SLOTS = [(OX - 3 - 4 * k, OY - 5 - 4 * m) for m in range(2) for k in range(3)]
# Special buildings stacked under the first plant, each touching the one above (power flows through).
SPECIAL_SLOTS = {"stadium": (OX - 5, OY - 1), "seaport": (OX - 5, OY + 3), "airport": (OX - 7, OY + 7)}
SPECIAL_FOOT = {"stadium": (OX - 6, OY - 2, OX - 3, OY + 1), "seaport": (OX - 6, OY + 2, OX - 3, OY + 5),
                "airport": (OX - 8, OY + 6, OX - 3, OY + 11)}   # x0, y0, x1, y1 inclusive (measured)
BRIDGE_X = OX - 3   # wire column linking the first plant to a special whose neighbour is missing

ZONE_KINDS = ("residential", "commercial", "industrial")
SERVICE_KINDS = ("police", "fire")


@dataclass
class ActionResult:
    action: str
    ok: bool
    cost: int = 0
    detail: str = ""
    where: tuple[int, int] | None = None
    skipped: bool = False   # deliberately not attempted (e.g. would break the reserve); not an error

    @property
    def status(self) -> str:
        return "done" if self.ok else "skipped" if self.skipped else "failed"

    def to_dict(self) -> dict:
        return {"action": self.action, "status": self.status, "ok": self.ok, "cost": self.cost,
                "detail": self.detail, "where": list(self.where) if self.where else None}


WORLD_W, WORLD_H = 120, 100
WATER = (2, 20)          # tile ids RIVER..LASTRIVEDGE: roads and zones cannot be placed there


def slot_x(j: int, conn_every: int = CONN_EVERY, ox: int = OX) -> int:
    return ox + 3 * j + (j // conn_every)


def max_pairs(oy: int = OY) -> int:
    """Row pairs whose bottom road still fits on the map."""
    return max(0, min(MAX_PAIRS, (WORLD_H - 2 - oy) // PERIOD))


def slot_order(max_cols: int = 33, conn_every: int = CONN_EVERY, ox: int = OX, oy: int = OY):
    """Fill compactly: 12 columns of every row pair, then the next 12 columns."""
    for b0 in range(0, max_cols, BLOCK_COLS):
        for p in range(max_pairs(oy)):
            for j in range(b0, min(b0 + BLOCK_COLS, max_cols)):
                if slot_x(j, conn_every, ox) + 2 > WORLD_W - 3:
                    continue
                for r in ROW_CENTERS:
                    yield (p, j, r)


def choose_origin(tiles, pairs: int = 4, cols: int = 12) -> tuple[int, int, int]:
    """Origin (ox, oy) whose plant area and first `pairs` x `cols` block hold the least water.

    Returns (ox, oy, water_tiles). On a blank map this is the default (OX, OY)."""
    import numpy as np
    t = np.asarray(tiles)
    water = ((t >= WATER[0]) & (t <= WATER[1])).astype(np.int32)
    trees = ((t >= 21) & (t <= 43)).astype(np.int32)
    integ = water.cumsum(0).cumsum(1)
    tinteg = trees.cumsum(0).cumsum(1)

    def box(a, x0, y0, x1, y1):   # inclusive sum via the integral image
        v = a[y1, x1]
        if x0 > 0:
            v -= a[y1, x0 - 1]
        if y0 > 0:
            v -= a[y0 - 1, x1]
        if x0 > 0 and y0 > 0:
            v += a[y0 - 1, x0 - 1]
        return int(v)

    best = None
    width = 3 * cols + cols // CONN_EVERY + 2
    for oy in range(12, WORLD_H - PERIOD * pairs - 2):
        for ox in range(13, WORLD_W - width - 3):
            # hard rule: plant slot 0 (x ox-4..ox-1, y oy-6..oy-3) and the spine top must be dry, otherwise
            # no plant can ever reach the grid (the other plant slots connect only through slot 0)
            if box(integ, ox - 4, oy - 6, ox - 1, oy - 1):
                continue
            x0, y0, x1, y1 = ox - 12, oy - 11, ox + width, oy + PERIOD * pairs
            score = (box(integ, x0, y0, x1, y1), box(tinteg, x0, y0, x1, y1), abs(ox - OX) + abs(oy - OY))
            if best is None or score < best[0]:
                best = (score, ox, oy)
    if best is None:
        raise ValueError("no origin with a dry first plant slot")
    return best[1], best[2], best[0][0]


@dataclass
class Planner:
    city: object
    reserve: int = 0
    used_slots: dict = field(default_factory=dict)   # (p, j, r) -> kind
    roads: set = field(default_factory=set)
    wires: set = field(default_factory=set)
    plants: list = field(default_factory=list)       # (x, y, kind)
    blocked: set = field(default_factory=set)
    districts: bool = False                          # separate industrial district (see next_slot)
    conn_every: int = CONN_EVERY                     # connector road after every N slots; fixed once building starts
    ox: int = OX                                     # layout origin (see choose_origin for terrain maps)
    oy: int = OY

    def __post_init__(self):
        ox, oy = self.ox, self.oy
        self.plant_slots = [(ox - 3 - 4 * k, oy - 5 - 4 * m) for m in range(2) for k in range(3)]
        self.special_slots = {"stadium": (ox - 5, oy - 1), "seaport": (ox - 5, oy + 3), "airport": (ox - 7, oy + 7)}
        self.special_foot = {"stadium": (ox - 6, oy - 2, ox - 3, oy + 1), "seaport": (ox - 6, oy + 2, ox - 3, oy + 5),
                             "airport": (ox - 8, oy + 6, ox - 3, oy + 11)}
        self.bridge_x = ox - 3
        self.destroyed: list = []                     # (date-less) log of zones lost to disasters, for stats
        self.T = self.city.Tool
        self.cost_of = {self.T.ROAD: 10, self.T.WIRE: 5, self.T.RESIDENTIAL: 100, self.T.COMMERCIAL: 100,
                        self.T.INDUSTRIAL: 100, self.T.POLICE_STATION: 500, self.T.FIRE_STATION: 500,
                        self.T.COAL_POWER: 3000, self.T.NUCLEAR_POWER: 5000, self.T.STADIUM: 5000,
                        self.T.SEAPORT: 3000, self.T.AIRPORT: 10000, self.T.BULLDOZER: 1}
        self.special_tool = {"stadium": self.T.STADIUM, "seaport": self.T.SEAPORT, "airport": self.T.AIRPORT}
        self.specials: dict = {}
        self.kind_tool = {"residential": self.T.RESIDENTIAL, "commercial": self.T.COMMERCIAL,
                          "industrial": self.T.INDUSTRIAL, "police": self.T.POLICE_STATION,
                          "fire": self.T.FIRE_STATION}

    # ------------------------------------------------------------ queries --
    def count(self, kind: str) -> int:
        return sum(1 for k in self.used_slots.values() if k == kind)

    def zones(self) -> int:
        return sum(1 for k in self.used_slots.values() if k in ZONE_KINDS)

    def next_slot(self, kind: str | None = None):
        """Next free slot. With districts on, industry fills the bottom row pairs (from IND_PAIR_START)
        and everything else the top ones, so housing sits ~50 tiles from factory pollution.
        Each district still starts at column 0, next to the power spine: zones pass power only
        to touching zones, so a district must grow outward from the spine without gaps."""
        def free(s):
            return s not in self.used_slots and s not in self.blocked
        if self.districts and kind is not None:
            want_ind = kind == "industrial"
            for s in slot_order(conn_every=self.conn_every, ox=self.ox, oy=self.oy):
                if free(s) and (s[0] >= IND_PAIR_START) == want_ind:
                    return s
        for s in slot_order(conn_every=self.conn_every, ox=self.ox, oy=self.oy):
            if free(s):
                return s
        return None

    # ----------------------------------------------------- infrastructure --
    def _infra_for(self, p: int, j: int) -> list[tuple]:
        """Tool calls (tool, x, y) still missing so that slot (p, j) has road + power."""
        T = self.T
        ox, oy = self.ox, self.oy
        y_top, y_bot = oy + PERIOD * p, oy + PERIOD * (p + 1)
        x_end = slot_x(j, self.conn_every, ox) + 2
        roads, wires = [], []
        for y in (y_top, y_bot):
            for x in range(ox - 2, x_end + 1):
                if (x, y) not in self.roads:
                    roads.append((T.ROAD, x, y))
        for y in range(oy, y_bot + 1):                       # vertical road
            if (ox - 2, y) not in self.roads:
                roads.append((T.ROAD, ox - 2, y))
        for c in range(self.conn_every, j + 1, self.conn_every):   # connector columns up to j
            cx = slot_x(c, self.conn_every, ox) - 1
            for y in range(y_top, y_bot + 1):
                if (cx, y) not in self.roads:
                    roads.append((T.ROAD, cx, y))
            for r in ROW_CENTERS:
                if (cx, y_top + r) not in self.wires:
                    wires.append((T.WIRE, cx, y_top + r))
        for y in range(oy - 2, y_bot):                       # power spine (crosses the roads)
            if (ox - 1, y) not in self.wires:
                wires.append((T.WIRE, ox - 1, y))
        # dedupe while keeping order: roads first, then wires over them
        seen, out = set(), []
        for c in roads + wires:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def _run(self, calls: list[tuple]) -> tuple[bool, int, str]:
        spent, failures = 0, []
        for tool, x, y in calls:
            before = self.city.stats()["funds"]
            res = self.city.tool(tool, x, y)
            spent += int(before - self.city.stats()["funds"])
            if res == self.city.OK:
                if tool == self.T.BULLDOZER:
                    self.wires.discard((x, y))
                (self.roads if tool == self.T.ROAD else self.wires if tool == self.T.WIRE else set()).add((x, y))
            else:
                failures.append(f"{tool.name}@{x},{y}={res.name}")
        return not failures, spent, "; ".join(failures[:5])

    def _affordable(self, calls: list[tuple]) -> tuple[bool, int]:
        need = sum(self.cost_of.get(t, 0) for t, _, _ in calls)
        return self.city.stats()["funds"] - need >= self.reserve, need

    # ------------------------------------------------------------ actions --
    def place(self, kind: str) -> ActionResult:
        """Put a zone or service building into the next free slot, with roads and power."""
        if kind not in self.kind_tool:
            return ActionResult(f"place:{kind}", False, detail="unknown kind")
        tiles = self.city.tiles()
        for _ in range(200):                                  # skip slots that would touch water
            slot = self.next_slot(kind)
            if slot is None:
                return ActionResult(f"place:{kind}", False, detail="map full")
            p, j, r = slot
            cx, cy = slot_x(j, self.conn_every, self.ox) + 1, self.oy + PERIOD * p + r
            calls = self._infra_for(p, j) + [(self.kind_tool[kind], cx, cy)]
            cells = [(x, y) for _, x, y in calls[:-1]] + [(cx + dx, cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            if any(not (0 <= x < WORLD_W and 0 <= y < WORLD_H) or WATER[0] <= tiles[y, x] <= WATER[1]
                   for x, y in cells):
                self.blocked.add(slot)
                continue
            break
        else:
            return ActionResult(f"place:{kind}", False, detail="no dry slot found")
        ok, need = self._affordable(calls)
        if not ok:
            return ActionResult(f"place:{kind}", False, detail=f"needs {need}, keeping reserve {self.reserve}",
                                skipped=True)
        ok, spent, why = self._run(calls)
        if ok:
            self.used_slots[slot] = kind
        else:
            self.blocked.add(slot)
        return ActionResult(f"place:{kind}", ok, spent, why or f"slot p{p} c{j} r{r}", (cx, cy))

    def build_power(self, kind: str = "coal") -> ActionResult:
        tool = self.T.NUCLEAR_POWER if kind == "nuclear" else self.T.COAL_POWER
        tiles = self.city.tiles()
        taken = {(x, y) for x, y, _ in self.plants}
        first = self.plant_slots[0]

        def connected(x, y):   # power reaches a plant only through slot 0 or a touching plant
            return (x, y) == first or any(abs(x - px) + abs(y - py) == 4 for px, py in taken)
        free = [(x, y) for x, y in self.plant_slots if (x, y) not in taken and connected(x, y) and not any(
            WATER[0] <= tiles[yy, xx] <= WATER[1] for xx in range(x - 1, x + 3) for yy in range(y - 1, y + 3))]
        if first not in taken and first not in free:
            free = []
        elif first not in taken:
            free = [first]   # the first plant must be slot 0 (it touches the spine)
        if not free:
            return ActionResult(f"power:{kind}", False, detail="no dry plant slot connected to the grid")
        x, y = free[0]
        calls = [(tool, x, y)]
        if not self.plants:   # first plant also needs the top of the spine
            calls += [(self.T.WIRE, self.ox - 1, yy) for yy in range(self.oy - 2, self.oy)
                      if (self.ox - 1, yy) not in self.wires]
        ok, need = self._affordable(calls)
        if not ok:
            return ActionResult(f"power:{kind}", False, detail=f"needs {need}, keeping reserve {self.reserve}",
                                skipped=True)
        ok, spent, why = self._run(calls)
        if ok:
            self.plants.append((x, y, kind))
        return ActionResult(f"power:{kind}", ok, spent, why or f"plant #{len(self.plants)}", (x, y))

    def build_special(self, kind: str) -> ActionResult:
        """Stadium / seaport / airport in its fixed slot (needs the first power plant above it)."""
        if kind not in self.special_slots:
            return ActionResult(f"special:{kind}", False, detail="unknown kind")
        if kind in self.specials:
            return ActionResult(f"special:{kind}", False, detail="already built", skipped=True)
        if not self.plants:
            return ActionResult(f"special:{kind}", False, skipped=True, detail="needs a power plant first")
        x, y = self.special_slots[kind]
        x0, y0, x1, y1 = self.special_foot[kind]
        # clear our own bridge wires inside the footprint, then bridge the gap up to the first plant
        calls = [(self.T.BULLDOZER, wx, wy) for wx, wy in sorted(self.wires) if x0 <= wx <= x1 and y0 <= wy <= y1]
        built = [self.special_foot[k] for k in self.specials]
        bx = self.bridge_x
        for wy in range(self.oy - 2, y0):
            inside = any(a <= bx <= c and b <= wy <= d for a, b, c, d in built)
            if not inside and (bx, wy) not in self.wires:
                calls.append((self.T.WIRE, bx, wy))
        calls.append((self.special_tool[kind], x, y))
        ok, need = self._affordable(calls)
        if not ok:
            return ActionResult(f"special:{kind}", False, detail=f"needs {need}, keeping reserve {self.reserve}",
                                skipped=True)
        ok, spent, why = self._run(calls)
        if ok:
            self.specials[kind] = (x, y)
        return ActionResult(f"special:{kind}", ok, spent, why or kind, (x, y))

    def set_road_spacing(self, every: int) -> bool:
        """Choose connector-road spacing (3-6 slots). Only before the first zone: the grid is fixed after."""
        every = max(3, min(6, int(every)))
        if self.used_slots or every == self.conn_every:
            return every == self.conn_every
        self.conn_every = every
        return True

    # ------------------------------------------------------- disasters --
    def audit(self) -> dict:
        """Compare what we built with the map (fires, floods, tornadoes destroy things).

        Lost zones/buildings free their slot; lost road/wire tiles leave our records so the
        next repair or placement rebuilds them. Returns counts of what was found missing."""
        mv = self.city.e.map_values
        lo = mv & 0x03FF
        zone_center = (mv & 0x0400) != 0
        lost_slots = []
        for (p, j, r), kind in list(self.used_slots.items()):
            cx, cy = slot_x(j, self.conn_every, self.ox) + 1, self.oy + PERIOD * p + r
            if not zone_center[cy, cx]:
                lost_slots.append(((p, j, r), kind))
                del self.used_slots[(p, j, r)]
        lost_roads = [(x, y) for x, y in self.roads if not (64 <= lo[y, x] <= 206)]
        # a wire survives as a power line (208-222) or as a road tile that conducts (road/power crossing;
        # its id changes as traffic animates, e.g. 77 -> 93, so check the CONDBIT flag, not the id)
        conducts = (mv & 0x4000) != 0
        lost_wires = [(x, y) for x, y in self.wires
                      if not (208 <= lo[y, x] <= 222 or (64 <= lo[y, x] <= 206 and conducts[y, x]))]
        for c in lost_roads:
            self.roads.discard(c)
        for c in lost_wires:
            self.wires.discard(c)
        lost_plants = [pl for pl in self.plants if not zone_center[pl[1], pl[0]]]
        for pl in lost_plants:
            self.plants.remove(pl)
        lost_specials = [k for k, (x, y) in self.specials.items() if not zone_center[y, x]]
        for k in lost_specials:
            del self.specials[k]
        self.destroyed += [kind for _, kind in lost_slots]
        return {"zones_lost": len(lost_slots), "lost_kinds": [k for _, k in lost_slots], "roads_lost": len(lost_roads),
                "wires_lost": len(lost_wires), "plants_lost": len(lost_plants), "specials_lost": lost_specials}

    def needs_repair(self) -> bool:
        pairs = {}
        for p, j, _ in self.used_slots:
            pairs[p] = max(pairs.get(p, 0), j)
        return any(self._infra_for(p, j) for p, j in pairs.items())

    def repair(self) -> ActionResult:
        """Rebuild missing roads/wires that serve existing slots (one row pair per call)."""
        pairs = {}
        for p, j, _ in self.used_slots:
            pairs[p] = max(pairs.get(p, 0), j)
        for p, j in sorted(pairs.items()):
            calls = self._infra_for(p, j)
            if calls:
                ok, need = self._affordable(calls)
                if not ok:
                    return ActionResult("repair", False, detail=f"needs {need}, keeping reserve {self.reserve}",
                                        skipped=True)
                ok, spent, why = self._run(calls)
                return ActionResult("repair", ok, spent, why or f"row pair {p}: {len(calls)} road/wire tiles")
        return ActionResult("repair", False, detail="nothing to repair", skipped=True)

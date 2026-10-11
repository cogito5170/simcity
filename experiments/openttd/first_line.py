"""First verified play: connect the two closest towns (400+ people, 15-45 tiles apart) with a bus line.

    python experiments/openttd/first_line.py        # server running, IdleAI company 0 present
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mayor.openttd.admin import Admin  # noqa: E402
from mayor.openttd.game import Game, plan_road  # noqa: E402


def main():
    a = Admin(password="mayor", name="first-line")
    g = Game(a, company=0)
    big = [t for t in g.towns() if t["pop"] >= 400]
    pairs = sorted((abs(p["x"] - q["x"]) + abs(p["y"] - q["y"]), i, j)
                   for i, p in enumerate(big) for j, q in enumerate(big) if i < j)
    d, i, j = next(z for z in pairs if 15 <= z[0] <= 45)
    A, B = big[i], big[j]
    x0, y0 = max(1, min(A["x"], B["x"]) - 8), max(1, min(A["y"], B["y"]) - 8)
    tiles = g.map_rect(x0, y0, abs(A["x"] - B["x"]) + 17, abs(A["y"] - B["y"]) + 17)
    path = plan_road(tiles, (A["x"], A["y"]), (B["x"], B["y"]))
    print(f"{A['name']} ({A['pop']}) -> {B['name']} ({B['pop']}), {d} tiles, road path {len(path)} "
          f"({sum(tiles[p] != 'R' for p in path)} new)")
    print("road", g.build_path(path))
    sa, sb, dp = g.bus_stop(A["x"], A["y"]), g.bus_stop(B["x"], B["y"]), g.depot(A["x"], A["y"])
    print("stops", sa, sb, "depot", dp)
    print("buses", g.buy_bus((dp["x"], dp["y"]), [(sa["x"], sa["y"]), (sb["x"], sb["y"])], 3))
    time.sleep(20)
    print("vehicles", g.vehicles())
    a.close()


if __name__ == "__main__":
    main()

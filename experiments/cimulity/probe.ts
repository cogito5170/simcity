/**
 * Headless probe of the cimulity simulation core (https://github.com/zeikar/cimulity).
 * No browser, no renderer: build a small city, run months, branch a saved state
 * into two strategies and compare. Used for docs/EVAL_CIMULITY.md.
 *
 *   git clone https://github.com/zeikar/cimulity /path/to/cimulity
 *   CIMULITY_DIR=/path/to/cimulity bun run experiments/cimulity/probe.ts
 */
const ROOT = process.env.CIMULITY_DIR ?? '../cimulity';
const { World } = await import(`${ROOT}/game/core/World.ts`);
const { Tool } = await import(`${ROOT}/game/tools/Tool.ts`);
const { executeClick, executeDrag } = await import(`${ROOT}/game/engine/CommandDispatcher.ts`);
const { inspectTile } = await import(`${ROOT}/game/engine/inspectTile.ts`);
const { serializeWorld, deserializeWorldInto } = await import(`${ROOT}/game/core/mapSerialization.ts`);

const act = (w: any, t: any, a: any, b?: any) => (b ? executeDrag(t, a, b, w) : executeClick(t, a, w));
const months = (w: any, n: number) => {
  for (let m = 0; m < n; m++) { const s = w.getElapsedDays(); while (w.getElapsedDays() - s < 30) w.tick(); }
};
const kpi = (w: any) => {
  const d = w.getDemand();
  return `pop=${w.getPopulation()} money=${w.getMoney()} jobs=${w.getJobsCapacity()} ` +
    `R/C/I=${d.residential.toFixed(2)}/${d.commercial.toFixed(2)}/${d.industrial.toFixed(2)} ` +
    `happy=${w.getHappiness().toFixed(3)} tax/upkeep=${w.monthlyTaxIncome()}/${w.monthlyUpkeep()}`;
};

// 1) utilities not touching the road: nothing grows; inspectTile says why
const bad = new World(64, 64, { regenerate: false });
act(bad, Tool.ROAD, { x: 10, y: 20 }, { x: 40, y: 20 });
act(bad, Tool.ZONE_RESIDENTIAL, { x: 10, y: 21 }, { x: 25, y: 24 });
act(bad, Tool.POWER_PLANT, { x: 10, y: 16 });
act(bad, Tool.WATER_TOWER, { x: 16, y: 18 });
months(bad, 6);
const t = inspectTile(bad, { x: 12, y: 21 });
console.log('[disconnected] 6 months:', kpi(bad), `| zone tile powered=${t.powered} watered=${t.watered}`);

// 2) utilities touching the road
const base = () => {
  const w = new World(64, 64, { regenerate: false });
  act(w, Tool.ROAD, { x: 10, y: 20 }, { x: 40, y: 20 });
  act(w, Tool.POWER_PLANT, { x: 10, y: 18 });
  act(w, Tool.WATER_TOWER, { x: 13, y: 19 });
  act(w, Tool.ZONE_RESIDENTIAL, { x: 14, y: 21 }, { x: 24, y: 22 });
  act(w, Tool.ZONE_COMMERCIAL, { x: 25, y: 21 }, { x: 30, y: 22 });
  act(w, Tool.ZONE_INDUSTRIAL, { x: 31, y: 21 }, { x: 38, y: 22 });
  months(w, 12);
  return w;
};
const a = base(), b = base();
console.log('[connected] 12 months:', kpi(a));
console.log('determinism:', kpi(a) === kpi(b) ? 'same input -> same result' : 'DIFFERENT');

// 3) what-if from one snapshot
const snap = serializeWorld(a);
const noop = new World(64, 64, { regenerate: false }); deserializeWorldInto(noop, snap); months(noop, 24);
const svc = new World(64, 64, { regenerate: false }); deserializeWorldInto(svc, snap);
act(svc, Tool.POLICE_STATION, { x: 16, y: 18 }); act(svc, Tool.FIRE_STATION, { x: 19, y: 18 });
act(svc, Tool.HOSPITAL, { x: 22, y: 18 }); act(svc, Tool.SCHOOL, { x: 26, y: 18 });
months(svc, 24);
console.log('[what-if +24m] do nothing  :', kpi(noop));
console.log('[what-if +24m] add services:', kpi(svc));

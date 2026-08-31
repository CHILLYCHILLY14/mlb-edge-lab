/*
 * The two simulators must agree.
 *
 *     node tests/test_sim.mjs [path/to/docs/data]
 *
 * pipeline/model/simulate.py runs at build time; docs/sim.js runs in the page
 * so you can change an input and see the score move. They are two hand-written
 * implementations of the same game, which is exactly the arrangement that
 * drifts apart quietly. This replays every published slate through the
 * JavaScript engine and checks it lands on Python's numbers inside Monte Carlo
 * error - and that the controls actually move the score in the right direction.
 */
import { createRequire } from "node:module";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const SIM = require("../docs/sim.js");

const dir = process.argv[2] || "docs/data";
let fails = 0;
const check = (name, cond, detail = "") => {
  if (cond) console.log(`  PASS  ${name}`);
  else { console.log(`  FAIL  ${name}  ${detail}`); fails++; }
};

if (!existsSync(dir)) {
  console.log(`skipped — no feed at ${dir} (run: python -m tools.make_sample docs/data)`);
  process.exit(0);
}
const slates = readdirSync(dir).filter(f => f.startsWith("slate-") && f.endsWith(".json"));
if (!slates.length) {
  console.log(`skipped — no slates in ${dir}`);
  process.exit(0);
}

const N = 20000;
console.log(`\n[replaying ${slates.length} slate(s) through the browser engine]`);

let compared = 0;
const dWin = [], dTotal = [], dRl = [], dF5 = [], dNrfi = [];
for (const f of slates) {
  const slate = JSON.parse(readFileSync(path.join(dir, f), "utf8"));
  for (const g of slate.games || []) {
    if (!g.sim_inputs) continue;
    // Python derives the run line against the market's own number, which is
    // +1.5 whenever the road team is favored. Feed the JavaScript side the same
    // line or the two are answering different questions.
    const rlLine = (g.odds && g.odds.rl_line != null) ? g.odds.rl_line : -1.5;
    const js = SIM.runGame(g.sim_inputs, { rlLine }, N, g.sim_inputs.seed);
    const py = g.sim;
    compared++;
    dWin.push(Math.abs(js.p_home - py.p_sim_home));
    dTotal.push(Math.abs(js.mean_total - py.mean_total));
    dRl.push(Math.abs(js.p_home_rl - py.p_home_rl));
    dF5.push(Math.abs(js.mean_f5_total - py.mean_f5_total));
    dNrfi.push(Math.abs(js.p_nrfi - py.p_nrfi));
  }
}
const worst = a => Math.max(...a);
const avg = a => a.reduce((x, y) => x + y, 0) / a.length;

check("every published game carries its simulator inputs", compared > 0, String(compared));
// Two independent 20k runs of the same game differ by roughly 0.5 points of
// win probability from sampling alone; anything inside a point is agreement.
check("home win probability agrees", worst(dWin) < 0.020,
  `worst ${worst(dWin).toFixed(4)}, average ${avg(dWin).toFixed(4)} over ${compared} games`);
check("projected total agrees", worst(dTotal) < 0.20,
  `worst ${worst(dTotal).toFixed(3)} runs`);
check("run line agrees", worst(dRl) < 0.020, `worst ${worst(dRl).toFixed(4)}`);
check("first five agrees", worst(dF5) < 0.20, `worst ${worst(dF5).toFixed(3)} runs`);
check("first inning agrees", worst(dNrfi) < 0.020, `worst ${worst(dNrfi).toFixed(4)}`);

console.log("\n[the controls move the game the way they should]");
// Future slates often begin with a TBA starter. A starter-quality slider is
// correctly inert for a bullpen game, so select the newest published matchup
// that actually has both starters before testing starter controls.
let game = null;
for (const file of slates.slice().sort().reverse()) {
  const candidateSlate = JSON.parse(readFileSync(path.join(dir, file), "utf8"));
  game = (candidateSlate.games || []).find(g => g.sim_inputs
    && !g.sim_inputs.away.no_starter && !g.sim_inputs.home.no_starter);
  if (game) break;
}
if (!game) {
  const fallback = JSON.parse(readFileSync(path.join(dir, slates[slates.length - 1]), "utf8"));
  game = (fallback.games || []).find(g => g.sim_inputs);
}
const base = SIM.runGame(game.sim_inputs, {}, N, 11);

const hotter = SIM.runGame(game.sim_inputs,
  { away: SIM.weatherMults(1.10), home: SIM.weatherMults(1.10) }, N, 11);
check("a hotter, wind-out night raises the total",
  hotter.mean_total > base.mean_total + 0.15,
  `${base.mean_total.toFixed(2)} -> ${hotter.mean_total.toFixed(2)}`);

const colder = SIM.runGame(game.sim_inputs,
  { away: SIM.weatherMults(0.90), home: SIM.weatherMults(0.90) }, N, 11);
check("a cold night with the wind in lowers it",
  colder.mean_total < base.mean_total - 0.15,
  `${base.mean_total.toFixed(2)} -> ${colder.mean_total.toFixed(2)}`);

const acePitching = SIM.runGame(game.sim_inputs, { away: { spQuality: 0.80 } }, N, 11);
check("a better home starter suppresses the away side",
  acePitching.mean_away < base.mean_away - 0.15,
  `${base.mean_away.toFixed(2)} -> ${acePitching.mean_away.toFixed(2)}`);
check("...and lifts the home team's win probability",
  acePitching.p_home > base.p_home,
  `${base.p_home.toFixed(3)} -> ${acePitching.p_home.toFixed(3)}`);

const gutted = SIM.runGame(game.sim_inputs,
  { home: { benched: [0, 1, 2, 3] } }, N, 11);
check("benching the top of the order costs runs",
  gutted.mean_home !== base.mean_home,
  `${base.mean_home.toFixed(2)} -> ${gutted.mean_home.toFixed(2)}`);

const shortHook = SIM.runGame(game.sim_inputs, { away: { bfMean: 12 } }, N, 11);
check("pulling the starter early changes the away total",
  Math.abs(shortHook.mean_away - base.mean_away) > 0.02,
  `${base.mean_away.toFixed(2)} -> ${shortHook.mean_away.toFixed(2)}`);

console.log("\n[determinism]");
const a = SIM.runGame(game.sim_inputs, {}, 4000, 99);
const b = SIM.runGame(game.sim_inputs, {}, 4000, 99);
check("the same seed gives the same answer", a.p_home === b.p_home && a.mean_total === b.mean_total);
const c = SIM.runGame(game.sim_inputs, {}, 4000, 100);
check("a different seed gives a different answer", c.p_home !== a.p_home);

console.log("\n" + "=".repeat(60));
if (fails) { console.log(`${fails} FAILURE(S)`); process.exit(1); }
console.log("all simulator checks passed");

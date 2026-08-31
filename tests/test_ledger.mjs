/*
 * Client-side ledger tests. Runs in plain node, no browser.
 *
 *     node tests/test_ledger.mjs
 *
 * The settlement rules here are a second implementation of the ones in
 * pipeline/grade.py. That duplication is deliberate - the browser cannot call
 * the MLB API - but it means the two can drift, so these cases pin the shapes
 * that would break first: the run line's sign, pushes, and F5 ties.
 */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const L = require("../docs/ledger.js");

let fails = 0;
const check = (name, cond, detail = "") => {
  if (cond) console.log(`  PASS  ${name}`);
  else { console.log(`  FAIL  ${name}  ${detail}`); fails++; }
};
const base = { gamePk: 42, date: "2026-08-21", away: "DET", home: "PHI",
               tier: "GOOD", edge: 0.03, stake: 10, price: -110 };
const R = (a, h, fa, fh) => ({ away_score: a, home_score: h, f5_away: fa, f5_home: fh });

console.log("\n[prices]");
check("decimal from -110", Math.abs(L.decimal(-110) - 1.909) < 0.001);
check("decimal from +150", Math.abs(L.decimal(150) - 2.5) < 1e-9);
check("nonsense price is inert", L.decimal("x") === 1);

console.log("\n[moneyline]");
check("away wins", L.settle({ ...base, market: "ML", selection: "DET" }, R(5, 4)).result === "win");
check("home wins", L.settle({ ...base, market: "ML", selection: "PHI" }, R(5, 4)).result === "loss");
check("payout uses the price taken",
  Math.abs(L.settle({ ...base, market: "ML", selection: "DET", price: 150 }, R(5, 4)).pl - 15) < 1e-9);

console.log("\n[run line]");
const rl = (sel, line, a, h) => L.settle({ ...base, market: "RL", selection: sel, line }, R(a, h)).result;
check("home -1.5 needs two", rl("PHI", -1.5, 3, 5) === "win" && rl("PHI", -1.5, 4, 5) === "loss");
check("away +1.5 survives a one-run loss", rl("DET", -1.5, 4, 5) === "win");
check("home +1.5 survives a one-run loss", rl("PHI", 1.5, 5, 4) === "win");
check("home +1.5 dies on a three-run loss", rl("PHI", 1.5, 7, 4) === "loss");
check("away -1.5 needs two", rl("DET", 1.5, 6, 4) === "win" && rl("DET", 1.5, 5, 4) === "loss");
check("a whole-number run line can push",
  L.settle({ ...base, market: "RL", selection: "PHI", line: -1 }, R(4, 5)).result === "push");

console.log("\n[totals]");
const tot = (sel, line, a, h) => L.settle({ ...base, market: "TOTAL", selection: sel, line }, R(a, h)).result;
check("over clears", tot("Over", 8.5, 5, 4) === "win");
check("under clears", tot("Under", 8.5, 4, 4) === "win");
check("integer total pushes", tot("Over", 9, 5, 4) === "push");
check("missing line is ungradeable",
  L.settle({ ...base, market: "TOTAL", selection: "Over" }, R(5, 4)) === null);

console.log("\n[first five]");
check("F5 side", L.settle({ ...base, market: "F5 ML", selection: "DET" }, R(5, 4, 3, 1)).result === "win");
check("F5 tie pushes", L.settle({ ...base, market: "F5 ML", selection: "DET" }, R(5, 4, 2, 2)).result === "push");
check("F5 without innings is ungradeable",
  L.settle({ ...base, market: "F5 ML", selection: "DET" }, R(5, 4)) === null);
check("F5 total", L.settle({ ...base, market: "F5 TOTAL", selection: "Under", line: 4.5 }, R(5, 4, 2, 1)).result === "win");

console.log("\n[unfinished games]");
check("no score, no grade", L.settle({ ...base, market: "ML", selection: "DET" }, {}) === null);
check("missing result, no grade", L.settle({ ...base, market: "ML", selection: "DET" }, null) === null);

console.log("\n[bulk settlement]");
const entries = [
  { ...base, market: "ML", selection: "DET", label: "DET ML" },
  { ...base, gamePk: 43, market: "ML", selection: "DET", label: "pending" },
];
const done = L.settleAll(entries, { 42: R(5, 4) });
check("settles only what has finished", done.changed === 1);
check("pending stays pending", done.entries[1].result == null);
check("settling twice is a no-op",
  L.settleAll(done.entries, { 42: R(5, 4) }).changed === 0);

console.log("\n[summary]");
const rows = [
  { ...base, market: "ML", selection: "DET", tier: "BEST BET", result: "win", pl: 9.09 },
  { ...base, market: "ML", selection: "PHI", tier: "GOOD", result: "loss", pl: -10 },
  { ...base, market: "TOTAL", selection: "Over", tier: "GOOD", result: "push", pl: 0 },
  { ...base, market: "RL", selection: "DET", tier: "LEAN", result: null, pl: null },
];
const sum = L.summarise(rows, 250);
check("counts every tracked bet", sum.overall.n === 4);
check("pending excluded from the record", sum.graded.w === 1 && sum.graded.l === 1);
check("pushes do not count in the hit rate", Math.abs(sum.graded.win_pct - 0.5) < 1e-9);
check("pushes are not staked", Math.abs(sum.graded.staked - 20) < 1e-9);
check("bankroll tracks P/L", Math.abs(sum.bankroll_now - (250 - 0.91)) < 0.01);
check("tiers are broken out",
  sum.by_tier["BEST BET"].n === 1 && sum.by_tier.GOOD.n === 2 && sum.by_tier.LEAN.n === 1);
check("open risk counts only unsettled", Math.abs(sum.overall.at_risk - 10) < 1e-9);

console.log("\n[export and import]");
const csv = L.toCSV(rows);
check("csv has a header and a row per bet", csv.split("\n").length === rows.length + 1);
check("csv quotes anything dangerous",
  L.toCSV([{ ...base, label: 'DET ML, "big"' }]).includes('"DET ML, ""big"""'));
const round = L.fromImport(JSON.stringify({ entries: rows }));
check("import accepts an export envelope", round.length === 4);
check("import accepts a bare array", L.fromImport(JSON.stringify(rows)).length === 4);
check("import drops junk rows",
  L.fromImport(JSON.stringify([{ nope: 1 }, rows[0]])).length === 1);
const merged = L.merge(rows, [rows[0], { ...base, gamePk: 99, market: "ML", selection: "X" }]);
check("merge deduplicates", merged.added === 1 && merged.entries.length === 5,
  `${merged.added} added, ${merged.entries.length} total`);
check("one bet per game, market and selection",
  new Set(rows.map(L.keyOf)).size === rows.length);

console.log("\n[storage without a browser]");
check("load survives no localStorage", Array.isArray(L.load()) && L.load().length === 0);
check("save reports failure instead of throwing", L.save([]) === false);
check("availability probe is honest", L.available() === false);

console.log("\n" + "=".repeat(60));
if (fails) { console.log(`${fails} FAILURE(S)`); process.exit(1); }
console.log("all client checks passed");

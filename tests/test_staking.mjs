/* Client-side adjustable staking tests. */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const K = require("../docs/staking.js");

let fails = 0;
const check = (name, cond, detail = "") => {
  if (cond) console.log(`  PASS  ${name}`);
  else { console.log(`  FAIL  ${name}  ${detail}`); fails++; }
};

const limits = { max_stake_pct: 0.05, max_slate_exposure_pct: 0.15,
                 min_stake: 1, stake_rounding: 0.5 };
const bet = { edge: 0.04, edge_real: 0.04, decimal: 2.1, price: 110,
              stake: 2.5, market: "ML", selection: "TOR" };
const game = { gamePk: 42, bets: [bet] };

console.log("\n[settings]");
check("invalid bankroll falls back", K.cleanBankroll("x", 250) === 250);
check("allowed Kelly choices survive", [0.25, 0.5, 1].every(x => K.cleanKelly(x) === x));
check("unsupported Kelly falls back", K.cleanKelly(0.75, 0.25) === 0.25);

console.log("\n[sizing]");
const q = K.sizeBet(bet, { bankroll: 250, kelly: 0.25 }, limits);
const h = K.sizeBet(bet, { bankroll: 250, kelly: 0.5 }, limits);
const f = K.sizeBet(bet, { bankroll: 250, kelly: 1 }, limits);
check("half Kelly is no smaller than quarter", h.stake >= q.stake, `${q.stake} -> ${h.stake}`);
check("full Kelly is no smaller than half", f.stake >= h.stake, `${h.stake} -> ${f.stake}`);
check("single bet respects 5% cap", f.stake <= 12.5, String(f.stake));
check("bankroll rescales stake",
  K.sizeBet(bet, { bankroll: 500, kelly: 0.25 }, limits).stake >= q.stake * 1.7);

console.log("\n[portfolio]");
const slate = { games: Array.from({ length: 6 }, (_, i) => ({
  gamePk: i + 1,
  bets: [{ ...bet, selection: `T${i}`, stake: 10 }],
})) };
const plan = K.plan(slate, { bankroll: 100, kelly: 1 }, limits);
check("daily exposure stays at or below 15%", plan.staked <= 15, String(plan.staked));
check("passed and suppressed zero-stake bets are not promoted",
  K.plan({ games: [{ gamePk: 9, bets: [{ ...bet, stake: 0 }] }] },
         { bankroll: 1000, kelly: 1 }, limits).n_plays === 0);
check("plan keys identify the exact market", Object.keys(K.plan({ games: [game] },
  { bankroll: 250, kelly: 0.25 }, limits).bets)[0] === "42|ML|TOR");

console.log("\n" + "=".repeat(60));
if (fails) { console.log(`${fails} FAILURE(S)`); process.exit(1); }
console.log("all staking checks passed");

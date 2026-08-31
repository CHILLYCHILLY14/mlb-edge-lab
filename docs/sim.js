/*
 * The game simulator, in the browser.
 *
 * A line-for-line port of pipeline/model/simulate.py so the page can replay any
 * game with the inputs changed - a different starter, a bullpen arm back,
 * fifteen degrees colder, the wind turned around - and show what it does to the
 * score. The build ships the rate vectors; this runs the same plate appearances
 * over them.
 *
 * The two engines are held together by tests/test_sim.mjs, which replays a
 * published slate here and checks the numbers land on top of Python's inside
 * Monte Carlo error. If they ever drift apart, that test fails.
 *
 * Outcome order is fixed everywhere in this repo:
 *   0 BB (walk or hit by pitch)  1 K  2 1B  3 2B  4 3B  5 HR  6 OUT (in play)
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MLBSim = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const I_BB = 0, I_K = 1, I_1B = 2, I_2B = 3, I_3B = 4, I_HR = 5, I_OUT = 6;

  // baserunning, matching the Python constants exactly
  const P_2ND_SCORES_ON_1B = 0.60;
  const P_1ST_TO_3RD_ON_1B = 0.28;
  const P_1ST_SCORES_ON_2B = 0.45;
  const P_GIDP = 0.11;
  const P_SAC_FLY = 0.28;
  const P_PRODUCTIVE_OUT = 0.20;
  const MAX_INNING = 21;

  /* A seeded generator, so the same inputs always give the same answer and a
     viewer can tell a real change from simulation noise. */
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* Box-Muller, for the starter's workload varying game to game. */
  function gauss(rng, mean, sd) {
    let u = 0, v = 0;
    while (u === 0) u = rng();
    while (v === 0) v = rng();
    return mean + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function cumulative(rows) {
    return rows.map(r => {
      const out = new Float64Array(7);
      let acc = 0;
      for (let i = 0; i < 7; i++) { acc += r[i]; out[i] = acc; }
      out[6] = 1;                       // guard against float drift
      return out;
    });
  }

  function pick(cum, u) {
    for (let i = 0; i < 7; i++) if (u < cum[i]) return i;
    return I_OUT;
  }

  /* ------------------------------------------------------------- rates ---- */

  /** Regress counts toward a league baseline by `prior` plate appearances. */
  function shrink(counts, denom, league, prior) {
    const v = new Float64Array(7);
    v[I_BB] = counts.bb || 0; v[I_K] = counts.k || 0; v[I_1B] = counts.s || 0;
    v[I_2B] = counts.d || 0;  v[I_3B] = counts.t || 0; v[I_HR] = counts.hr || 0;
    let used = 0;
    for (let i = 0; i < 6; i++) used += v[i];
    v[I_OUT] = Math.max(denom - used, 0);
    let sum = 0;
    for (let i = 0; i < 7; i++) { v[i] += league[i] * prior; sum += v[i]; }
    if (sum <= 0) return Float64Array.from(league);
    for (let i = 0; i < 7; i++) v[i] /= sum;
    return v;
  }

  /** Multinomial odds-ratio matchup: batter times pitcher over league. */
  function log5(bat, pit, league) {
    const v = new Float64Array(7);
    let sum = 0;
    for (let i = 0; i < 7; i++) {
      v[i] = (bat[i] * pit[i]) / Math.max(league[i], 1e-9);
      sum += v[i];
    }
    if (sum <= 0) return Float64Array.from(league);
    for (let i = 0; i < 7; i++) v[i] /= sum;
    return v;
  }

  /**
   * Scale specific outcomes and let the in-play out bucket absorb the rest, so
   * the vector stays a valid probability distribution.
   */
  function applyMultipliers(vec, hrMult, hitMult, kMult, bbMult) {
    const v = Float64Array.from(vec);
    v[I_HR] *= (hrMult == null ? 1 : hrMult);
    v[I_1B] *= (hitMult == null ? 1 : hitMult);
    v[I_2B] *= (hitMult == null ? 1 : hitMult);
    v[I_3B] *= (hitMult == null ? 1 : hitMult);
    v[I_K] *= (kMult == null ? 1 : kMult);
    v[I_BB] *= (bbMult == null ? 1 : bbMult);
    let nonOut = 0;
    for (let i = 0; i < 6; i++) nonOut += v[i];
    if (nonOut >= 0.98) {
      let s = nonOut + v[I_OUT];
      for (let i = 0; i < 7; i++) v[i] /= s;
      return v;
    }
    v[I_OUT] = 1 - nonOut;
    return v;
  }

  /* --------------------------------------------------------- simulation --- */

  function makeSide(pack, rng) {
    const noStarter = !!pack.no_starter;
    return {
      cSp: cumulative(pack.p_sp),
      cSp3: cumulative(pack.p_sp3),
      cPen: cumulative(pack.p_pen),
      adv: pack.adv == null ? 1 : pack.adv,
      bfMean: pack.bf_mean, bfSd: pack.bf_sd == null ? 4.5 : pack.bf_sd,
      bfMin: pack.bf_min == null ? 9 : pack.bf_min,
      bfMax: pack.bf_max == null ? 30 : pack.bf_max,
      noStarter,
      runs: 0, pos: 0, spBf: 0, bfLimit: 0,
    };
  }

  function resetSide(s, rng) {
    s.runs = 0; s.pos = 0; s.spBf = 0;
    s.bfLimit = s.noStarter ? 0
      : Math.min(Math.max(Math.round(gauss(rng, s.bfMean, s.bfSd)), s.bfMin), s.bfMax);
  }

  /**
   * One half inning. `walkoffVs` set means stop the moment this side goes
   * ahead, which is what the bottom of the ninth and every extra inning is.
   */
  function halfInning(s, rng, ghost, walkoffVs) {
    let outs = 0, b1 = false, b2 = ghost === true, b3 = false;
    let guard = 0;
    while (outs < 3) {
      if (walkoffVs != null && s.runs > walkoffVs) return;
      if (++guard > 200) return;

      const usePen = s.spBf >= s.bfLimit;
      const tto3 = !usePen && s.spBf >= 18;
      const cum = usePen ? s.cPen[s.pos] : (tto3 ? s.cSp3[s.pos] : s.cSp[s.pos]);
      const oc = pick(cum, rng());
      if (!usePen) s.spBf += 1;
      s.pos = (s.pos + 1) % 9;

      if (oc === I_BB) {
        if (b1 && b2 && b3) s.runs += 1;
        const n3 = b3 || (b1 && b2), n2 = b2 || b1;
        b3 = n3; b2 = n2; b1 = true;

      } else if (oc === I_K) {
        outs += 1;

      } else if (oc === I_1B) {
        const sc2 = b2 && rng() < Math.min(P_2ND_SCORES_ON_1B * s.adv, 0.85);
        const to3 = b1 && rng() < Math.min(P_1ST_TO_3RD_ON_1B * s.adv, 0.50);
        s.runs += (b3 ? 1 : 0) + (sc2 ? 1 : 0);
        const n3 = (b2 && !sc2) || to3, n2 = b1 && !to3;
        b3 = n3; b2 = n2; b1 = true;

      } else if (oc === I_2B) {
        const sc1 = b1 && rng() < Math.min(P_1ST_SCORES_ON_2B * s.adv, 0.70);
        s.runs += (b3 ? 1 : 0) + (b2 ? 1 : 0) + (sc1 ? 1 : 0);
        b3 = b1 && !sc1; b2 = true; b1 = false;

      } else if (oc === I_3B) {
        s.runs += (b1 ? 1 : 0) + (b2 ? 1 : 0) + (b3 ? 1 : 0);
        b3 = true; b2 = false; b1 = false;

      } else if (oc === I_HR) {
        s.runs += 1 + (b1 ? 1 : 0) + (b2 ? 1 : 0) + (b3 ? 1 : 0);
        b1 = false; b2 = false; b3 = false;

      } else {                                   // ball in play, out
        const r = rng(), r2 = rng(), r3 = rng();
        const gidp0 = b1 && outs === 0 && r < P_GIDP;
        const gidp1 = b1 && outs === 1 && r < P_GIDP;
        const sacf = !gidp0 && !gidp1 && b3 && outs < 2
                     && r2 < Math.min(P_SAC_FLY * s.adv, 0.45);
        const prod = !gidp0 && !gidp1 && !sacf && b2 && !b3 && outs < 2
                     && r3 < P_PRODUCTIVE_OUT;
        if (gidp0) { outs += 2; s.runs += b3 ? 1 : 0; b1 = false; b3 = false; }
        else if (gidp1) { outs += 2; b1 = false; }
        else if (sacf) { outs += 1; s.runs += 1; b3 = false; }
        else if (prod) { outs += 1; b3 = true; b2 = false; }
        else { outs += 1; }
      }
    }
  }

  /** Play `n` complete games. Returns the run arrays, same shape as Python. */
  function simulate(awayPack, homePack, n, seed) {
    const rng = mulberry32((seed == null ? 1 : seed) >>> 0);
    const A = makeSide(awayPack, rng), H = makeSide(homePack, rng);
    const away = new Int16Array(n), home = new Int16Array(n);
    const f5a = new Int16Array(n), f5h = new Int16Array(n);
    const i1a = new Int16Array(n), i1h = new Int16Array(n);

    for (let g = 0; g < n; g++) {
      resetSide(A, rng); resetSide(H, rng);
      for (let inning = 1; inning <= 9; inning++) {
        halfInning(A, rng, false, null);
        if (inning === 9) {
          if (H.runs <= A.runs) halfInning(H, rng, false, A.runs);
        } else {
          halfInning(H, rng, false, null);
        }
        if (inning === 1) { i1a[g] = A.runs; i1h[g] = H.runs; }
        if (inning === 5) { f5a[g] = A.runs; f5h[g] = H.runs; }
      }
      let inning = 10;
      while (A.runs === H.runs && inning <= MAX_INNING) {
        halfInning(A, rng, true, null);
        halfInning(H, rng, true, A.runs);
        inning += 1;
      }
      if (A.runs === H.runs) { if (rng() < 0.5) H.runs += 1; else A.runs += 1; }
      away[g] = A.runs; home[g] = H.runs;
    }
    return { away, home, f5_away: f5a, f5_home: f5h, i1_away: i1a, i1_home: i1h, n };
  }

  /* ------------------------------------------------------ derivations ----- */

  const mean = a => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i]; return s / a.length; };
  const frac = (a, f) => { let c = 0; for (let i = 0; i < a.length; i++) if (f(a[i], i)) c++; return c / a.length; };

  function median(a) {
    const s = Array.from(a).sort((x, y) => x - y);
    const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  function overUnder(totals, line) {
    let over = 0, under = 0, push = 0;
    for (let i = 0; i < totals.length; i++) {
      if (totals[i] > line) over++;
      else if (totals[i] < line) under++;
      else push++;
    }
    const n = totals.length;
    let o = over / n, u = under / n;
    if (push > 0) { const d = o + u; o = d ? o / d : 0.5; u = 1 - o; }
    return { over: o, under: u, push: push / n };
  }

  function hist(values, lo, hi) {
    const out = new Array(hi - lo + 1).fill(0);
    for (let i = 0; i < values.length; i++) {
      const v = Math.min(Math.max(values[i], lo), hi);
      out[v - lo] += 1;
    }
    return out;
  }

  function derive(sim, marketTotal, rlLine) {
    const n = sim.n;
    const tot = new Int16Array(n), marg = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      tot[i] = sim.away[i] + sim.home[i];
      marg[i] = sim.home[i] - sim.away[i];
    }
    const pHome = frac(marg, m => m > 0);
    const line = rlLine == null ? -1.5 : rlLine;
    const out = {
      p_home: pHome, p_away: 1 - pHome,
      se: Math.sqrt(Math.max(pHome * (1 - pHome), 1e-9) / n),
      mean_away: mean(sim.away), mean_home: mean(sim.home),
      mean_total: mean(tot), mean_margin: mean(marg),
      fair_total: median(tot),
      p_home_rl: frac(marg, m => m + line > 0),
      p_f5_home: frac(sim.f5_home, (v, i) => v > sim.f5_away[i]),
      p_f5_away: frac(sim.f5_away, (v, i) => v > sim.f5_home[i]),
      p_f5_tie: frac(sim.f5_away, (v, i) => v === sim.f5_home[i]),
      mean_f5_total: mean(sim.f5_away) + mean(sim.f5_home),
      p_nrfi: frac(sim.i1_away, (v, i) => v + sim.i1_home[i] === 0),
      p_away_shutout: frac(sim.away, v => v === 0),
      p_home_shutout: frac(sim.home, v => v === 0),
      hist: hist(tot, 0, 22),
    };
    out.p_away_rl = 1 - out.p_home_rl;
    out.p_yrfi = 1 - out.p_nrfi;
    if (marketTotal != null) {
      const ou = overUnder(tot, marketTotal);
      out.p_total_over = ou.over; out.p_total_under = ou.under; out.p_total_push = ou.push;
    }
    return out;
  }

  /* -------------------------------------------------- rebuilding a game --- */

  /**
   * Turn the published rate vectors back into the three 9x7 matrices the
   * simulator runs on, applying park, weather, defense and home field in the
   * same order the build does.
   *
   * `tweaks` is what makes this a simulator rather than a replay:
   *   hr, hit          extra multipliers on top of the published ones
   *   spQuality        > 1 makes the opposing starter worse, < 1 better
   *   penQuality       same for the bullpen
   *   bfMean           how long the opposing starter goes
   *   benched          indexes of hitters replaced by a replacement-level bat
   *   adv              baserunning
   */
  function buildPack(side, mults, league, tweaks) {
    const t = tweaks || {};
    const hfa = (mults.hfa || 0) * (side.hfa_sign || 0);
    const hfaHr = 1 + 2.2 * hfa, hfaHit = 1 + 0.6 * hfa;
    const def = side.hfa_sign === 1 ? mults.def_home : mults.def_away;
    const hrM = (mults.hr || 1) * (t.hr == null ? 1 : t.hr);
    const hitM = (mults.hit || 1) * def * (t.hit == null ? 1 : t.hit);

    const degrade = (vec, q) => {
      if (q == null || Math.abs(q - 1) < 1e-9) return vec;
      const k = q - 1;                       // q>1 => pitcher gives up more
      return applyMultipliers(vec, 1 + 2 * k, 1 + k, 1 - 0.5 * k);
    };
    const oppSp = degrade(side.opp_sp, t.spQuality);
    const oppSp3 = degrade(side.opp_sp3, t.spQuality);
    const oppPen = degrade(side.opp_pen, t.penQuality);

    const benched = new Set(t.benched || []);
    const pSp = [], pSp3 = [], pPen = [];
    for (let i = 0; i < 9; i++) {
      const b = side.bats[i];
      // A benched bat is replaced by a league-average one, which is roughly
      // what the 26th man on the roster is.
      const vsSp = benched.has(i) ? league : b.vs_sp;
      const vsPen = benched.has(i) ? league : b.vs_pen;
      pSp.push(applyMultipliers(log5(vsSp, oppSp, league), hrM * hfaHr, hitM * hfaHit));
      pSp3.push(applyMultipliers(log5(vsSp, oppSp3, league), hrM * hfaHr, hitM * hfaHit));
      pPen.push(applyMultipliers(log5(vsPen, oppPen, league), hrM * hfaHr, hitM * hfaHit));
    }
    return {
      p_sp: pSp, p_sp3: pSp3, p_pen: pPen,
      bf_mean: t.bfMean == null ? side.bf_mean : t.bfMean,
      bf_sd: side.bf_sd, bf_min: side.bf_min, bf_max: side.bf_max,
      adv: t.adv == null ? side.adv : t.adv,
      no_starter: t.noStarter == null ? side.no_starter : t.noStarter,
    };
  }

  /**
   * Weather, expressed the way the build expresses it: a run-environment
   * multiplier that lands mostly on home runs and partly on other hits.
   */
  function weatherMults(runMult) {
    const m = runMult == null ? 1 : runMult;
    return { hr: 1 + 2.2 * (m - 1), hit: 1 + 0.5 * (m - 1) };
  }

  /** Replay a published game, optionally with inputs changed. */
  function runGame(inputs, tweaks, nSims, seed) {
    const t = tweaks || {};
    const league = inputs.league;
    const away = buildPack(inputs.away, inputs.mults, league, t.away);
    const home = buildPack(inputs.home, inputs.mults, league, t.home);
    const sim = simulate(away, home, nSims || 12000,
                         seed == null ? inputs.seed : seed);
    return derive(sim, t.marketTotal, t.rlLine == null ? -1.5 : t.rlLine);
  }

  return { simulate, derive, shrink, log5, applyMultipliers, mulberry32,
           overUnder, median, buildPack, runGame, weatherMults,
           I_BB, I_K, I_1B, I_2B, I_3B, I_HR, I_OUT };
});

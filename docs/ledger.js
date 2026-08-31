/*
 * My Ledger - the bets you actually placed.
 *
 * Deliberately separate from the model's shadow book. The shadow book grades
 * every call the model made, including the ones it told you to pass on, which
 * is how you find out whether the thresholds sit in the right place. This file
 * tracks only what you clicked, at the price and stake you took, so the two
 * questions - "is the model any good" and "am I any good at using it" - never
 * get answered with the same number.
 *
 * Storage is the browser's, per device. Nothing leaves the page. Export writes
 * the whole ledger out as JSON or CSV so it survives a cleared browser.
 *
 * Pure functions live at the top so they can be tested outside a browser.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MLBLedger = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STORAGE_KEY = "mlbedge.ledger.v2";
  const SCHEMA = 2;

  /* ------------------------------------------------------------- pure ---- */

  function keyOf(e) {
    return `${e.gamePk}|${e.market}|${e.selection}`;
  }

  function decimal(american) {
    const a = Number(american);
    if (!isFinite(a) || a === 0) return 1;
    return 1 + (a > 0 ? a / 100 : 100 / Math.abs(a));
  }

  /**
   * Settle one ledger entry against a final score.
   * Mirrors pipeline/grade.py::_settle exactly - if one changes, change both.
   * Returns null when the game has not finished or cannot be graded yet.
   */
  function settle(entry, res) {
    if (!res || res.away_score == null || res.home_score == null) return null;
    const a = Number(res.away_score), h = Number(res.home_score);
    const m = entry.market, sel = entry.selection;
    let result = null;

    if (m === "ML") {
      result = ((sel === entry.away) ? a > h : h > a) ? "win" : "loss";

    } else if (m === "RL") {
      // line is stored from the home team's point of view, sign included
      const ln = entry.line == null ? -1.5 : Number(entry.line);
      const marg = h - a;
      const cover = (sel === entry.home) ? marg + ln : -marg - ln;
      result = cover > 0 ? "win" : cover === 0 ? "push" : "loss";

    } else if (m === "TOTAL") {
      const ln = Number(entry.line);
      if (!isFinite(ln)) return null;
      const tot = a + h;
      result = tot === ln ? "push" : ((tot > ln) === (sel === "Over") ? "win" : "loss");

    } else if (m === "F5 ML") {
      if (res.f5_away == null || res.f5_home == null) return null;
      const fa = Number(res.f5_away), fh = Number(res.f5_home);
      result = fa === fh ? "push"
             : (((sel === entry.away) ? fa > fh : fh > fa) ? "win" : "loss");

    } else if (m === "F5 TOTAL") {
      if (res.f5_away == null || res.f5_home == null) return null;
      const ln = Number(entry.line);
      if (!isFinite(ln)) return null;
      const tot = Number(res.f5_away) + Number(res.f5_home);
      result = tot === ln ? "push" : ((tot > ln) === (sel === "Over") ? "win" : "loss");
    }
    if (!result) return null;

    const stake = Number(entry.stake) || 0;
    const pl = result === "win" ? stake * (decimal(entry.price) - 1)
             : result === "loss" ? -stake : 0;
    return { result, pl: Math.round(pl * 100) / 100,
             final: `${a}-${h}`, settled_at: new Date().toISOString() };
  }

  /** Settle every pending entry that now has a final score. */
  function settleAll(entries, resultsByGame) {
    let changed = 0;
    const out = entries.map(e => {
      if (e.result) return e;
      const s = settle(e, (resultsByGame || {})[String(e.gamePk)]);
      if (!s) return e;
      changed++;
      return Object.assign({}, e, s);
    });
    return { entries: out, changed };
  }

  function block(rows) {
    const w = rows.filter(r => r.result === "win").length;
    const l = rows.filter(r => r.result === "loss").length;
    const p = rows.filter(r => r.result === "push").length;
    const staked = rows.filter(r => r.result && r.result !== "push")
                       .reduce((s, r) => s + (Number(r.stake) || 0), 0);
    const pl = rows.reduce((s, r) => s + (Number(r.pl) || 0), 0);
    return {
      n: rows.length, w, l, p,
      pending: rows.filter(r => !r.result).length,
      win_pct: (w + l) ? w / (w + l) : null,
      staked: Math.round(staked * 100) / 100,
      pl: Math.round(pl * 100) / 100,
      roi: staked > 0 ? pl / staked : null,
      at_risk: Math.round(rows.filter(r => !r.result)
                              .reduce((s, r) => s + (Number(r.stake) || 0), 0) * 100) / 100,
    };
  }

  /** Overall, by tier and by market - the same shape the model's summary uses. */
  function summarise(entries, startingBankroll) {
    const graded = entries.filter(e => e.result);
    const byTier = {};
    ["BEST BET", "GOOD", "LEAN", "PASS"].forEach(t => {
      byTier[t] = block(entries.filter(e => e.tier === t));
    });
    const byMarket = {};
    [...new Set(entries.map(e => e.market))].sort().forEach(m => {
      byMarket[m] = block(entries.filter(e => e.market === m));
    });
    let bal = Number(startingBankroll) || 0;
    const curve = graded
      .slice()
      .sort((x, y) => String(x.date).localeCompare(String(y.date)))
      .map(e => { bal += Number(e.pl) || 0;
                  return { date: e.date, label: e.label, pl: e.pl,
                           balance: Math.round(bal * 100) / 100 }; });
    return {
      overall: block(entries), graded: block(graded),
      by_tier: byTier, by_market: byMarket,
      last10: block(graded.slice(-10)), last25: block(graded.slice(-25)),
      curve,
      bankroll_now: Math.round(((Number(startingBankroll) || 0)
        + graded.reduce((s, e) => s + (Number(e.pl) || 0), 0)) * 100) / 100,
    };
  }

  const CSV_COLS = ["date", "away", "home", "market", "selection", "label", "line",
                    "price", "stake", "tier", "edge", "p_model", "book",
                    "result", "pl", "final", "added_at"];

  function toCSV(entries) {
    const esc = v => {
      const s = v == null ? "" : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return [CSV_COLS.join(",")]
      .concat(entries.map(e => CSV_COLS.map(c => esc(e[c])).join(",")))
      .join("\n");
  }

  function fromImport(text) {
    /** Accept either a full export envelope or a bare array. */
    const parsed = JSON.parse(text);
    const rows = Array.isArray(parsed) ? parsed : (parsed.entries || []);
    return rows.filter(r => r && r.gamePk && r.market && r.selection);
  }

  function merge(existing, incoming) {
    const byKey = new Map(existing.map(e => [keyOf(e), e]));
    let added = 0;
    incoming.forEach(e => {
      const k = keyOf(e);
      if (!byKey.has(k)) { byKey.set(k, e); added++; }
    });
    return { entries: [...byKey.values()], added };
  }

  /* ---------------------------------------------------------- storage ---- */

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      const rows = Array.isArray(parsed) ? parsed : (parsed.entries || []);
      return rows.filter(r => r && r.gamePk);
    } catch (err) {
      return [];
    }
  }

  function save(entries) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(
        { schema: SCHEMA, saved_at: new Date().toISOString(), entries }));
      return true;
    } catch (err) {
      return false;                 // private window, blocked storage, quota
    }
  }

  function available() {
    try {
      const probe = "__mlbedge_probe__";
      localStorage.setItem(probe, "1");
      localStorage.removeItem(probe);
      return true;
    } catch (err) {
      return false;
    }
  }

  /* ------------------------------------------------------- entry build --- */

  function entryFrom(game, bet, stakeOverride) {
    const stake = stakeOverride != null ? Number(stakeOverride)
                : (Number(bet.stake) > 0 ? Number(bet.stake) : 1);
    return {
      gamePk: game.gamePk, date: game.date, start: game.start,
      away: game.away, home: game.home,
      market: bet.market, selection: bet.selection, label: bet.label,
      line: bet.line == null ? null : bet.line,
      price: bet.price, book: bet.book || null,
      stake: Math.round(stake * 100) / 100,
      tier: bet.tier, edge: bet.edge, p_model: bet.p_final,
      added_at: new Date().toISOString(),
      result: null, pl: null, final: null,
    };
  }

  return { STORAGE_KEY, SCHEMA, keyOf, decimal, settle, settleAll, block,
           summarise, toCSV, fromImport, merge, load, save, available,
           entryFrom, CSV_COLS };
});

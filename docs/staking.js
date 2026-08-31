/*
 * Local staking controls for the dashboard.
 *
 * The model publishes a conservative quarter-Kelly card. This module lets the
 * viewer resize that same approved portfolio for a different bankroll and for
 * quarter, half or full Kelly. It deliberately does not promote suppressed or
 * passed bets: changing risk tolerance must not change what the model likes.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MLBStaking = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STORAGE_KEY = "mlbedge.staking.v1";
  const ALLOWED_KELLY = [0.25, 0.50, 1.00];

  function cleanBankroll(value, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return Number(fallback) || 250;
    return Math.min(Math.max(Math.round(n * 100) / 100, 1), 1000000);
  }

  function cleanKelly(value, fallback) {
    const n = Number(value);
    if (ALLOWED_KELLY.includes(n)) return n;
    const f = Number(fallback);
    return ALLOWED_KELLY.includes(f) ? f : 0.25;
  }

  function defaults(index) {
    const i = index || {};
    return {
      bankroll: cleanBankroll(i.bankroll, 250),
      kelly: cleanKelly((i.settings || {}).kelly, 0.25),
    };
  }

  function load(index) {
    const base = defaults(index);
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null") || {};
      return {
        bankroll: cleanBankroll(parsed.bankroll, base.bankroll),
        kelly: cleanKelly(parsed.kelly, base.kelly),
      };
    } catch {
      return base;
    }
  }

  function save(settings) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        bankroll: cleanBankroll(settings.bankroll, 250),
        kelly: cleanKelly(settings.kelly, 0.25),
        saved_at: new Date().toISOString(),
      }));
      return true;
    } catch {
      return false;
    }
  }

  function keyOf(game, bet) {
    return `${game.gamePk}|${bet.market}|${bet.selection}`;
  }

  function decimal(bet) {
    const d = Number(bet.decimal);
    if (Number.isFinite(d) && d > 1) return d;
    const a = Number(bet.price);
    if (!Number.isFinite(a) || a === 0) return 1;
    return 1 + (a > 0 ? a / 100 : 100 / Math.abs(a));
  }

  function roundStake(value, increment, mode) {
    const inc = Number(increment) > 0 ? Number(increment) : 0.50;
    const units = Number(value) / inc;
    return (mode === "down" ? Math.floor(units + 1e-9) : Math.round(units)) * inc;
  }

  function sizeBet(bet, settings, limits) {
    const bankroll = cleanBankroll(settings.bankroll, 250);
    const fraction = cleanKelly(settings.kelly, 0.25);
    const maxPct = Number(limits.max_stake_pct) || 0.05;
    const minStake = Number(limits.min_stake) || 1;
    const rounding = Number(limits.stake_rounding) || 0.50;
    const edge = Math.min(Number(bet.edge) || 0, Number(bet.edge_real) || 0);
    const dec = decimal(bet);
    if (edge <= 0 || dec <= 1) return { stake: 0, to_win: 0, full_kelly: 0 };

    const b = dec - 1;
    const p = (1 + edge) / dec;
    const full = Math.max(0, (b * p - (1 - p)) / b);
    const used = Math.min(full * fraction, maxPct);
    let stake = roundStake(bankroll * used, rounding, "nearest");
    if (stake < minStake) stake = 0;
    return {
      stake: Math.round(stake * 100) / 100,
      to_win: Math.round(stake * (dec - 1) * 100) / 100,
      full_kelly: full,
      used_fraction: used,
    };
  }

  function plan(slate, settings, limits) {
    const out = { bets: {}, n_plays: 0, staked: 0, exposure_scaled: false,
                  scale_factor: 1, exposure_pct: 0 };
    const bankroll = cleanBankroll(settings.bankroll, 250);
    const rounding = Number(limits.stake_rounding) || 0.50;
    const active = [];

    for (const game of ((slate || {}).games || [])) {
      for (const bet of (game.bets || [])) {
        // The server has already applied correlation, readiness and play-count
        // rules. Only resize positions that survived those rules.
        if (!(Number(bet.stake) > 0)) continue;
        const sized = sizeBet(bet, settings, limits || {});
        const row = { game, bet, key: keyOf(game, bet), ...sized };
        active.push(row);
      }
    }

    let total = active.reduce((sum, row) => sum + row.stake, 0);
    const capPct = Number(limits.max_slate_exposure_pct) || 0.15;
    const cap = bankroll * capPct;
    if (total > cap && total > 0) {
      const scale = cap / total;
      out.exposure_scaled = true;
      out.scale_factor = Math.round(scale * 1000) / 1000;
      for (const row of active) {
        row.stake = roundStake(row.stake * scale, rounding, "down");
        row.to_win = Math.round(row.stake * (decimal(row.bet) - 1) * 100) / 100;
      }
      total = active.reduce((sum, row) => sum + row.stake, 0);
    }

    for (const row of active) {
      out.bets[row.key] = { stake: row.stake, to_win: row.to_win,
                            full_kelly: row.full_kelly,
                            used_fraction: row.used_fraction };
    }
    out.n_plays = active.filter(row => row.stake > 0).length;
    out.staked = Math.round(total * 100) / 100;
    out.exposure_pct = bankroll > 0 ? out.staked / bankroll : 0;
    return out;
  }

  function label(kelly) {
    return cleanKelly(kelly, 0.25) === 1 ? "Full Kelly"
      : cleanKelly(kelly, 0.25) === 0.5 ? "½ Kelly" : "¼ Kelly";
  }

  return { STORAGE_KEY, ALLOWED_KELLY, cleanBankroll, cleanKelly, defaults,
           load, save, keyOf, decimal, sizeBet, plan, label };
});

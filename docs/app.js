/* MLB Edge Desk - dashboard. No build step, no dependencies. */
(() => {
"use strict";

const Q = new URLSearchParams(location.search);
// ?theme=light or ?theme=dark pins the palette, which is what you want when the
// page is embedded in a site whose own theme never changes. Left off, it follows
// the viewer's system preference.
const T = Q.get("theme");
if (T === "light" || T === "dark") document.documentElement.dataset.theme = T;

const S = { index: null, slate: null, ratings: null, perf: null, results: null,
            preds: null, pick: null, simPick: null, simState: null, simFor: null,
            mine: [], storageOK: true, staking: null, stakePlanCache: null,
            date: Q.get("date") || null, tab: Q.get("tab") || "slate" };

const L = (typeof MLBLedger !== "undefined") ? MLBLedger : null;
const SCH = (typeof MLBSchedule !== "undefined") ? MLBSchedule : null;
const STK = (typeof MLBStaking !== "undefined") ? MLBStaking : null;
const todayET = () => SCH ? SCH.easternDate() : new Date().toISOString().slice(0, 10);

const $  = (s, r = document) => r.querySelector(s);
const el = (t, c, h) => { const n = document.createElement(t);
  if (c) n.className = c; if (h !== undefined) n.innerHTML = h; return n; };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const pct   = (v, d = 1) => v == null ? "—" : (v * 100).toFixed(d) + "%";
const money = v => v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2);
const sgn   = v => v == null ? "" : (v > 0 ? "pos" : v < 0 ? "neg" : "neu");
const num   = (v, d = 2) => v == null ? "—" : Number(v).toFixed(d);

async function getJSON(p) {
  // Single-file exports (tools/make_preview.py) inline the feed here so the
  // dashboard works with no server behind it.
  if (window.__EMBED__ && Object.prototype.hasOwnProperty.call(window.__EMBED__, p))
    return window.__EMBED__[p];
  try { const r = await fetch(p + "?v=" + Date.now(), { cache: "no-store" });
        return r.ok ? await r.json() : null; } catch { return null; }
}

/* ------------------------------------------------------------ formatting */
function timeET(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString("en-US",
      { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET";
  } catch { return ""; }
}
function agoTxt(iso) {
  if (!iso) return "";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return m + "m ago";
  const h = Math.floor(m / 60);
  return h < 24 ? h + "h ago" : Math.floor(h / 24) + "d ago";
}
const tierCls = t => t === "BEST BET" ? "best" : t === "GOOD" ? "good"
                    : t === "LEAN" ? "lean" : "pass";

/* ----------------------------------------------------------- staking plan */
function stakeLimits() {
  const st = (S.index || {}).settings || {};
  return {
    max_stake_pct: st.max_stake_pct ?? 0.05,
    max_slate_exposure_pct: st.max_slate_exposure_pct ?? 0.15,
    min_stake: st.min_stake ?? 1,
    stake_rounding: st.stake_rounding ?? 0.50,
  };
}

function currentBankroll() {
  return Number((S.staking || {}).bankroll) || Number((S.index || {}).bankroll) || 250;
}

function currentPlan() {
  const sig = [S.date, (S.slate || {}).generated_at, currentBankroll(),
               Number((S.staking || {}).kelly) || 0.25].join("|");
  if (S.stakePlanCache && S.stakePlanCache.sig === sig) return S.stakePlanCache.plan;
  if (!STK) {
    const bets = {};
    for (const g of ((S.slate || {}).games || []))
      for (const b of (g.bets || [])) bets[`${g.gamePk}|${b.market}|${b.selection}`] = b;
    const staked = Object.values(bets).reduce((sum, b) => sum + (Number(b.stake) || 0), 0);
    const plan = { bets, staked, n_plays: Object.values(bets).filter(b => b.stake > 0).length,
                   exposure_pct: staked / currentBankroll(), exposure_scaled: false };
    S.stakePlanCache = { sig, plan };
    return plan;
  }
  const plan = STK.plan(S.slate || {}, S.staking || STK.defaults(S.index), stakeLimits());
  S.stakePlanCache = { sig, plan };
  return plan;
}

function sized(g, b) {
  const row = currentPlan().bets[`${g.gamePk}|${b.market}|${b.selection}`];
  return row || { stake: 0, to_win: 0 };
}

function renderStakingControls() {
  const box = $("#stakingControls");
  if (!box) return;
  const cfg = S.staking || { bankroll: currentBankroll(), kelly: 0.25 };
  const label = STK ? STK.label(cfg.kelly) : "¼ Kelly";
  box.innerHTML = `<div class="stake-title">Staking plan
      <small>Changes today's suggested stakes only — past results and ledger bets stay unchanged.</small></div>
    <label class="stake-field">Bankroll
      <input id="bankrollInput" type="number" min="1" max="1000000" step="25"
        inputmode="decimal" value="${Number(cfg.bankroll).toFixed(2)}" aria-label="Betting bankroll">
    </label>
    <div class="stake-field"><span>Risk</span><div class="kelly-pick" role="group"
      aria-label="Kelly fraction">
      ${[[0.25, "¼ Kelly"], [0.5, "½ Kelly"], [1, "Full Kelly"]].map(([v, t]) =>
        `<button type="button" data-kelly="${v}" class="${Number(cfg.kelly) === v ? "on" : ""}"
          aria-pressed="${Number(cfg.kelly) === v}">${t}</button>`).join("")}
    </div></div>
    <span class="chip">${esc(label)} · 5% per bet · 15% daily cap</span>
    <button type="button" class="stake-reset" data-stake-reset>Reset</button>`;
}

function updateStaking(next, message) {
  if (!STK) return;
  const base = STK.defaults(S.index);
  S.staking = {
    bankroll: STK.cleanBankroll(next.bankroll, base.bankroll),
    kelly: STK.cleanKelly(next.kelly, base.kelly),
  };
  S.stakePlanCache = null;
  STK.save(S.staking);
  renderStakingControls();
  renderKPIs();
  renderView();
  if (message) toast(message);
}

/* ------------------------------------------------------------------ KPIs */
function renderKPIs() {
  const k = $("#kpis"); k.innerHTML = "";
  const p = S.perf || {}, ov = p.overall || {}, pf = (S.slate || {}).portfolio || {};
  const plan = currentPlan();
  const riskNote = `${pct(plan.exposure_pct || 0, 1)} of bankroll` +
    (plan.exposure_scaled ? ` · scaled ×${plan.scale_factor}` : "");
  const cards = [
    ["Betting bankroll", money(currentBankroll()),
     STK ? STK.label((S.staking || {}).kelly) : "¼ Kelly", ""],
    ["Plays today", plan.n_plays ?? 0, `${pf.n_best ?? 0} best bet${pf.n_best === 1 ? "" : "s"}`, ""],
    ["At risk today", money(plan.staked ?? 0), riskNote, ""],
    ["Settled record", `${ov.w ?? 0}-${ov.l ?? 0}${ov.p ? "-" + ov.p : ""}`,
     ov.n ? `${pct(ov.win_pct)} wins · ${money(ov.avg_win_pl || 0)} avg win / ${money(ov.avg_loss_pl || 0)} avg loss`
          : "no settled bets yet", ""],
    ["ROI", ov.roi == null ? "—" : pct(ov.roi, 1),
     ov.staked ? `on ${money(ov.staked)} staked` : "—", sgn(ov.roi)],
    ["Profit", money(ov.pl ?? 0), `last 20: ${money((p.last20 || {}).pl ?? 0)}`, sgn(ov.pl)],
    ["Profit factor", ov.profit_factor == null ? "—" : num(ov.profit_factor, 2),
     "gross wins ÷ gross losses", sgn((ov.profit_factor ?? 1) - 1)],
    ["Avg CLV", ov.avg_clv == null ? "—" : (ov.avg_clv > 0 ? "+" : "") + ov.avg_clv + "%",
     "vs closing price", sgn(ov.avg_clv)],
  ];
  cards.forEach(([a, b, c, cls]) => {
    const n = el("div", "kpi");
    n.append(el("div", "k", esc(a)), el("div", "v " + (cls || ""), esc(String(b))),
             el("div", "n", esc(c)));
    k.append(n);
  });
}

/* ------------------------------------------------------------------ tabs */
const TABS = [["slate", "Slate"], ["bets", "Best Bets"], ["matchups", "Matchups"],
              ["sim", "Simulator"], ["mine", "My Ledger"], ["ratings", "Power Ratings"],
              ["accuracy", "Accuracy"], ["model", "Model"]];
function renderTabs() {
  const n = $("#tabs"); n.innerHTML = "";
  TABS.forEach(([id, label]) => {
    const b = el("button", S.tab === id ? "on" : "", esc(label));
    b.onclick = () => { S.tab = id; renderTabs(); renderView(); syncURL(); };
    n.append(b);
  });
}
function syncURL() {
  const u = new URL(location);
  u.searchParams.set("tab", S.tab);
  if (S.date) u.searchParams.set("date", S.date);
  history.replaceState(null, "", u);
}

/* ------------------------------------------------------------ game cards */
const READY_LABEL = { SET: "ready", PRICED: "priced", EARLY: "no prices yet",
                     PENCIL: "starter TBA", LIVE: "live" };

function readyChip(g) {
  const r = g.readiness;
  if (!r) return "";
  return `<span class="chip rd-${esc(r)}" title="${esc(g.readiness_note || "")}">${
    esc(READY_LABEL[r] || r)}</span>`;
}

function verdictBlock(g) {
  const v = g.verdict;
  if (!v) return "";
  return `<div class="verdict"><span class="act act-${esc(v.action)}">${esc(v.action)}</span>
    <span>${esc(v.text)}</span></div>`;
}

function weatherChip(w) {
  if (!w) return "";
  if (w.roof_closed) return `<span class="chip">roof closed</span>`;
  if (!w.ok) return `<span class="chip">weather n/a</span>`;
  const cls = Math.abs(w.applied_pct) >= 2 ? "warnc" : "";
  const wind = w.wind_component == null ? "" :
    ` · ${Math.abs(w.wind_component).toFixed(0)}mph ${w.wind_component > 0 ? "out" : "in"}`;
  const rain = w.precip >= 0.4 ? ` · ${Math.round(w.precip * 100)}% rain` : "";
  return `<span class="chip ${cls}">${Math.round(w.temp_f)}°F${wind}${rain} · ${
    w.applied_pct > 0 ? "+" : ""}${w.applied_pct}% runs</span>`;
}

function distBars(hist, marketTotal) {
  if (!hist || !hist.length) return "";
  const max = Math.max(...hist) || 1;
  const bars = hist.map((v, i) => {
    const h = Math.max(2, Math.round((v / max) * 100));
    const hot = v / max > 0.55 ? " hot" : "";
    const mark = marketTotal != null && Math.round(marketTotal) === i ? " line" : "";
    return `<span class="${hot}${mark}" style="height:${h}%" title="${i} runs: ${
      (v / hist.reduce((a, b) => a + b, 0) * 100).toFixed(1)}%"></span>`;
  }).join("");
  return `<div class="dist">${bars}</div>
    <div class="axis"><span>0 runs</span><span>total runs scored</span><span>22+</span></div>`;
}

function spBlock(sp, team, pen) {
  pen = pen || {};
  if (!sp) return "";
  const posted = sp.posted && sp.era != null;
  return `<div class="sp">
    <div class="team">${esc(team)} starter</div>
    <div class="who">${esc(sp.name || "TBA")}${sp.hand ? ` <span class="team">${esc(sp.hand)}HP</span>` : ""}</div>
    <div class="line">${posted ? `
      ${sp.rest != null && sp.rest <= 6 ? `<span>rest <b>${sp.rest}d</b></span>` : ""}
      <span>ERA <b>${num(sp.era)}</b></span>
      <span>WHIP <b>${num(sp.whip)}</b></span>
      <span>K/9 <b>${num(sp.k9, 1)}</b></span>
      <span>BB/9 <b>${num(sp.bb9, 1)}</b></span>
      <span>IP <b>${sp.ip ?? "—"}</b></span>
      <span>~<b>${sp.bf_per_start ?? "—"}</b> BF/start</span>`
      : `<span>no season line — modeled as a bullpen game</span>`}
      <span>pen ERA <b>${num(pen.era)}</b></span>
      ${(pen.unavailable || []).length
        ? `<span class="badc">${pen.unavailable.length} arm(s) unavailable</span>` : ""}
      ${(pen.tired || []).length
        ? `<span class="warnc">${pen.tired.length} worked</span>` : ""}
    </div>
    ${(pen.unavailable || []).length ? `<div class="sup" style="margin-top:4px">
      Down: ${(pen.unavailable || []).map(a => esc(a.name) + " (" + esc(a.why) + ")").join(", ")}
    </div>` : ""}</div>`;
}

/* The edge meter is a magnitude encoding, so it is one hue on a fixed scale
   rather than four tier colors: gold and green sit too close together for a
   red-green colorblind reader to separate on a 7px bar. The tier is carried by
   its text badge, which is unambiguous, and the ticks show where the tier
   boundaries fall so the bar is readable on its own terms. */
function tiers() {
  const st = (S.index || {}).settings || {};
  return { best: st.tier_best ?? 0.035, good: st.tier_good ?? 0.025,
           lean: st.tier_lean ?? 0.012, ceiling: st.edge_ceiling ?? 0.055 };
}

function edgeMeter(edge) {
  const t = tiers();
  const w = Math.min(Math.abs(edge) / t.ceiling, 1) * 100;
  const ticks = [t.lean, t.good, t.best]
    .map(v => `<u style="left:${(v / t.ceiling * 100).toFixed(1)}%"></u>`).join("");
  const cls = edge < 0 ? "neg" : "";
  const title = `${(edge * 100).toFixed(2)}% edge on a scale to ` +
                `${(t.ceiling * 100).toFixed(1)}% (ticks: lean ${(t.lean * 100).toFixed(1)}, ` +
                `good ${(t.good * 100).toFixed(1)}, best ${(t.best * 100).toFixed(1)})`;
  return `<span class="meter ${w < 1 ? "zero" : ""}" title="${esc(title)}" aria-hidden="true">
    <i class="${cls}" style="width:${w.toFixed(1)}%"></i>${ticks}</span>`;
}

function scaleLegend() {
  const t = tiers();
  const p = v => (v * 100).toFixed(1).replace(/\.0$/, "");
  return `<div class="scalebar" role="img"
      aria-label="Edge scale: pass below ${p(t.lean)} percent, lean to ${p(t.good)}, good to ${p(t.best)}, best bet above ${p(t.best)}">
    <div class="z-pass"><span class="sw"></span>PASS &lt; ${p(t.lean)}%</div>
    <div class="z-lean"><span class="sw"></span>LEAN ${p(t.lean)}–${p(t.good)}%</div>
    <div class="z-good"><span class="sw"></span>GOOD ${p(t.good)}–${p(t.best)}%</div>
    <div class="z-best"><span class="sw"></span>BEST BET ≥ ${p(t.best)}%</div>
  </div>`;
}

/* --------------------------------------------------------- add to ledger -- */
const ADDABLE = new Set(["BEST BET", "GOOD", "LEAN"]);

function inMine(gamePk, market, selection) {
  return S.mine.some(e => String(e.gamePk) === String(gamePk)
    && e.market === market && e.selection === selection);
}

function addBtn(g, b) {
  if (!L) return "";
  if (!ADDABLE.has(b.tier))
    return `<button class="add" disabled title="The model passed on this one">–</button>`;
  const on = inMine(g.gamePk, b.market, b.selection);
  const id = `${g.gamePk}|${b.market}|${b.selection}`;
  return `<button class="add ${on ? "on" : ""}" data-bet="${esc(id)}"
    title="${on ? "Remove from your ledger" : "Add to your ledger at " + b.price_txt}">
    ${on ? "✓ In ledger" : "+ Ledger"}</button>`;
}

function findBet(id) {
  const [pk, market, selection] = String(id).split("|");
  for (const g of ((S.slate || {}).games || [])) {
    if (String(g.gamePk) !== pk) continue;
    for (const b of (g.bets || []))
      if (b.market === market && b.selection === selection) return { g, b };
  }
  return null;
}

function toggleBet(id) {
  const hit = findBet(id);
  if (!hit || !L) return;
  const { g, b } = hit;
  if (inMine(g.gamePk, b.market, b.selection)) {
    S.mine = S.mine.filter(e => L.keyOf(e) !== id);
    persistMine();
    toast(`Removed <b>${esc(b.label)}</b> from your ledger.`);
  } else {
    const rec = sized(g, b);
    const entry = L.entryFrom(g, b, rec.stake > 0 ? rec.stake : 1);
    S.mine = S.mine.concat([entry]);
    persistMine();
    toast(`Added <b>${esc(b.label)}</b> at ${esc(b.price_txt)} for ${money(entry.stake)}` +
          ` — change the stake in My Ledger.`);
  }
  renderKPIs();
  renderView();
}

function persistMine() {
  if (!L) return;
  S.storageOK = L.save(S.mine);
  if (!S.storageOK)
    toast("Could not save to this browser — export your ledger to keep it.");
}

let toastTimer = null;
function toast(html) {
  document.querySelectorAll(".toast").forEach(n => n.remove());
  const n = el("div", "toast", html);
  n.setAttribute("role", "status");
  document.body.append(n);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => n.remove(), 4200);
}

/* Every book in the feed, exactly as it came back. This exists because a price
   with no name attached is indistinguishable from a made-up one - and this
   model did once invent -110 for run lines and totals nobody had priced.
   Open it and the number on the card is somewhere in this table, or the model
   has a bug worth reporting. */
function boardTable(o) {
  const rows = (o.board || []).filter(Boolean);
  if (!rows.length) return "";
  const p = v => (v == null ? "—" : (v > 0 ? "+" : "") + Math.round(v));
  const pref = (o.preferred_book || "").toLowerCase();
  const body = rows.map(r => `<tr${r.book && r.book.toLowerCase() === pref ? ' class="mine"' : ""}>
      <td data-primary>${esc(r.book)}${
        r.book && r.book.toLowerCase() === pref ? ' <span class="sup">yours</span>' : ""}</td>
      <td class="num">${p(r.ml_away)}</td><td class="num">${p(r.ml_home)}</td>
      <td class="num">${r.rl_line == null ? "—" : (r.rl_line > 0 ? "+" : "") + r.rl_line}</td>
      <td class="num">${p(r.rl_home)}</td><td class="num">${p(r.rl_away)}</td>
      <td class="num">${r.total == null ? "—" : r.total}</td>
      <td class="num">${p(r.over)}</td><td class="num">${p(r.under)}</td></tr>`).join("");
  return `<details class="board"><summary>Every book's number for this game (${rows.length})</summary>
    <div class="scroll"><table class="rt"><thead><tr><th>Book</th>
      <th class="num">ML away</th><th class="num">ML home</th>
      <th class="num">RL</th><th class="num">RL home</th><th class="num">RL away</th>
      <th class="num">Total</th><th class="num">Over</th><th class="num">Under</th>
    </tr></thead><tbody>${body}</tbody></table></div>
    <div class="note">A dash means that book has not posted that market yet. The model
      never fills a dash in with a number - it prices its own fair line instead and
      does not bet the market.${o.fetched_at
        ? ` Pulled ${esc(new Date(o.fetched_at).toLocaleString())}.` : ""}</div>
  </details>`;
}

function betRows(bets, g) {
  if (!bets || !bets.length) return `<div class="note">No prices available for this game yet.</div>`;
  // Qualified markets first and passed ones folded away. On a phone the full
  // list ran three screens per game, and the rows the model rejected were the
  // ones taking up the room.
  const live = bets.filter(b => b.tier !== "PASS");
  const dead = bets.filter(b => b.tier === "PASS");
  const table = rows => `<div class="scroll"><table class="rt">
    <thead><tr><th>Market</th><th>Selection</th><th class="num">Book price</th>
      <th class="num">Fair (model)</th><th class="num">Model</th>
      <th class="num hide-sm">Market</th>
      <th class="num">Edge</th><th class="hide-sm">Scale</th><th>Tier</th>
      <th class="num">Stake</th><th>Track</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
  const render = list => list.map(b => {
    const rec = sized(g, b);
    const stake = rec.stake > 0
      ? `<b>${money(rec.stake)}</b><br><span class="sup">to win ${money(rec.to_win)}</span>`
      : (b.suppressed ? `<span class="sup">${esc(b.suppressed)}</span>` : "—");
    const why = (b.lock_fails && b.lock_fails.length && b.tier !== "BEST BET")
      ? `<br><span class="sup">not a lock: ${esc(b.lock_fails[0])}</span>` : "";
    return `<tr>
      <td>${esc(b.market)}</td>
      <td data-primary>${esc(b.label)}${why}</td>
      <td class="num">${esc(b.price_txt)}${
        b.book ? `<br><span class="sup">${esc(b.book)}</span>` : ""}${
        b.best_price_txt && b.best_price !== b.price
          ? `<br><span class="sup better">${esc(b.best_price_txt)} at ${esc(b.best_book)}</span>` : ""}</td>
      <td class="num">${esc(b.fair_price)}<br><span class="sup">model</span></td>
      <td class="num">${pct(b.p_final)}</td>
      <td class="num hide-sm">${b.p_market == null ? "—" : pct(b.p_market)}</td>
      <td class="num ${sgn(b.edge)}">${b.edge_pct > 0 ? "+" : ""}${num(b.edge_pct, 2)}%
        ${b.edge_price_pct ? `<br><span class="sup">${b.edge_real_pct > 0 ? "+" : ""}${
          num(b.edge_real_pct, 2)}% at this price</span>` : ""}</td>
      <td class="hide-sm">${edgeMeter(b.edge)}</td>
      <td><span class="tier ${tierCls(b.tier)}">${esc(b.tier)}</span></td>
      <td class="num">${stake}</td>
      <td data-trail>${addBtn(g, b)}</td></tr>`;
  }).join("");
  return table(render(live))
    + (dead.length ? `<details><summary>Show the ${dead.length} market(s) the model
       passed on</summary>${table(render(dead))}</details>` : "");
}

function lineupTable(lu, team) {
  if (!lu || !lu.length) return "";
  return `<table><thead><tr><th>${esc(team)}</th><th class="hide-sm">Pos</th><th>B</th>
    <th class="num hide-sm">PA</th><th class="num">AVG</th><th class="num">HR</th><th class="num">OPS</th></tr></thead>
    <tbody>${lu.map((b, i) => `<tr><td>${i + 1}. ${esc(b.name)}</td><td class="hide-sm">${esc(b.pos)}</td>
      <td>${esc(b.bats)}</td><td class="num hide-sm">${b.pa}</td>
      <td class="num">${b.avg ? Number(b.avg).toFixed(3).replace(/^0/, "") : "—"}</td>
      <td class="num">${b.hr}</td>
      <td class="num">${b.ops ? Number(b.ops).toFixed(3).replace(/^0/, "") : "—"}</td></tr>`).join("")}
    </tbody></table>`;
}

function blendNote(s, g) {
  const fin = s.p_home_final ?? s.p_home;
  if (s.p_sim_home == null || Math.abs(s.p_sim_home - fin) < 0.01)
    return `<span>model price, after the market blend</span>`;
  return `<span>raw simulation had ${esc(g.away)} ${pct(s.p_sim_away)} · ` +
         `${esc(g.home)} ${pct(s.p_sim_home)} — shown above after the market blend</span>`;
}

/* The one line that answers "is there a bet here" without opening anything. */
function bestChip(g) {
  const b = g.best;
  if (!b || b.tier === "PASS")
    return `<div class="glance-bet"><span class="tier pass">NO PLAY</span>
      <span class="sup">market is priced where the model is</span>
      ${g.sim_inputs ? `<button class="add" data-sim-open="${g.gamePk}"
        title="Open this game in the simulator">Simulate</button>` : ""}</div>`;
  const rec = sized(g, b);
  return `<div class="glance-bet">
    <span class="tier ${tierCls(b.tier)}">${esc(b.tier)}</span>
    <b>${esc(b.label)}</b> <span class="mono">${esc(b.price_txt)}</span>
    <span class="${sgn(b.edge)}">${b.edge_pct > 0 ? "+" : ""}${num(b.edge_pct, 2)}%</span>
    ${rec.stake > 0 ? `<span class="sup">${money(rec.stake)}</span>` : ""}
    ${addBtn(g, b)}
    ${g.sim_inputs ? `<button class="add" data-sim-open="${g.gamePk}"
      title="Open this game in the simulator">Simulate</button>` : ""}</div>`;
}

/* Markets nobody publishes a free price for. The simulation produces them
   anyway, so the fair number is printed and you can shop it by hand. */
function derivedBlock(g) {
  const d = g.derived, s = g.sim;
  if (!d) return "";
  const tt = d.team_totals || {};
  const row = (t) => tt[t] ? `<tr>
      <td data-primary>${esc(t)} team total</td>
      <td>${tt[t].line}</td>
      <td class="num">${esc(tt[t].over)}</td>
      <td class="num">${esc(tt[t].under)}</td>
      <td class="num">${num(tt[t].mean)}</td>
      <td class="num">${d.shutout ? num(d.shutout[t], 1) + "%" : "—"}</td></tr>` : "";
  return `<div class="scroll"><table class="rt">
    <thead><tr><th>Market</th><th>Line</th><th class="num">Over / Yes</th>
      <th class="num">Under / No</th><th class="num">Projected</th>
      <th class="num">Shutout</th></tr></thead>
    <tbody>
      <tr><td data-primary>First 5 innings</td>
        <td>${g.f5_fair.total}</td>
        <td class="num">${esc(g.f5_fair.away)} ${esc(g.away)}</td>
        <td class="num">${esc(g.f5_fair.home)} ${esc(g.home)}</td>
        <td class="num">${num(s.mean_f5_total)}</td>
        <td class="num">tie ${g.f5_fair.tie_pct}%</td></tr>
      <tr><td data-primary>No run in the 1st</td>
        <td>NRFI</td>
        <td class="num">${esc(d.nrfi.yes)}</td>
        <td class="num">${esc(d.nrfi.no)}</td>
        <td class="num">${num(d.nrfi.mean_runs)}</td>
        <td class="num">${num(d.nrfi.yes_pct, 1)}%</td></tr>
      ${row(g.away)}${row(g.home)}
    </tbody></table></div>`;
}

function gameCard(g) {
  const s = g.sim, o = g.odds || {};
  const pa = s.p_away_final ?? s.p_away, ph = s.p_home_final ?? s.p_home;
  const card = el("div", "card");
  const started = g.abstract === "Final" || g.abstract === "Live";
  const scoreChip = g.away_score != null && started
    ? `<span class="chip ok">${g.away} ${g.away_score} – ${g.home_score} ${g.home} · ${esc(g.status)}</span>` : "";

  card.innerHTML = `
  <div class="hd">
    <div class="match">${esc(g.away)} <span class="at">@</span> ${esc(g.home)}</div>
    <div class="meta">
      ${scoreChip}
      <span class="chip">${timeET(g.start)}</span>
      <span class="chip">${esc(g.venue)} · ${g.park.run}/${g.park.hr}</span>
      ${weatherChip(g.weather)}
      <span class="chip ${g.lineups_confirmed ? "ok" : ""}">${
        g.lineups_confirmed ? "lineups confirmed" : "lineups projected"}</span>
      ${readyChip(g)}
      ${o.book ? (o.espn_id
        ? `<a class="chip" target="_blank" rel="noopener"
             href="https://www.espn.com/mlb/game/_/gameId/${esc(o.espn_id)}"
             title="Open this game on ESPN and check the price yourself">${esc(o.book)} ↗</a>`
        : `<span class="chip">${esc(o.book)}</span>`)
        : `<span class="chip warnc">no price</span>`}
      ${o.preferred_book && (o.books || []).some(
          b => (b || "").toLowerCase() === o.preferred_book.toLowerCase())
        ? `<span class="chip ok" title="Prices on this card are ${esc(o.preferred_book)}'s
             wherever it has posted one">pricing ${esc(o.preferred_book)}</span>`
        : (o.preferred_book
            ? `<span class="chip warnc" title="${esc(o.preferred_book)} is not in the feed for
                 this game, so you are seeing the best price on the board">no ${esc(
                 o.preferred_book)} price</span>` : "")}
    </div>
  </div>
  <div class="body">
    <div class="glance">
      <div class="score">${num(s.mean_away)} <small>–</small> ${num(s.mean_home)}
        <small>&nbsp;proj</small></div>
      <div class="glance-mid">
        <div class="lab"><span>${esc(g.away)} ${pct(pa)} (${esc(g.model_line.away)})</span>
          <span>total ${num(s.mean_total)}${o.total != null ? ` vs ${o.total}` : ""}</span>
          <span>${esc(g.model_line.home)} ${pct(ph)} ${esc(g.home)}</span></div>
        <div class="bar"><i class="a" style="width:${(pa * 100).toFixed(1)}%"></i>
          <i class="h" style="width:${(ph * 100).toFixed(1)}%"></i></div>
        <div class="lab sub">${blendNote(s, g)}</div>
      </div>
      ${bestChip(g)}
    </div>
    ${verdictBlock(g)}

    <details class="gd" open>
    <summary class="gsum">Full breakdown — starters, run distribution, every market</summary>
    <div class="grid2">
      ${spBlock(g.away_sp, g.away, g.away_pen)}
      ${spBlock(g.home_sp, g.home, g.home_pen)}
    </div>

    ${distBars(g.hist, o.total)}

    ${betRows(g.bets, g)}
    ${boardTable(o)}

    <div class="why">${esc(g.rationale)}</div>

    <details><summary>Derived markets, model line and lineups</summary>
      ${derivedBlock(g)}
      <div class="note">
        Full-game fair total <b>${g.model_line.total}</b>, market
        <b>${o.total ?? "—"}</b>. Simulation standard error ±${(s.se * 100).toFixed(2)}%.
        ${g.defense ? `Defensive efficiency ${esc(g.away)} ${num(g.defense[g.away], 3)} ·
          ${esc(g.home)} ${num(g.defense[g.home], 3)} (league ${num(g.defense.league, 3)}).` : ""}
      </div>
      <div class="grid2" style="margin-top:8px">
        <div class="scroll">${lineupTable(g.away_lineup, g.away)}</div>
        <div class="scroll">${lineupTable(g.home_lineup, g.home)}</div>
      </div>
    </details>
    </details>
  </div>`;
  return card;
}

/* ----------------------------------------------------------------- views */
/* The whole point of the page in one card: what to bet, what to lean on, and
   what the model has no opinion about. Everything below it is the working. */
/* Why there is nothing to bet, honestly. It used to always say "the market is
   priced where the model is", which is only one of four possible reasons and
   the wrong one on every future date. */
function noBetReason() {
  const slate = S.slate || {};
  const games = slate.games || [];
  const out = slate.days_out || 0;
  const maxOut = (S.index?.settings?.stake_max_days_out) ?? 1;
  if (out > maxOut)
    return `This slate is ${out} days out. The model prices it and publishes fair
      lines, but it does not size stakes more than ${maxOut === 1 ? "a day" : maxOut + " days"}
      ahead, because the number will move long before you could take it.`;
  const waiting = games.filter(g => g.readiness === "EARLY" || g.readiness === "PENCIL").length;
  if (waiting >= Math.max(1, games.length * 0.5))
    return `${waiting} of ${games.length} games are still waiting on prices or a named
      starter, so there is nothing to size yet. Bets appear through the day as books
      post numbers and lineups land.`;
  const health = slate.odds_health || {};
  if (health.priced === 0 && games.length)
    return `No prices came back from the feed for this date, so there is nothing to
      compare the model against. Fair lines are still published on every card.`;
  return `That is a result, not a failure — the market is priced where the model is.`;
}

function whatToBet() {
  const games = (S.slate || {}).games || [];
  const plays = [], leans = [], watches = [];
  games.forEach(g => (g.bets || []).forEach(b => {
    const rec = sized(g, b);
    if (rec.stake > 0) plays.push({ g, b, rec });
    else if (b.tier === "BEST BET" || b.tier === "GOOD") leans.push({ g, b });
  }));
  games.forEach(g => { if (g.verdict && g.verdict.action === "WATCH") watches.push(g); });
  plays.sort((a, b) => b.rec.stake - a.rec.stake || b.b.edge - a.b.edge);
  leans.sort((a, b) => b.b.edge - a.b.edge);

  const risk = plays.reduce((s, p) => s + p.rec.stake, 0);
  const line = ({ g, b, rec }, withStake) => `<div class="playline">
    <span class="tier ${tierCls(b.tier)}">${esc(b.tier)}</span>
    <span class="big">${esc(b.label)}</span>
    <span>${esc(b.price_txt)}</span>
    ${b.book ? `<span class="sup">${esc(b.book)}</span>` : ""}
    <span class="${sgn(b.edge)}">${b.edge_pct > 0 ? "+" : ""}${num(b.edge_pct, 2)}%</span>
    <span class="sup">${esc(g.away)}@${esc(g.home)} ${timeET(g.start)}</span>
    ${withStake ? `<span class="amt">${money(rec.stake)} → ${money(rec.to_win)}</span>`
                : `<span class="amt sup">${esc(b.suppressed
                     || (b.lock_fails || [])[0] || "no stake")}</span>`}
    ${addBtn(g, b)}</div>`;

  const v = el("div", "card today");
  v.innerHTML = `<div class="hd"><div class="match">What to bet${
      S.date === ((S.index || {}).latest) ? " today" : ` — ${esc(S.date || "")}`}</div>
    <div class="meta">
      <span class="chip ${plays.length ? "ok" : ""}">${plays.length} bet(s)</span>
      <span class="chip">${money(risk)} at risk</span>
      <span class="chip">${games.length} games</span></div></div>
  <div class="body">
    ${plays.length ? plays.map(p => line(p, true)).join("")
      : `<div class="note"><b>Nothing to bet.</b> ${noBetReason()} ${
           leans.length ? "The closest calls are below." : ""}</div>`}
    ${leans.length ? `<details${plays.length ? "" : " open"}><summary>
       ${leans.length} number(s) the model likes but will not stake</summary>
       ${leans.slice(0, 8).map(p => line(p, false)).join("")}</details>` : ""}
    ${watches.length ? `<div class="note">${watches.length} game(s) not priced yet —
       fair lines are published on each card, so you can shop them when the number lands.</div>` : ""}
  </div>`;
  return v;
}

/* Say plainly when the page is not showing what the viewer asked for. */
function freshnessBanner() {
  const age = SCH ? SCH.ageHours((S.index || {}).generated_at) : null;
  const bits = [];
  if (S.pick && S.pick.reason === "behind" && S.date !== todayET())
    bits.push(`Today's slate (${todayET()}) has not been built yet, so this is the most
      recent one. The build runs through the night; it should appear shortly.`);
  if (age != null && age > 8)
    bits.push(`The feed was last built ${age.toFixed(0)} hours ago — the scheduled job may be
      failing. Check the Actions tab in the repository.`);
  if (!bits.length) return null;
  return el("div", "flag", bits.map(esc).join(" "));
}

function viewSlate() {
  const v = el("div");
  const games = (S.slate || {}).games || [];
  const pf = (S.slate || {}).portfolio || {};
  const fresh = freshnessBanner();
  if (fresh) v.append(fresh);
  if (pf.divergence_flag)
    v.append(el("div", "flag",
      `Slate-wide divergence: on a typical game the model is ${pct(pf.median_gap, 1)} away from ` +
      `the market, against a ${pct((S.index?.settings?.divergence_gap) ?? 0.05, 1)} threshold. ` +
      `A model anchored to the market should normally sit within a point or two, so a gap this ` +
      `size across ${pf.priced_games} priced games points at an input being wrong — stale prices, ` +
      `missing starters — rather than at the market being wrong. Treat today's edges with suspicion.`));
  if (!games.length) {
    if (!S.slate || !S.slate.generated_at)
      return el("div", "empty",
        "No feed for this date yet. Run the “Build MLB slate” workflow in the Actions tab " +
        "(or `python -m pipeline.build` locally) and the slate will appear here.");
    return el("div", "empty", "No games scheduled for this date.");
  }
  const notes = [];
  const plan = currentPlan();
  if (pf.correlated_suppressed) notes.push(`${pf.correlated_suppressed} correlated bet(s) suppressed`);
  if (pf.best_downgraded) notes.push(`${pf.best_downgraded} best bet(s) downgraded by the slate cap`);
  if (pf.plays_trimmed) notes.push(`${pf.plays_trimmed} play(s) trimmed to the daily limit`);
  if (plan.exposure_scaled) notes.push(`your stakes scaled ×${plan.scale_factor} to respect the exposure cap`);
  if (notes.length) v.append(el("div", "note", "Portfolio rules: " + notes.join(" · ") + "."));
  const oh = (S.slate || {}).odds_health;
  if (oh && oh.expected_games)
    v.append(el("div", "note",
      `Prices: ${oh.priced} of ${oh.expected_games} games, from ${
        (oh.books || []).join(", ") || "no book"}${
        (oh.sources || []).length ? ` via ESPN ${oh.sources.join(" + ")}` : ""}. ` +
      `Every price on this page came from that feed — nothing is estimated. ` +
      `Tap the book chip on a card to check the number against ESPN.`));
  v.append(whatToBet());
  v.append(el("div", "", scaleLegend()));
  games.forEach(g => v.append(gameCard(g)));
  return v;
}

function viewBets() {
  const games = (S.slate || {}).games || [];
  const plays = [];
  games.forEach(g => (g.bets || []).forEach(b => {
    const rec = sized(g, b);
    if (rec.stake > 0 || b.tier === "BEST BET") plays.push({ g, b, rec });
  }));
  plays.sort((a, b) => b.b.edge - a.b.edge);
  if (!plays.length) return el("div", "empty",
    "No qualifying plays on this slate. That is a result, not a failure — " +
    "the market is priced where the model is.");
  const v = el("div", "card");
  const staked = plays.filter(p => p.rec.stake > 0).length;
  const flagged = plays.length - staked;
  v.innerHTML = `<div class="hd"><div class="match">Today's card</div>
    <div class="meta"><span class="chip">${staked} staked</span>
    ${flagged ? `<span class="chip">${flagged} flagged, no stake</span>` : ""}
    <span class="chip">${money(plays.reduce((a, p) => a + p.rec.stake, 0))} at risk</span></div></div>
  <div class="body">${scaleLegend()}<div class="scroll"><table class="rt">
    <thead><tr><th>Game</th><th>Bet</th><th class="num">Price</th><th class="num">Fair</th>
      <th class="num">Model</th><th class="num">Edge</th><th class="hide-sm">Scale</th><th>Tier</th>
      <th class="num">Stake</th><th class="num">To win</th><th>Start</th>
      <th>Track</th></tr></thead>
    <tbody>${plays.map(({ g, b, rec }) => `<tr>
      <td data-primary>${esc(g.away)}@${esc(g.home)} · ${esc(b.label)}</td>
      <td class="hide-sm">${esc(b.label)}</td>
      <td class="num">${esc(b.price_txt)}${
        b.book ? `<br><span class="sup">${esc(b.book)}</span>` : ""}${
        b.best_price_txt && b.best_price !== b.price
          ? `<br><span class="sup better">${esc(b.best_price_txt)} at ${esc(b.best_book)}</span>` : ""}</td>
      <td class="num">${esc(b.fair_price)}<br><span class="sup">model</span></td>
      <td class="num">${pct(b.p_final)}</td>
      <td class="num ${sgn(b.edge)}">+${num(b.edge_pct, 2)}%</td>
      <td class="hide-sm">${edgeMeter(b.edge)}</td>
      <td><span class="tier ${tierCls(b.tier)}">${esc(b.tier)}</span></td>
      <td class="num">${rec.stake > 0 ? money(rec.stake)
        : `<span class="sup">${esc(b.suppressed || "no stake")}</span>`}</td>
      <td class="num">${rec.stake > 0 ? money(rec.to_win) : "—"}</td>
      <td>${timeET(g.start)}</td>
      <td data-trail>${addBtn(g, b)}</td></tr>`).join("")}</tbody></table></div></div>`;
  return v;
}

function viewMatchups() {
  const games = (S.slate || {}).games || [];
  if (!games.length) return el("div", "empty", "No games to compare on this date.");
  const rank = {};
  (((S.ratings || {}).teams) || []).forEach(r => { rank[r.team] = r; });

  const row = g => {
    const s = g.sim, o = g.odds || {}, b = g.best;
    const ra = rank[g.away] || {}, rh = rank[g.home] || {};
    const wx = g.weather || {};
    const wxTxt = wx.roof_closed ? "roof"
      : (wx.ok ? `${Math.round(wx.temp_f)}°F ${wx.applied_pct > 0 ? "+" : ""}${wx.applied_pct}%` : "—");
    return `<tr>
      <td class="t" data-primary>${esc(g.away)} <span class="split">@</span> ${esc(g.home)}</td>
      <td>${timeET(g.start)}</td>
      <td class="num">${ra.rank ?? "—"} <span class="split">/</span> ${rh.rank ?? "—"}</td>
      <td class="num">${num(ra.true_wpct, 3).replace(/^0/, "")} <span class="split">/</span> ${num(rh.true_wpct, 3).replace(/^0/, "")}</td>
      <td>${esc((g.away_sp || {}).name || "TBA")} <span class="split">/</span> ${esc((g.home_sp || {}).name || "TBA")}</td>
      <td class="num">${num((g.away_sp || {}).era)} <span class="split">/</span> ${num((g.home_sp || {}).era)}</td>
      <td class="num">${num((g.away_pen || {}).era)} <span class="split">/</span> ${num((g.home_pen || {}).era)}</td>
      <td class="num">${g.park.run}<span class="split">/</span>${g.park.hr}</td>
      <td>${esc(wxTxt)}</td>
      <td class="num">${num(s.mean_away)}<span class="split">–</span>${num(s.mean_home)}</td>
      <td class="num">${num(s.mean_total)} <span class="split">vs</span> ${o.total ?? "—"}</td>
      <td class="num">${pct(s.p_away_final ?? s.p_away, 0)}<span class="split">/</span>${pct(s.p_home_final ?? s.p_home, 0)}</td>
      <td>${b ? esc(b.label) : "—"}</td>
      <td class="num ${b ? sgn(b.edge) : ""}">${b ? (b.edge_pct > 0 ? "+" : "") + num(b.edge_pct, 2) + "%" : "—"}</td>
      <td class="hide-sm">${b ? edgeMeter(b.edge) : ""}</td>
      <td>${b ? `<span class="tier ${tierCls(b.tier)}">${esc(b.tier)}</span>` : ""}</td>
      <td data-trail>${b ? addBtn(g, b) : ""}</td></tr>`;
  };

  const v = el("div", "card");
  v.innerHTML = `<div class="hd"><div class="match">Matchups</div>
    <div class="meta"><span class="chip">${games.length} games</span>
      <span class="chip">${esc(S.date || "")}</span></div></div>
  <div class="body">
    <div class="note">Every game on one line, away value first. The rating columns come from
      the power ratings; the last four are the model's best number on that game.</div>
    ${scaleLegend()}
    <div class="scroll"><table class="mu rt">
      <thead><tr><th>Game</th><th>Start</th><th class="num">Rank</th><th class="num">True W%</th>
        <th>Starters</th><th class="num">SP ERA</th><th class="num">Pen ERA</th>
        <th class="num">Park</th><th>Weather</th><th class="num">Projection</th>
        <th class="num">Total</th><th class="num">Win%</th>
        <th>Best number</th><th class="num">Edge</th><th class="hide-sm">Scale</th><th>Tier</th>
        <th>Track</th></tr></thead>
      <tbody>${games.map(row).join("")}</tbody></table></div></div>`;
  return v;
}

function mineSummary() {
  return L ? L.summarise(S.mine, currentBankroll()) : null;
}

/* ============================================================== simulator ==
   The same engine the build runs, in the page, with the inputs exposed. Change
   a starter, sit a hitter, turn the wind around, and watch the score move. The
   baseline is whatever the build published, so every number is shown as a
   difference from it rather than in isolation.                              */

const SIM = (typeof MLBSim !== "undefined") ? MLBSim : null;

const SIM_DEFAULTS = () => ({
  away: { spQuality: 1, penQuality: 1, bfMean: null, benched: [], adv: null },
  home: { spQuality: 1, penQuality: 1, bfMean: null, benched: [], adv: null },
  wx: 1, park: 1, nSims: 12000,
});

/* Surname if there is one. Falls back to a truncated full name so a
   single-word or numbered name does not render as a bare digit. */
function shortName(n) {
  const parts = String(n || "").trim().split(/\s+/);
  const last = parts[parts.length - 1];
  if (parts.length > 1 && !/^[\d.]+$/.test(last)) return last;
  return String(n || "").slice(0, 12);
}

function simGame() {
  const games = ((S.slate || {}).games || []).filter(g => g.sim_inputs);
  if (!games.length) return null;
  const want = S.simPick;
  return games.find(g => String(g.gamePk) === String(want)) || games[0];
}

function runSim(g, st) {
  const inp = g.sim_inputs;
  const wx = SIM.weatherMults(st.wx);
  const park = SIM.weatherMults(st.park);
  const side = (s, key) => Object.assign({}, st[key], {
    hr: wx.hr * park.hr, hit: wx.hit * park.hit,
    benched: st[key].benched,
    bfMean: st[key].bfMean == null ? undefined : st[key].bfMean,
    adv: st[key].adv == null ? undefined : st[key].adv,
  });
  const rlLine = (g.odds && g.odds.rl_line != null) ? g.odds.rl_line : -1.5;
  return SIM.runGame(inp, {
    away: side(inp.away, "away"), home: side(inp.home, "home"),
    marketTotal: (g.odds || {}).total, rlLine,
  }, st.nSims, inp.seed);
}

function dl(now, was, digits, invert) {
  if (was == null || !isFinite(was)) return "";
  const d = now - was;
  if (Math.abs(d) < Math.pow(10, -digits) / 2) return "";
  const cls = (invert ? -d : d) > 0 ? "pos" : "neg";
  return `<span class="delta ${cls}">${d > 0 ? "+" : ""}${d.toFixed(digits)}</span>`;
}

function simSlider(label, key, side, min, max, step, val, fmt) {
  return `<div class="row">
    <label for="sl-${side}-${key}">${esc(label)}</label>
    <input type="range" id="sl-${side}-${key}" data-sim="${side}" data-key="${key}"
      min="${min}" max="${max}" step="${step}" value="${val}">
    <span class="val">${esc(fmt(val))}</span></div>`;
}

function simControls(g, st) {
  const inp = g.sim_inputs;
  const pct1 = v => `${v > 1 ? "+" : ""}${((v - 1) * 100).toFixed(0)}%`;
  const lineup = (key) => {
    const side = inp[key];
    return `<div class="lineup-toggle">${side.bats.map((b, i) => `
      <button data-bench="${key}:${i}" class="${st[key].benched.includes(i) ? "off" : ""}"
        title="${esc(b.name)} — ${b.pa} PA. Tap to replace with a replacement-level bat.">
        ${i + 1}. ${esc(shortName(b.name))}</button>`).join("")}</div>`;
  };
  return `
  <div class="ctrl"><h4>Game</h4>
    <select class="pick" data-simpick>${((S.slate || {}).games || [])
      .filter(x => x.sim_inputs).map(x => `<option value="${x.gamePk}"
        ${String(x.gamePk) === String(g.gamePk) ? "selected" : ""}>
        ${esc(x.away)} @ ${esc(x.home)} — ${timeET(x.start)}</option>`).join("")}</select>
    <div class="simnote">${esc(g.venue)} · park ${g.park.run}/${g.park.hr}
      · ${g.lineups_confirmed ? "confirmed lineups" : "projected lineups"}</div>
  </div>

  <div class="ctrl"><h4>Pitching — ${esc(g.away)} throwing</h4>
    <div class="simnote" style="margin:0 0 6px">${esc(inp.home.opp_sp_name)}
      ${(g.away_sp || {}).era != null ? `· ${num((g.away_sp || {}).era)} ERA` : ""}</div>
    ${simSlider("Starter sharper / worse", "spQuality", "home", 0.75, 1.25, 0.01,
                st.home.spQuality, v => pct1(2 - v))}
    ${simSlider("Pulled earlier / later", "bfMean", "home", 9, 30, 0.5,
                st.home.bfMean == null ? inp.home.bf_mean : st.home.bfMean,
                v => `${(+v).toFixed(1)} BF`)}
    ${simSlider("Bullpen sharper / worse", "penQuality", "home", 0.75, 1.25, 0.01,
                st.home.penQuality, v => pct1(2 - v))}
  </div>

  <div class="ctrl"><h4>Pitching — ${esc(g.home)} throwing</h4>
    <div class="simnote" style="margin:0 0 6px">${esc(inp.away.opp_sp_name)}
      ${(g.home_sp || {}).era != null ? `· ${num((g.home_sp || {}).era)} ERA` : ""}</div>
    ${simSlider("Starter sharper / worse", "spQuality", "away", 0.75, 1.25, 0.01,
                st.away.spQuality, v => pct1(2 - v))}
    ${simSlider("Pulled earlier / later", "bfMean", "away", 9, 30, 0.5,
                st.away.bfMean == null ? inp.away.bf_mean : st.away.bfMean,
                v => `${(+v).toFixed(1)} BF`)}
    ${simSlider("Bullpen sharper / worse", "penQuality", "away", 0.75, 1.25, 0.01,
                st.away.penQuality, v => pct1(2 - v))}
  </div>

  <div class="ctrl"><h4>${esc(g.away)} lineup</h4>${lineup("away")}
    <div class="simnote">Tap a name to sit him and drop a replacement-level bat in.</div></div>
  <div class="ctrl"><h4>${esc(g.home)} lineup</h4>${lineup("home")}</div>

  <div class="ctrl"><h4>Conditions</h4>
    <div class="simnote" style="margin:0 0 6px">${
      (g.weather || {}).roof_closed ? "Roof shut in the published run."
      : (g.weather || {}).ok ? `Published: ${Math.round(g.weather.temp_f)}°F,
          ${Math.abs(g.weather.wind_component || 0).toFixed(0)}mph
          ${(g.weather.wind_component || 0) > 0 ? "out" : "in"},
          ${g.weather.applied_pct > 0 ? "+" : ""}${g.weather.applied_pct}% runs`
      : "No forecast in the published run."}</div>
    ${simSlider("Weather run environment", "wx", "global", 0.85, 1.15, 0.01,
                st.wx, v => pct1(v))}
    ${simSlider("Park run environment", "park", "global", 0.85, 1.15, 0.01,
                st.park, v => pct1(v))}
    ${simSlider("Simulations", "nSims", "global", 4000, 30000, 2000,
                st.nSims, v => `${(+v / 1000).toFixed(0)}k`)}
    <div class="row" style="margin-top:9px">
      <button class="btn" data-simreset>Reset to the published game</button></div>
  </div>`;
}

function simResults(g, st, res) {
  const base = g.sim;
  const o = g.odds || {};
  const changed = JSON.stringify(st) !== JSON.stringify(
    Object.assign(SIM_DEFAULTS(), { nSims: st.nSims }));
  const bars = (() => {
    const h = res.hist, max = Math.max(...h) || 1, n = res.n || h.reduce((a, b) => a + b, 0);
    return `<div class="dist">${h.map((v, i) => `<span class="${v / max > 0.55 ? "hot" : ""}${
      o.total != null && Math.round(o.total) === i ? " line" : ""}"
      style="height:${Math.max(2, Math.round(v / max * 100))}%"
      title="${i} runs: ${(v / n * 100).toFixed(1)}%"></span>`).join("")}</div>
      <div class="axis"><span>0 runs</span><span>total runs scored</span><span>22+</span></div>`;
  })();

  return `<div class="simout">
    <div class="simscore">${res.mean_away.toFixed(2)}${dl(res.mean_away, base.mean_away, 2)}
      <small>–</small> ${res.mean_home.toFixed(2)}${dl(res.mean_home, base.mean_home, 2)}
      <small>&nbsp;projected</small></div>
    <div class="lab" style="display:flex;justify-content:space-between;
      font-family:var(--mono);font-size:11px;color:var(--dim);margin:8px 0 4px">
      <span>${esc(g.away)} ${pct(1 - res.p_home)}</span>
      <span>total ${res.mean_total.toFixed(2)}${dl(res.mean_total, base.mean_total, 2)}${
        o.total != null ? ` vs ${o.total}` : ""}</span>
      <span>${pct(res.p_home)} ${esc(g.home)}</span></div>
    <div class="bar"><i class="a" style="width:${((1 - res.p_home) * 100).toFixed(1)}%"></i>
      <i class="h" style="width:${(res.p_home * 100).toFixed(1)}%"></i></div>
    ${bars}
    <div class="scroll"><table class="rt">
      <thead><tr><th>Market</th><th class="num">Simulated</th><th class="num">Published</th>
        <th class="num">Change</th><th class="num">Fair price</th></tr></thead>
      <tbody>
        ${simRow(`${g.home} win`, res.p_home, base.p_sim_home, true)}
        ${simRow(`${g.home} ${((g.odds || {}).rl_line ?? -1.5) > 0 ? "+" : ""}${
          (g.odds || {}).rl_line ?? -1.5}`, res.p_home_rl, base.p_home_rl, true)}
        ${o.total != null ? simRow(`Over ${o.total}`, res.p_total_over, base.p_total_over, true) : ""}
        ${simRow("First five — " + g.home, res.p_f5_home, base.p_f5_home, true)}
        ${simRow("No run in the 1st", res.p_nrfi, base.p_nrfi, true)}
        ${simRow(`${g.away} shut out`, res.p_away_shutout, base.p_away_shutout, true)}
        ${simRow(`${g.home} shut out`, res.p_home_shutout, base.p_home_shutout, true)}
      </tbody></table></div>
    <div class="simnote">${changed
      ? `Inputs changed from the published run. ${(st.nSims / 1000).toFixed(0)},000 simulations —
         about ${(res.se * 100).toFixed(2)} points of sampling noise on the win probability, so
         treat anything smaller than that as nothing.`
      : `This is the published game, re-run in your browser with the same engine and the same
         seed. Move a slider to see what changes it.`}</div>
  </div>`;
}

function simRow(label, now, was, asPct) {
  const d = (was == null) ? null : now - was;
  return `<tr><td data-primary>${esc(label)}</td>
    <td class="num">${asPct ? pct(now) : num(now)}</td>
    <td class="num">${was == null ? "—" : (asPct ? pct(was) : num(was))}</td>
    <td class="num ${d == null ? "" : sgn(d)}">${
      d == null ? "—" : (d > 0 ? "+" : "") + (d * 100).toFixed(1) + " pts"}</td>
    <td class="num">${fairFromProb(now)}</td></tr>`;
}

function fairFromProb(p) {
  if (!isFinite(p) || p <= 0 || p >= 1) return "—";
  const a = p >= 0.5 ? -100 * p / (1 - p) : 100 * (1 - p) / p;
  return (a > 0 ? "+" : "") + a.toFixed(0);
}

function viewSimulator() {
  if (!SIM) return el("div", "empty", "Simulator engine failed to load.");
  const g = simGame();
  if (!g) return el("div", "empty",
    "No simulatable games on this date yet — the inputs are published once a slate is built.");
  S.simPick = g.gamePk;
  if (!S.simState || S.simFor !== g.gamePk) {
    S.simState = SIM_DEFAULTS();
    S.simFor = g.gamePk;
  }
  const st = S.simState;
  let res;
  try {
    res = runSim(g, st);
  } catch (err) {
    return el("div", "empty", "Simulation failed: " + esc(String(err && err.message)));
  }
  const v = el("div", "card simcard");
  v.innerHTML = `<div class="hd"><div class="match">${esc(g.away)} @ ${esc(g.home)} — simulator</div>
    <div class="meta"><span class="chip">${(st.nSims / 1000).toFixed(0)}k simulations</span>
      <span class="chip">${esc(g.readiness_note || "")}</span></div></div>
    <div class="body"><div class="simgrid">
      <div id="simctl">${simControls(g, st)}</div>
      <div id="simres">${simResults(g, st, res)}</div>
    </div></div>`;
  return v;
}

function rerunSim() {
  const g = simGame();
  if (!g || !SIM) return;
  const host = $("#simres");
  if (host) host.classList.add("simbusy");
  // Let the class paint before a chunk of synchronous simulation.
  setTimeout(() => {
    const res = runSim(g, S.simState);
    if (host) { host.innerHTML = simResults(g, S.simState, res); host.classList.remove("simbusy"); }
    const ctl = $("#simctl");
    if (ctl) labelTables(ctl);
    const out = $("#simres");
    if (out) labelTables(out);
    reportHeight();
  }, 16);
}

function viewMine() {
  const v = el("div");
  if (!L) return el("div", "empty", "Ledger module failed to load.");
  if (!S.storageOK)
    v.append(el("div", "flag",
      "This browser is blocking local storage, so clicks will not survive a reload. " +
      "Use Export below to keep a copy."));

  const sum = mineSummary();
  const rows = S.mine.slice().sort((a, b) =>
    String(b.date || "").localeCompare(String(a.date || "")) ||
    String(a.away || "").localeCompare(String(b.away || "")));

  const kpi = el("div", "kpis");
  [["Your bankroll", money(sum.bankroll_now), `start ${money(currentBankroll())}`,
    sgn(sum.bankroll_now - currentBankroll())],
   ["Your record", `${sum.graded.w}-${sum.graded.l}${sum.graded.p ? "-" + sum.graded.p : ""}`,
    sum.graded.n ? pct(sum.graded.win_pct) + " win rate" : "nothing settled yet", ""],
   ["Your ROI", sum.graded.roi == null ? "—" : pct(sum.graded.roi, 1),
    sum.graded.staked ? `on ${money(sum.graded.staked)} staked` : "—", sgn(sum.graded.roi)],
   ["Your profit", money(sum.graded.pl), `${sum.overall.pending} pending`, sgn(sum.graded.pl)],
   ["Open risk", money(sum.overall.at_risk), "on unsettled bets", ""],
  ].forEach(([a, b, c, cls]) => {
    const n = el("div", "kpi");
    n.append(el("div", "k", esc(a)), el("div", "v " + (cls || ""), esc(String(b))),
             el("div", "n", esc(c)));
    kpi.append(n);
  });
  v.append(kpi);

  const card = el("div", "card");
  card.innerHTML = `<div class="hd"><div class="match">My ledger</div>
    <div class="meta"><span class="chip">${S.mine.length} bet(s)</span>
      <span class="chip">${sum.overall.pending} pending</span>
      <span class="chip">stored in this browser</span></div></div>
  <div class="body">
    <div class="tools">
      <button class="btn" data-act="export-json">Export JSON</button>
      <button class="btn" data-act="export-csv">Export CSV</button>
      <button class="btn" data-act="import">Import</button>
      <button class="btn" data-act="settle">Settle finished games</button>
      <button class="btn" data-act="clear">Clear all</button>
    </div>
    <div id="io"></div>
    ${rows.length ? `<div class="scroll"><table class="rt">
      <thead><tr><th>Date</th><th>Game</th><th>Bet</th><th>Tier</th>
        <th class="num">Price</th><th class="num">Edge</th><th class="num">Stake</th>
        <th>Result</th><th class="num">P/L</th><th class="num">Final</th>
        <th></th></tr></thead>
      <tbody>${rows.map(e => {
        const k = L.keyOf(e);
        return `<tr>
          <td data-primary>${esc(e.away)}@${esc(e.home)} · ${esc(e.label)}</td>
          <td>${esc(e.date)}</td>
          <td class="hide-sm">${esc(e.label)}</td>
          <td><span class="tier ${tierCls(e.tier)}">${esc(e.tier)}</span></td>
          <td class="num">${e.price > 0 ? "+" : ""}${e.price}</td>
          <td class="num ${sgn(e.edge)}">${e.edge == null ? "—"
            : (e.edge > 0 ? "+" : "") + num(e.edge * 100, 2) + "%"}</td>
          <td class="num"><input class="stake" type="number" step="0.5" min="0"
            value="${Number(e.stake).toFixed(2)}" data-stake="${esc(k)}"
            aria-label="Stake for ${esc(e.label)}"></td>
          <td class="${e.result === "win" ? "pos" : e.result === "loss" ? "neg" : "neu"}">${
            esc(e.result || "pending")}</td>
          <td class="num ${sgn(e.pl)}">${e.result ? money(e.pl) : "—"}</td>
          <td class="num">${esc(e.final || "—")}</td>
          <td data-trail><button class="mini" data-del="${esc(k)}"
            aria-label="Remove ${esc(e.label)}">Remove</button></td></tr>`;
      }).join("")}</tbody></table></div>`
      : `<div class="empty">Nothing here yet. Tap <b>+ Ledger</b> next to any LEAN, GOOD or
         BEST BET on the Slate, Best Bets or Matchups tab and it lands here with the price and
         the recommended stake. Change the stake after if you bet a different number.</div>`}
    ${rows.length ? `<div class="note">Bets settle automatically once the game is final and the
      next build publishes the score — or tap <b>Settle finished games</b> to do it now. Grading
      uses exactly the same rules the model grades itself with.</div>` : ""}
  </div>`;
  v.append(card);

  if (sum.graded.n) {
    const perf = el("div", "card");
    perf.innerHTML = `<div class="hd"><div class="match">Your accuracy by tier</div></div>
      <div class="body">${tierTableFrom(sum.by_tier,
        "How the tiers have actually performed for you — not for the model, for you. " +
        "Only bets you clicked are counted.")}</div>`;
    v.append(perf);
  }
  return v;
}

function tierTableFrom(by, note) {
  const keys = Object.keys(by || {}).filter(k => (by[k] || {}).n);
  if (!keys.length) return `<div class="note">Nothing settled yet.</div>`;
  return `${note ? `<div class="note">${esc(note)}</div>` : ""}<div class="scroll"><table class="rt">
    <thead><tr><th>Tier</th><th class="num">Bets</th><th class="num">W-L-P</th>
      <th class="num">Hit rate</th><th class="num">Staked</th><th class="num">P/L</th>
      <th class="num">ROI</th><th class="num">Pending</th></tr></thead>
    <tbody>${keys.map(k => { const b = by[k]; return `<tr>
      <td data-primary><b>${esc(k)}</b></td><td class="num">${b.n}</td>
      <td class="num">${b.w}-${b.l}${b.p ? "-" + b.p : ""}</td>
      <td class="num">${b.win_pct == null ? "—" : pct(b.win_pct)}</td>
      <td class="num">${b.staked ? money(b.staked) : "—"}</td>
      <td class="num ${sgn(b.pl)}">${b.staked ? money(b.pl) : "—"}</td>
      <td class="num ${sgn(b.roi)}">${b.roi == null ? "—" : pct(b.roi, 1)}</td>
      <td class="num">${b.pending || 0}</td></tr>`; }).join("")}</tbody></table></div>`;
}

function viewRatings() {
  const t = (S.ratings || {}).teams || [];
  if (!t.length) return el("div", "empty", "Power ratings have not been generated yet.");
  const v = el("div", "card");
  v.innerHTML = `<div class="hd"><div class="match">Power ratings</div>
    <div class="meta"><span class="chip">neutral park, league-average opponent</span></div></div>
  <div class="body">
   <div class="note">Every roster plays a full game — both as the home team and as the visitor —
    against one reference opponent built the same way from all 30 rosters, in a neutral park.
    This is a true-talent rating, not a standings snapshot: “luck” is how many wins the actual
    record sits above or below the simulated talent over 162 games. RS/G and RA/G are recentered so the
    league's runs scored and runs allowed balance, then scaled to the runs per game the league
    has actually produced this season. Each team keeps its distance from average; ordering,
    spread and true win percentage are untouched.</div>
   <div class="scroll"><table class="rt">
    <thead><tr><th>#</th><th>Team</th><th class="num">True W%</th><th class="num">Proj 162</th>
      <th class="num">RS/G</th><th class="num">RA/G</th><th class="num">Net</th>
      <th class="num hide-sm">Rot ERA</th><th class="num hide-sm">Pen ERA</th>
      <th class="num">Record</th><th class="num hide-sm">Diff</th><th class="num">Luck</th></tr></thead>
    <tbody>${t.map(r => `<tr>
      <td class="hide-sm">${r.rank}</td><td data-primary><b>${r.rank}. ${esc(r.team)}</b></td>
      <td class="num">${num(r.true_wpct, 3).replace(/^0/, "")}</td>
      <td class="num">${num(r.proj_162, 1)}</td>
      <td class="num">${num(r.rs_per_g)}</td><td class="num">${num(r.ra_per_g)}</td>
      <td class="num ${sgn(r.net)}">${r.net > 0 ? "+" : ""}${num(r.net)}</td>
      <td class="num hide-sm">${num(r.rotation_era)}</td><td class="num hide-sm">${num(r.bullpen_era)}</td>
      <td class="num">${r.w ?? "—"}-${r.l ?? "—"}</td>
      <td class="num hide-sm ${sgn(r.diff)}">${r.diff > 0 ? "+" : ""}${r.diff ?? "—"}</td>
      <td class="num ${sgn(r.luck)}">${r.luck == null ? "—" : (r.luck > 0 ? "+" : "") + r.luck}</td>
      </tr>`).join("")}</tbody></table></div></div>`;
  return v;
}

function viewLedger() {
  const led = (S.perf || {}).ledger || [];
  if (!led.length) return el("div", "empty",
    "The ledger fills in as games settle. Every call is recorded when it is made and graded when the game ends.");
  const rows = led.slice().reverse();
  const v = el("div", "card");
  v.innerHTML = `<div class="hd"><div class="match">Graded ledger</div>
    <div class="meta"><span class="chip">${rows.length} settled call(s)</span>
    <span class="chip">${(S.perf.open_calls || 0)} pending</span></div></div>
  <div class="body"><div class="note">Every call the model made is graded, including the ones it
    passed on. Only rows with a stake move the bankroll. Profit uses the entry price; the close
    is shown only to measure CLV.</div><div class="scroll"><table class="rt">
    <thead><tr><th>Date</th><th>Game</th><th>Bet</th><th>Tier</th><th class="num">Entry</th>
      <th class="num">Close</th>
      <th class="num">Stake</th><th>Result</th><th class="num">P/L</th>
      <th class="num">Final</th><th class="num">CLV</th></tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td data-primary>${esc(r.away)}@${esc(r.home)} · ${esc(r.label)}</td>
      <td>${esc(r.date)}</td>
      <td class="hide-sm">${esc(r.label)}</td>
      <td><span class="tier ${tierCls(r.tier)}">${esc(r.tier)}</span></td>
      <td class="num">${r.entry_price > 0 ? "+" : ""}${r.entry_price ?? "—"}</td>
      <td class="num">${r.close_price > 0 ? "+" : ""}${r.close_price ?? "—"}</td>
      <td class="num">${r.stake ? money(r.stake) : "—"}</td>
      <td class="${r.result === "win" ? "pos" : r.result === "loss" ? "neg" : "neu"}">${esc(r.result)}</td>
      <td class="num ${sgn(r.pl)}">${r.stake ? money(r.pl) : "—"}</td>
      <td class="num">${r.final_away}-${r.final_home}</td>
      <td class="num ${sgn(r.clv)}">${r.clv == null ? "—" : (r.clv > 0 ? "+" : "") + r.clv + "%"}</td>
      </tr>`).join("")}</tbody></table></div></div>`;
  return v;
}

function curveSVG(curve) {
  if (!curve || curve.length < 2) return "";
  const w = 600, h = 70, xs = w / (curve.length - 1);
  const vals = curve.map(c => c.balance);
  const lo = Math.min(...vals), hi = Math.max(...vals), rng = (hi - lo) || 1;
  const pts = curve.map((c, i) =>
    `${(i * xs).toFixed(1)},${(h - ((c.balance - lo) / rng) * (h - 8) - 4).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  return `<svg class="curve" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline fill="none" stroke="${up ? "var(--good)" : "var(--bad)"}" stroke-width="2" points="${pts}"/>
  </svg><div class="axis"><span>${money(lo)}</span><span>bankroll</span><span>${money(hi)}</span></div>`;
}

function tierTable(by, title, note) {
  const keys = Object.keys(by || {});
  if (!keys.length) return "";
  return `<div class="card"><div class="hd"><div class="match">${esc(title)}</div></div>
  <div class="body">${note ? `<div class="note">${esc(note)}</div>` : ""}<div class="scroll"><table class="rt">
    <thead><tr><th>Tier</th><th class="num">Calls</th><th class="num">W-L-P</th><th class="num">Hit rate</th>
      <th class="num">Staked</th><th class="num">P/L</th><th class="num">ROI</th><th class="num">Avg CLV</th></tr></thead>
    <tbody>${keys.map(k => { const b = by[k]; return `<tr>
      <td data-primary><b>${esc(k)}</b></td><td class="num">${b.n}</td>
      <td class="num">${b.w}-${b.l}${b.p ? "-" + b.p : ""}</td>
      <td class="num">${b.n ? pct(b.win_pct) : "—"}</td>
      <td class="num">${b.staked ? money(b.staked) : "—"}</td>
      <td class="num ${sgn(b.pl)}">${b.staked ? money(b.pl) : "—"}</td>
      <td class="num ${sgn(b.roi)}">${b.roi == null ? "—" : pct(b.roi, 1)}</td>
      <td class="num ${sgn(b.avg_clv)}">${b.avg_clv == null ? "—" : (b.avg_clv > 0 ? "+" : "") + b.avg_clv + "%"}</td>
      </tr>`; }).join("")}</tbody></table></div></div></div>`;
}

function statRow(items) {
  return `<div class="statrow">${items.map(([k, val, n, cls]) => `<div class="stat">
    <div class="k">${esc(k)}</div><div class="v ${cls || ""}">${esc(String(val))}</div>
    <div class="n">${esc(n || "")}</div></div>`).join("")}</div>`;
}

/* Every game the model called, whether or not there was a bet in it. This is
   the record that says whether it understands baseball; the bet ledger only
   says whether it found soft numbers. */
function predictionsBlock() {
  const P = S.preds;
  if (!P || !P.overall || !P.overall.n)
    return `<div class="card"><div class="hd"><div class="match">Game predictions</div></div>
      <div class="body"><div class="note">Every game gets a prediction recorded whether you
      bet it or not. They start scoring as soon as games finish.</div></div></div>`;
  const o = P.overall, vm = P.vs_market || {}, cal = P.calibration || {};
  const better = vm.market_brier != null && vm.model_brier != null
    ? vm.model_brier < vm.market_brier : null;

  // Scale to the range the bars actually live in (roughly 50-80%) so the
  // difference between predicted and actual is visible rather than a rounding
  // error at the top of a 0-100 axis.
  const relRows = P.reliability || [];
  const vals = relRows.flatMap(r => [r.predicted, r.actual]);
  const lo = Math.max(0, Math.min(...vals, 0.5) - 0.05);
  const hi = Math.min(1, Math.max(...vals, 0.6) + 0.05);
  const h = v => Math.max(3, Math.round(((v - lo) / Math.max(hi - lo, 1e-6)) * 100));
  const rel = relRows.map(r => `<div class="grp"
      title="${esc(r.bucket)}: model said ${pct(r.predicted)}, actually happened ${pct(r.actual)} over ${r.n} games">
      <span class="pred" style="height:${h(r.predicted)}%"></span>
      <span class="act" style="height:${h(r.actual)}%"></span></div>`).join("");
  const relAxis = relRows.map(r => `<div>${esc(r.bucket)}<br>${r.n}</div>`).join("");

  return `<div class="card"><div class="hd"><div class="match">Game predictions</div>
    <div class="meta"><span class="chip">${o.n} graded</span>
      <span class="chip">${P.pending || 0} pending</span></div></div>
  <div class="body">
    <div class="note">Every game on the schedule is predicted and scored — bet or not.
      This is the record of whether the model reads baseball correctly.</div>
    ${statRow([
      ["Winners called", `${o.correct}-${o.n - o.correct}`, pct(o.accuracy) + " right"],
      ["Brier score", o.brier ?? "—", "lower is better", ""],
      ["vs market", vm.market_brier == null ? "—" : (better ? "ahead" : "behind"),
       vm.market_brier == null ? "" : `market ${vm.market_brier}`,
       better === null ? "" : (better ? "pos" : "neg")],
      ["Total error", o.mae_total ?? "—", "runs, average miss"],
      ["Total bias", o.bias_total == null ? "—" : (o.bias_total > 0 ? "+" : "") + o.bias_total,
       o.bias_total > 0 ? "projecting high" : "projecting low",
       Math.abs(o.bias_total || 0) > 0.3 ? "neg" : ""],
      ["Score error", o.mae_runs ?? "—", "runs per team"],
      ["Over/under lean", o.total_lean_acc == null ? "—" : pct(o.total_lean_acc),
       `${o.total_lean_n || 0} with a market number`],
      ["Last 30", (P.last30 || {}).accuracy == null ? "—" : pct(P.last30.accuracy),
       "recent form"],
    ])}
    ${rel ? `<div class="note" style="margin-top:4px"><b>Calibration.</b> For every
      confidence bucket, what the model said against what actually happened. The pairs
      should match — a taller blue bar means the model is underselling itself, a taller
      grey bar means it is too sure.</div>
      <div class="rel">${rel}</div><div class="rel-x">${relAxis}</div>
      <div class="rel-key"><span><i class="p"></i>model said</span>
        <span><i class="a"></i>actually happened</span>
        <span>axis ${pct(lo, 0)}–${pct(hi, 0)}</span></div>` : ""}
    <div class="note"><b>Learning from it.</b> ${cal.applied
      ? `Applied: totals shifted ${cal.total_adj > 0 ? "+" : ""}${cal.total_adj} runs and
         confidence scaled ×${cal.prob_scale}, learned from ${cal.n} graded games.`
      : `Not applied yet — ${esc(cal.reason || "waiting for more games")}. Corrections switch
         on at ${cal.min_games || 150} graded games and are capped either way.`}</div>
    ${(P.confirmed_lineups || {}).n ? `<div class="scroll"><table class="rt">
      <thead><tr><th>Split</th><th class="num">Games</th><th class="num">Winners</th>
        <th class="num">Brier</th><th class="num">Total error</th></tr></thead>
      <tbody>${[["Lineups confirmed", P.confirmed_lineups],
                ["Lineups projected", P.projected_lineups],
                ["Last 100", P.last100]].filter(([, b]) => b && b.n).map(([k, b]) => `<tr>
        <td data-primary>${esc(k)}</td><td class="num">${b.n}</td>
        <td class="num">${pct(b.accuracy)}</td><td class="num">${b.brier ?? "—"}</td>
        <td class="num">${b.mae_total ?? "—"}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${(P.team_bias || []).length ? `<details><summary>Which clubs the model reads wrong</summary>
      <div class="note">Negative means the model projects them for fewer runs than they
      score.</div><div class="scroll"><table class="rt">
      <thead><tr><th>Team</th><th class="num">Games</th><th class="num">Run bias</th>
        <th class="num">Avg miss</th></tr></thead>
      <tbody>${P.team_bias.map(t => `<tr><td data-primary>${esc(t.team)}</td>
        <td class="num">${t.n}</td>
        <td class="num ${sgn(-t.bias)}">${t.bias > 0 ? "+" : ""}${t.bias}</td>
        <td class="num">${t.mae}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
    ${(P.recent || []).length ? `<details><summary>Every graded game</summary>
      <div class="scroll"><table class="rt">
      <thead><tr><th>Date</th><th>Game</th><th>Pick</th><th class="num">Conf</th>
        <th class="num">Projected</th><th class="num">Final</th><th>Result</th>
        <th class="num">Total miss</th></tr></thead>
      <tbody>${P.recent.slice().reverse().map(r => `<tr>
        <td data-primary>${esc(r.away)}@${esc(r.home)}</td>
        <td>${esc(r.date)}</td>
        <td>${esc(r.pick)}</td>
        <td class="num">${pct(r.pick_conf, 0)}</td>
        <td class="num">${num(r.proj_away)}–${num(r.proj_home)}</td>
        <td class="num">${r.final_away}–${r.final_home}</td>
        <td class="${r.correct ? "pos" : "neg"}">${r.correct ? "right" : "wrong"}</td>
        <td class="num ${Math.abs(r.err_total || 0) > 3 ? "neg" : ""}">${
          r.err_total > 0 ? "+" : ""}${num(r.err_total, 1)}</td></tr>`).join("")}
      </tbody></table></div></details>` : ""}
  </div></div>`;
}

function viewAccuracy() {
  const p = S.perf;
  const v = el("div");
  v.insertAdjacentHTML("beforeend", predictionsBlock());
  if (!p) return v;
  const cal = p.calibration || [];

  // Two different questions, kept apart on purpose: is the model any good, and
  // are you any good at using it. Answering both with one number hides which is
  // which the first time a month goes badly.
  const mine = mineSummary();
  if (mine && mine.overall.n) {
    const yours = el("div", "card");
    yours.innerHTML = `<div class="hd"><div class="match">Your bets</div>
      <div class="meta"><span class="chip">${mine.overall.n} tracked</span>
      <span class="chip">${mine.overall.pending} pending</span></div></div>
      <div class="body">${tierTableFrom(mine.by_tier,
        "Only the bets you clicked into My Ledger, at the price and stake you took.")}
        ${Object.keys(mine.by_market).length
          ? tierTableFrom(mine.by_market, "By market.") : ""}
        ${tierTableFrom({ "Last 10": mine.last10, "Last 25": mine.last25,
                          "All settled": mine.graded }, "Recent form.")}
      </div>`;
    v.append(yours);
  }

  v.append(el("div", "note",
    "Everything below is the model grading itself. Staked results are kept together so " +
    "record and ROI use the same bets; the All calls row also includes PASSes. Your own " +
    "results are the panel above."));

  const modelBlocks = el("div");
  modelBlocks.innerHTML =
    tierTable(p.placed_by_tier || p.by_tier, "Placed bets by tier",
      "Only positions that carried a stake. This keeps the record, dollars and ROI on the same denominator.") +
    tierTable(p.placed_by_market || p.by_market, "Placed bets by market", "") +
    tierTable({ "Last 20": p.last20 || {}, "Last 50": p.last50 || {},
                "All placed": p.overall || {}, "All calls": p.all_calls || {} },
      "Recent form", "") +
    `<div class="card"><div class="hd"><div class="match">Bankroll</div></div>
      <div class="body">${curveSVG(p.curve) ||
        `<div class="note">The curve appears once bets have settled.</div>`}</div></div>` +
    (cal.length ? `<div class="card"><div class="hd"><div class="match">Calibration</div></div>
      <div class="body"><div class="note">Of the things the model called at each confidence level,
      how often did they actually happen? Predicted and actual should track each other.</div>
      <div class="scroll"><table class="rt"><thead><tr><th>Model said</th><th class="num">Calls</th>
      <th class="num">Predicted</th><th class="num">Actual</th><th class="num">Gap</th></tr></thead>
      <tbody>${cal.map(c => `<tr><td data-primary>${esc(c.bucket)}</td><td class="num">${c.n}</td>
        <td class="num">${pct(c.predicted)}</td><td class="num">${pct(c.actual)}</td>
        <td class="num ${sgn(c.actual - c.predicted)}">${pct(c.actual - c.predicted)}</td>
        </tr>`).join("")}</tbody></table></div></div></div>` : "");
  v.append(modelBlocks);

  const det = el("details");
  det.append(el("summary", "", "Show every graded call the model made"));
  det.append(viewLedger());
  v.append(det);
  return v;
}

function viewModel() {
  const st = (S.index || {}).settings || {};
  const v = el("div", "card");
  v.innerHTML = `<div class="hd"><div class="match">How the number is made</div></div>
  <div class="body">
  <div class="note">
   <p><b>1. Inputs.</b> Schedule, probable starters, confirmed batting orders, season rate stats
   for every hitter and pitcher, and standings come from the MLB Stats API. Prices come from
   ESPN's public scoreboard. Weather comes from Open-Meteo at the park's coordinates for the
   hour of first pitch. Park factors and field orientation are a table in the repo you can edit.</p>

   <p><b>2. Matchup.</b> Each hitter's and pitcher's plate-appearance outcome rates are regressed
   toward league average, then combined with a multinomial odds-ratio matchup, adjusted for
   handedness, park, weather and home field. Every adjustment is a multiplier on a specific
   outcome — home runs, hits, strikeouts — never a fudge applied to the final score.</p>

   <p><b>3. Simulation.</b> The game is played ${(st.n_sims || 20000).toLocaleString()} times,
   one plate appearance at a time: base-out states, forced advances, sacrifice flies, double
   plays, a starter who tires and hands off to the bullpen, the third-time-through penalty, the
   home team skipping the ninth when ahead, walk-offs, and the extra-innings ghost runner. The
   moneyline, run line, total and first five all come out of the same distribution, so they
   cannot contradict each other.</p>

   <p><b>4. Market.</b> Book prices are de-vigged with the power method, which takes more juice
   off the favorite than off the dog — closer to the truth on a −250 baseball moneyline than
   splitting the margin evenly. The model is then pulled ${pct(st.market_blend ?? 0.40, 0)}
   of the way toward that no-vig price.</p>

   <p><b>4b. Two edges, not one.</b> The number in the Edge column is the model's
   disagreement with the market: expected value priced at the no-vig consensus of
   every book in the feed. Underneath it, where they differ, is what the bet is
   actually worth at the price you are being shown. Keeping them apart matters —
   the gap between consensus and best price is positive on <em>both</em> sides of a
   game whenever books disagree, so counting it as model edge would make every
   market look like a play. Bets qualify on the first number and are sized on the
   second.</p>

   <p><b>4c. Whose price you are looking at.</b> Every number on a card comes from a
   named sportsbook${st.preferred_book ? ` — ${esc(st.preferred_book)}'s wherever it has
   posted one, because that is the number you will see when you go to bet` : ""}. Where
   another book is offering better, it is printed underneath in green. The fold-out
   under each game lists every book in the feed, so nothing has to be taken on trust.
   A market no book has priced stays blank and is marked <em>fair (model)</em>: that is
   the model's own opinion, not an offer, and it is never bet. This paragraph exists
   because an earlier version filled missing run-line and total prices in with −110,
   which made the model measure real edges against a market that did not exist.</p>

   <p><b>5. Edge and stake.</b> Raw expected value is squashed through tanh toward a hard ceiling
   of ${pct(st.edge_ceiling ?? 0.055, 1)}. The published card defaults to ${st.kelly ?? 0.25}
   Kelly; the Staking plan control can resize the approved card to quarter, half or full Kelly
   and to your bankroll. The ${pct(st.max_stake_pct ?? 0.05, 0)} per-bet and
   ${pct(st.max_slate_exposure_pct ?? 0.15, 0)} daily caps always remain in force.
   A wild readout still produces a small bet instead of a disaster.</p>

   <p><b>6. Portfolio rules.</b> Never the moneyline and the run line on the same team. At most
   three best bets and six plays on a slate. Total exposure capped across the day. If the model
   disagrees with the market on a large share of the slate, that is flagged as a data problem
   rather than a windfall.</p>

   <p><b>7. Grading.</b> Every call is recorded with the entry price and entry line it was made at,
   then graded at that same number when the game ends — including the PASSes, which is the only
   way to know whether the thresholds are set correctly. The closing price is kept separately
   for CLV; it never changes historical profit or ROI.</p>

   <p><b>What it does not know:</b> bullpen availability from yesterday's usage, injuries that
   have not hit the lineup card, catcher framing, umpire tendencies, or a starter pitching
   hurt. Treat the number as one opinion with a spreadsheet behind it.</p>
  </div></div>`;
  return v;
}

/* Below 720px the stylesheet turns every .rt table into stacked cards, and a
   stacked cell has no column heading above it any more. Copy the heading onto
   each cell so the CSS can print it as the chip's label. Done once per render
   rather than baked into every template, so a new column cannot forget it. */
function labelTables(root) {
  root.querySelectorAll("table.rt").forEach(t => {
    const heads = [...t.querySelectorAll("thead th")].map(th => th.textContent.trim());
    if (!heads.length) return;
    t.querySelectorAll("tbody tr").forEach(tr => {
      [...tr.children].forEach((td, i) => {
        if (heads[i] && !td.hasAttribute("data-label")) td.setAttribute("data-label", heads[i]);
      });
    });
  });
}

/* The schedule runs a week ahead. The strip says, per day, how much of the
   picture has actually arrived - because a game with no starter named is not
   the same thing as one an hour from first pitch. */
function weekStrip() {
  const dates = ((S.index || {}).dates || []).slice().sort();
  if (dates.length < 2) return "";
  // The viewer's Eastern date, not the build's. Marking the build's last run as
  // "today" is what made the strip and the Today button disagree.
  const today = todayET();
  const forward = dates.filter(d => d >= today).slice(0, 8);
  const back = dates.filter(d => d < today).slice(-2);
  const show = back.concat(forward);
  const counts = (S.index || {}).day_summary || {};
  return `<div class="week">${show.map(d => {
    const c = counts[d] || {};
    const dt = new Date(d + "T12:00:00Z");
    const dow = dt.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
    const dm = dt.toLocaleDateString("en-US", { month: "numeric", day: "numeric", timeZone: "UTC" });
    const note = d === today ? "today"
      : c.plays ? `${c.plays} play${c.plays === 1 ? "" : "s"}`
      : c.priced ? `${c.priced} priced`
      : c.games ? `${c.games} games`
      : d < today ? "played" : "scheduled";
    return `<button data-date="${esc(d)}" class="${d === S.date ? "on" : ""}">
      ${esc(dow)}<b>${esc(dm)}</b><i>${esc(note)}</i></button>`;
  }).join("")}</div>`;
}

function renderView() {
  const v = $("#view");
  v.innerHTML = "";
  const strip = weekStrip();
  if (strip) v.insertAdjacentHTML("beforeend", strip);
  const map = { slate: viewSlate, bets: viewBets, matchups: viewMatchups,
                sim: viewSimulator, mine: viewMine, ratings: viewRatings,
                accuracy: viewAccuracy, model: viewModel };
  v.append((map[S.tab] || viewSlate)());
  labelTables(v);
  // On a phone fifteen fully expanded game cards is twenty-odd screens of
  // scrolling. Collapse the breakdowns; the glance line above each one already
  // answers whether there is a bet in the game.
  if (window.innerWidth <= 720)
    v.querySelectorAll("details.gd[open]").forEach(d => d.removeAttribute("open"));
  reportHeight();
}

/* --------------------------------------------------------------- events */
let pendingFile = null;

/* Two different hosts, two different ways to hand over a file. On GitHub Pages
   an ordinary anchor works. Inside the claude.ai artifact viewer a page cannot
   start its own download at all, so the save has to go through the host's own
   prompt. Try that first, fall back to the anchor, and either way the text is
   sitting in a box you can copy - which is the one path that never fails. */
async function saveFile(filename, text, mime) {
  const c = typeof window !== "undefined" ? window.claude : null;
  if (c && typeof c.use === "function") {
    try {
      const dl = await c.use("downloads");
      if (dl) {
        await dl.save({ filename, data: text });
        toast("Saved.");
        return;
      }
    } catch (err) {
      const code = err && err.code;
      if (code === "declined") return;
      if (code === "extension_not_enabled" && !/\.txt$/.test(filename))
        return saveFile(filename.replace(/\.[^.]+$/, ".txt"), text, "text/plain");
      // anything else: fall through to the ordinary anchor
    }
  }
  try {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (err) {
    toast("This viewer will not save files — copy the text instead.");
  }
}

function ioPanel(title, text, filename, mime) {
  const box = $("#io");
  if (!box) return;
  pendingFile = { filename, text, mime };
  box.innerHTML = `<div class="note"><b>${esc(title)}</b> — copy this, or save it as a file.
    Copying always works, wherever the page is embedded.</div>
    <textarea class="io" readonly id="ioText"></textarea>
    <div class="tools" style="margin-top:8px">
      <button class="btn" data-act="copy">Copy to clipboard</button>
      <button class="btn" data-act="download">Save ${esc(filename)}</button>
      <button class="btn" data-act="close-io">Close</button>
    </div>`;
  $("#ioText").value = text;
}

function importPanel() {
  const box = $("#io");
  if (!box) return;
  box.innerHTML = `<div class="note"><b>Import</b> — paste a previously exported JSON ledger,
    or choose the file. Bets you already have are left alone.</div>
    <textarea class="io" id="ioText" placeholder="Paste exported JSON here"></textarea>
    <div class="tools" style="margin-top:8px">
      <input type="file" id="ioFile" accept=".json,application/json">
      <button class="btn" data-act="do-import">Import</button>
      <button class="btn" data-act="close-io">Close</button>
    </div>`;
  $("#ioFile").addEventListener("change", ev => {
    const f = ev.target.files && ev.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => { $("#ioText").value = String(r.result || ""); };
    r.readAsText(f);
  });
}

function doImport() {
  try {
    const rows = L.fromImport($("#ioText").value);
    const { entries, added } = L.merge(S.mine, rows);
    S.mine = entries;
    settlePending();
    persistMine();
    toast(`Imported <b>${added}</b> new bet(s).`);
    renderView();
  } catch (err) {
    toast("That did not parse as an exported ledger.");
  }
}

function settlePending() {
  if (!L) return 0;
  const games = ((S.results || {}).games) || {};
  const { entries, changed } = L.settleAll(S.mine, games);
  S.mine = entries;
  return changed;
}

function onClick(ev) {
  const kelly = ev.target.closest("button[data-kelly]");
  if (kelly) {
    const fraction = Number(kelly.dataset.kelly);
    updateStaking({ ...(S.staking || {}), kelly: fraction },
      `Staking changed to <b>${esc(STK.label(fraction))}</b>.`);
    return;
  }
  if (ev.target.closest("[data-stake-reset]")) {
    const base = STK ? STK.defaults(S.index) : null;
    if (base) updateStaking(base, "Staking plan reset to the model default.");
    return;
  }

  const open = ev.target.closest("[data-sim-open]");
  if (open) {
    S.simPick = open.dataset.simOpen;
    S.simState = null;
    S.tab = "sim";
    renderTabs(); renderView(); syncURL();
    return;
  }

  const bench = ev.target.closest("button[data-bench]");
  if (bench) {
    const [side, idxs] = bench.dataset.bench.split(":");
    const i = parseInt(idxs, 10);
    const list = S.simState[side].benched;
    const at = list.indexOf(i);
    if (at >= 0) list.splice(at, 1); else list.push(i);
    bench.classList.toggle("off", at < 0);
    scheduleSimRun();
    return;
  }
  if (ev.target.closest("[data-simreset]")) {
    S.simState = SIM_DEFAULTS();
    renderView();
    return;
  }

  const add = ev.target.closest("button.add[data-bet]");
  if (add) { toggleBet(add.dataset.bet); return; }

  const del = ev.target.closest("button[data-del]");
  if (del) {
    const k = del.dataset.del;
    S.mine = S.mine.filter(e => L.keyOf(e) !== k);
    persistMine(); renderKPIs(); renderView();
    return;
  }

  const day = ev.target.closest("button[data-date]");
  if (day) { loadDate(day.dataset.date); return; }

  const act = ev.target.closest("[data-act]");
  if (!act) return;
  const a = act.dataset.act;
  if (a === "export-json")
    ioPanel("Ledger JSON",
      JSON.stringify({ schema: L.SCHEMA, exported_at: new Date().toISOString(),
                       bankroll_start: currentBankroll(), entries: S.mine }, null, 2),
      "mlb-edge-ledger.json", "application/json");
  else if (a === "export-csv")
    ioPanel("Ledger CSV", L.toCSV(S.mine), "mlb-edge-ledger.csv", "text/csv");
  else if (a === "download") {
    if (pendingFile) saveFile(pendingFile.filename, pendingFile.text, pendingFile.mime);
  }
  else if (a === "import") importPanel();
  else if (a === "do-import") doImport();
  else if (a === "close-io") { const b = $("#io"); if (b) b.innerHTML = ""; }
  else if (a === "copy") {
    const t = $("#ioText");
    if (!t) return;
    t.select();
    if (navigator.clipboard && navigator.clipboard.writeText)
      navigator.clipboard.writeText(t.value).then(() => toast("Copied."),
        () => toast("Select the text and copy manually."));
    else toast("Select the text and copy manually.");
  } else if (a === "settle") {
    const n = settlePending();
    persistMine();
    toast(n ? `Settled <b>${n}</b> bet(s).`
            : "Nothing new to settle — results appear once the next build publishes them.");
    renderKPIs(); renderView();
  } else if (a === "clear") {
    if (!S.mine.length) return;
    if (act.dataset.armed) {
      S.mine = []; persistMine(); renderKPIs(); renderView();
      toast("Ledger cleared.");
    } else {
      act.dataset.armed = "1";
      act.textContent = "Tap again to clear";
      setTimeout(() => { delete act.dataset.armed; act.textContent = "Clear all"; }, 4000);
    }
  }
}

function onSimInput(ev) {
  const sl = ev.target.closest("input[type=range][data-sim]");
  if (sl) {
    const side = sl.dataset.sim, key = sl.dataset.key, val = parseFloat(sl.value);
    if (side === "global") S.simState[key] = val;
    else S.simState[side][key] = val;
    const box = sl.closest(".row");
    if (box) {
      const out = box.querySelector(".val");
      if (out) {
        const pct1 = v => `${v > 1 ? "+" : ""}${((v - 1) * 100).toFixed(0)}%`;
        out.textContent = key === "bfMean" ? `${val.toFixed(1)} BF`
          : key === "nSims" ? `${(val / 1000).toFixed(0)}k`
          : key === "spQuality" || key === "penQuality" ? pct1(2 - val)
          : pct1(val);
      }
    }
    scheduleSimRun();
    return true;
  }
  return false;
}

let simTimer = null;
function scheduleSimRun() {
  clearTimeout(simTimer);
  simTimer = setTimeout(rerunSim, 140);   // let a dragged slider settle first
}

function onChange(ev) {
  const bankroll = ev.target.closest("#bankrollInput");
  if (bankroll && STK) {
    const value = STK.cleanBankroll(bankroll.value, currentBankroll());
    updateStaking({ ...(S.staking || {}), bankroll: value },
      `Bankroll changed to <b>${money(value)}</b>.`);
    return;
  }
  const pickG = ev.target.closest("select[data-simpick]");
  if (pickG) {
    S.simPick = pickG.value;
    S.simState = null;
    renderView();
    return;
  }
  const inp = ev.target.closest("input.stake[data-stake]");
  if (!inp || !L) return;
  const k = inp.dataset.stake;
  const val = Math.max(0, Number(inp.value) || 0);
  S.mine = S.mine.map(e => {
    if (L.keyOf(e) !== k) return e;
    const next = Object.assign({}, e, { stake: Math.round(val * 100) / 100 });
    if (next.result) {                       // restake a settled bet, re-grade it
      const res = (((S.results || {}).games) || {})[String(next.gamePk)];
      const s2 = L.settle(Object.assign({}, next, { result: null }), res);
      if (s2) Object.assign(next, s2);
    }
    return next;
  });
  persistMine();
  renderKPIs();
  renderView();
}

document.addEventListener("click", onClick);
document.addEventListener("change", onChange);
document.addEventListener("input", onSimInput);

/* --------------------------------------------------------------- loading */
function shiftDate(days) {
  const d = new Date(S.date + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

async function loadDate(ds) {
  S.date = ds;
  S.stakePlanCache = null;
  $("#curDate").textContent = ds;
  $("#view").innerHTML = `<div class="empty">Loading ${ds}…</div>`;
  S.slate = await getJSON(`data/slate-${ds}.json`) || { date: ds, games: [] };
  const age = SCH ? SCH.ageHours((S.index || {}).generated_at) : null;
  $("#stamp").textContent = S.slate.generated_at
    ? `updated ${agoTxt(S.slate.generated_at)}${age != null && age > 8 ? " · stale" : ""}`
    : "no data for this date";
  renderKPIs(); renderView(); syncURL();
}

async function boot() {
  S.index = await getJSON("data/index.json") || {};
  S.staking = STK ? STK.load(S.index) : {
    bankroll: Number(S.index.bankroll) || 250,
    kelly: Number((S.index.settings || {}).kelly) || 0.25,
  };
  S.ratings = await getJSON("data/ratings.json");
  S.perf = await getJSON("data/performance.json");
  S.results = await getJSON("data/results.json") || { games: {} };
  S.preds = await getJSON("data/predictions.json");
  if (L) {
    S.storageOK = L.available();
    S.mine = L.load();
    if (settlePending()) persistMine();
  }
  $("#seasonTag").textContent =
    `${(S.index.settings || {}).season || ""} · ${((S.index.settings || {}).n_sims || 20000).toLocaleString()} sims/game`;
  $("#nsims").textContent = ((S.index.settings || {}).n_sims || 20000).toLocaleString();
  renderStakingControls();
  renderTabs();
  const pick = SCH
    ? SCH.resolveDate(S.index.dates, todayET(), S.date)
    : { date: S.date || S.index.latest, reason: "none", stale: false };
  S.pick = pick;
  await loadDate(pick.date);

  $("#prev").onclick = () => loadDate(shiftDate(-1));
  $("#next").onclick = () => loadDate(shiftDate(1));
  $("#today").onclick = () => {
    // The viewer's Eastern date, not whatever day the last build thought it
    // was. If it has not been built yet, resolveDate falls back and says so.
    const p = SCH ? SCH.resolveDate(S.index.dates, todayET(), null)
                  : { date: S.index.latest };
    S.pick = p;
    loadDate(p.date);
  };
  setInterval(() => { if (S.slate?.generated_at)
    $("#stamp").textContent = `updated ${agoTxt(S.slate.generated_at)}`; }, 60000);
  startWatching();
}

/* A dashboard people leave open needs to notice three things: a new build
   landing, midnight passing, and the tab coming back to the foreground after
   hours in the background. */
let watchTimer = null;

async function checkForUpdates(force) {
  const fresh = await getJSON("data/index.json");
  if (!fresh) return;
  const state = { generatedAt: (S.index || {}).generated_at,
                  date: S.date, onToday: S.date === todayET() || S.pick?.reason === "behind" };
  const verdict = SCH ? SCH.shouldRefresh(state, fresh, new Date())
                      : { reload: false };
  if (!verdict.reload && !force) return;
  S.index = fresh;
  S.ratings = await getJSON("data/ratings.json") || S.ratings;
  S.perf = await getJSON("data/performance.json") || S.perf;
  S.results = await getJSON("data/results.json") || S.results;
  S.preds = await getJSON("data/predictions.json") || S.preds;
  if (L) { if (settlePending()) persistMine(); }
  const target = verdict.newDate
    || (SCH ? SCH.resolveDate(fresh.dates, todayET(), S.date).date : S.date);
  await loadDate(target);
  if (verdict.why) toast(`Refreshed — ${esc(verdict.why)}.`);
}

function startWatching() {
  clearInterval(watchTimer);
  watchTimer = setInterval(() => checkForUpdates(false), 5 * 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkForUpdates(false);
  });
}

/* Wix embeds this in an iframe; tell the parent how tall we are. */
function reportHeight() {
  try {
    const h = document.documentElement.scrollHeight;
    parent.postMessage({ type: "mlb-edge-height", height: h }, "*");
  } catch {}
}
let heightPending = false;
function queueHeight() {
  if (heightPending) return;
  heightPending = true;
  requestAnimationFrame(() => { heightPending = false; reportHeight(); });
}
window.addEventListener("resize", queueHeight);
new MutationObserver(queueHeight).observe(document.body, { childList: true, subtree: true });

boot();
})();

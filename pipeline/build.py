#!/usr/bin/env python3
"""
MLB Edge - build the slate.

Pulls the schedule, probable starters, batting orders, season rate stats,
standings, sportsbook prices and game-time weather; simulates every game
20,000 times at the plate-appearance level; prices the moneyline, run line,
total and first five; and writes a JSON feed the dashboard reads.

    python -m pipeline.build                # today
    python -m pipeline.build --date 2026-08-21
    python -m pipeline.build --days 3       # today plus the next two
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import datetime, timedelta, timezone

import numpy as np

from . import config as C
from .model import rates as R
from .model import teams as T
from .model.market import fmt_american, prob_to_american
from .model import portfolio
from .model.price import price_game, f5_fair
from .model.simulate import SidePack, simulate_game, derive
from .sources import espn, parks, weather
from .model.price import derived_lines
from .sources.mlb_api import (TEAM_ABBR, hitting_splits, people_stats, recent_workload,
                              schedule, standings, stats_by_range, team_batters,
                              team_pitchers)
from . import predict

# Baseball's calendar is written in Eastern time, and Eastern is UTC-4 for most
# of the season but UTC-5 either side of it. A frozen offset is right in July and
# an hour wrong in November - enough to build the wrong day's slate overnight.
# Use the real zone where the platform has tz data, fall back to the summer
# offset where it does not.
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                    # no tzdata installed
    ET = timezone(timedelta(hours=-4))


# ------------------------------------------------------------------ utils ---
def today_et() -> str:
    return datetime.now(timezone.utc).astimezone(ET).strftime("%Y-%m-%d")


def now_et():
    return datetime.now(timezone.utc).astimezone(ET)


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"), default=str)
    os.replace(tmp, path)


# ------------------------------------------------------- roster gathering ---
class League:
    """
    Fetch-once cache of everything the model needs about every club: rosters,
    a rolling recent-form window, handedness splits, defensive efficiency, and
    who in each bullpen has already been used up this week.
    """

    def __init__(self):
        self.bat: dict[int, list] = {}
        self.pit: dict[int, list] = {}
        self.recent_bat: dict[int, dict] = {}
        self.recent_pit: dict[int, dict] = {}
        self.splits: dict[int, dict] = {}
        self.der: dict[int, float] = {}
        self.league_der: float | None = None
        self.usage: dict[int, dict] = {}
        self.baseline = R.LEAGUE_FALLBACK.copy()

    def load(self, team_ids: list[int]) -> None:
        for tid in team_ids:
            if tid not in self.bat:
                self.bat[tid] = team_batters(tid)
            if tid not in self.pit:
                self.pit[tid] = team_pitchers(tid)
        allb = [b for v in self.bat.values() for b in v]
        if allb:
            self.baseline = R.league_baseline(allb)
        self._load_defense()

    def _load_defense(self) -> None:
        """Defensive efficiency per club, from its own pitching aggregate."""
        if not C.USE_TEAM_DEFENSE:
            return
        tot = {k: 0.0 for k in ("bb", "k", "s", "d", "t", "hr")}
        tbf = 0.0
        for tid, pitchers in self.pit.items():
            c = {k: sum(p["counts"].get(k, 0.0) for p in pitchers) for k in tot}
            t = sum(p.get("tbf", 0.0) for p in pitchers)
            der = R.team_der(c, t)
            if der is not None:
                self.der[tid] = der
            for k in tot:
                tot[k] += c[k]
            tbf += t
        self.league_der = R.team_der(tot, tbf)

    def load_form(self, team_ids: list[int], start: str, end: str) -> None:
        """The rolling window and the platoon splits, in bulk."""
        bat_ids, pit_ids = [], []
        for tid in team_ids:
            bat_ids += [b["id"] for b in self.bat.get(tid, []) if b.get("id")]
            pit_ids += [p["id"] for p in self.pit.get(tid, []) if p.get("id")]
        bat_ids = [i for i in bat_ids if i not in self.recent_bat]
        pit_ids = [i for i in pit_ids if i not in self.recent_pit]
        if C.RECENT_WEIGHT_BAT > 0 and bat_ids:
            self.recent_bat.update(stats_by_range(bat_ids, "hitting", start, end))
            for i in bat_ids:
                self.recent_bat.setdefault(i, {})
        if C.RECENT_WEIGHT_PIT > 0 and pit_ids:
            self.recent_pit.update(stats_by_range(pit_ids, "pitching", start, end))
            for i in pit_ids:
                self.recent_pit.setdefault(i, {})
        if C.USE_REAL_SPLITS:
            need = [b["id"] for tid in team_ids for b in self.bat.get(tid, [])
                    if b.get("id") and b["id"] not in self.splits]
            if need:
                self.splits.update(hitting_splits(need))
                for i in need:
                    self.splits.setdefault(i, {})

    def load_usage(self, team_ids: list[int], dates: list[str]) -> None:
        if C.PEN_LOOKBACK_DAYS > 0 and not self.usage:
            self.usage = recent_workload(team_ids, dates)

    # ---------------------------------------------------------- vectors ----
    def batter_vec(self, b: dict, hand: str | None) -> np.ndarray:
        """One hitter's outcome rates: season, plus recent form, plus the
        platoon split against the hand he is facing tonight."""
        rec = self.recent_bat.get(b.get("id")) or {}
        base_counts, base_pa = b["counts"], b["pa"]
        if rec.get("denom"):
            merged = R.blend_windows(base_counts, base_pa, rec["counts"], rec["denom"],
                                     self.baseline, 0.0, C.RECENT_WEIGHT_BAT,
                                     C.RECENT_MIN_PA)
            # blend_windows with prior 0 returns rates; rebuild pseudo-counts
            base_counts = {k: float(merged[i]) * base_pa for k, i in
                           (("bb", R.I_BB), ("k", R.I_K), ("s", R.I_1B),
                            ("d", R.I_2B), ("t", R.I_3B), ("hr", R.I_HR))}
        sp = (self.splits.get(b.get("id")) or {}) if C.USE_REAL_SPLITS else {}
        code = "vl" if (hand or "R").upper() == "L" else "vr"
        rec_split = sp.get(code)
        if rec_split:
            return R.split_vector(base_counts, base_pa, rec_split["counts"],
                                  rec_split["pa"], self.baseline,
                                  C.PRIOR_PA_BATTER, C.SPLIT_PRIOR_PA)
        vec = R.shrink(base_counts, base_pa, self.baseline, C.PRIOR_PA_BATTER)
        if hand:
            # No split on file for this hitter - a call-up, or someone who has
            # barely faced left-handers. Fall back to the generic platoon
            # adjustment rather than silently pretending handedness does not
            # exist, which is what happened before this line.
            pl_hr, pl_hit, pl_k = R.platoon_mults(b.get("bats", "R"), hand)
            vec = R.apply_multipliers(vec, pl_hr, pl_hit, pl_k)
        return vec

    def pitcher_vec(self, p: dict | None, is_sp: bool, penalty: float = 0.0) -> np.ndarray:
        if not p:
            return self.baseline.copy()
        prior = C.PRIOR_TBF_SP if is_sp else C.PRIOR_TBF_RP
        counts, tbf = p["counts"], p.get("tbf", 0.0)
        rec = self.recent_pit.get(p.get("id")) or {}
        if rec.get("denom") and C.RECENT_WEIGHT_PIT > 0:
            v = R.blend_windows(counts, tbf, rec["counts"], rec["denom"],
                                self.baseline, 0.0, C.RECENT_WEIGHT_PIT,
                                C.RECENT_MIN_TBF)
            counts = {k: float(v[i]) * tbf for k, i in
                      (("bb", R.I_BB), ("k", R.I_K), ("s", R.I_1B),
                       ("d", R.I_2B), ("t", R.I_3B), ("hr", R.I_HR))}
        vec = R.shrink(counts, tbf, self.baseline, prior)
        vec = R.regress_hr(vec, self.baseline, C.HR_REGRESS_PITCHER)
        if penalty > 0:                     # short rest, or a worked bullpen
            vec = R.apply_multipliers(vec, 1.0 + 2.0 * penalty, 1.0 + penalty,
                                      1.0 - 0.5 * penalty)
        return vec


# ------------------------------------------------------------- narrative ----
def rationale(g, d, best, away_sp, home_sp, wx, park, lineup_conf,
              p_away_f=None, p_home_f=None, away_pen=None, home_pen=None,
              away_rest=None, home_rest=None):
    """Plain-English reason the number is what it is - drivers, not adjectives."""
    bits = []
    a, h = g["away"], g["home"]
    ph = p_home_f if p_home_f is not None else d["p_home"]
    tail = ""
    if p_home_f is not None and abs(p_home_f - d["p_home"]) >= 0.01:
        tail = (f" (the raw simulation had {h} at {d['p_home']*100:.1f}% before the "
                f"market blend)")
    bits.append(f"Sim projects {d['mean_away']:.2f}-{d['mean_home']:.2f} "
                f"({d['mean_total']:.2f} total); {h} wins {ph*100:.1f}%{tail}.")
    if away_sp and home_sp:
        bits.append(f"{away_sp['name']} ({away_sp.get('era',0):.2f} ERA, "
                    f"{away_sp.get('k9',0):.1f} K/9) vs {home_sp['name']} "
                    f"({home_sp.get('era',0):.2f} ERA, {home_sp.get('k9',0):.1f} K/9).")
    elif away_sp or home_sp:
        named = away_sp or home_sp
        bits.append(f"Only one starter posted ({named['name']}); the other side is "
                    f"modelled as a bullpen game.")
    else:
        bits.append("Neither starter posted - both staffs modelled as bullpen games.")
    if park.get("run", 100) != 100 or park.get("hr", 100) != 100:
        bits.append(f"{park['name']} plays {park['run']} runs / {park['hr']} HR.")
    if wx.get("roof_closed"):
        bits.append("Roof shut, weather neutralised.")
    elif abs(wx.get("applied_pct", 0)) >= 1.0:
        bits.append(f"Weather {wx['applied_pct']:+.1f}% run environment ({wx['note']}).")
    for side, pen in ((g["away"], away_pen), (g["home"], home_pen)):
        out = (pen or {}).get("unavailable") or []
        if out:
            names = ", ".join(a["name"] for a in out[:3])
            bits.append(f"{side} bullpen down {len(out)} arm(s) ({names}) on recent workload.")
    for side, sp, rest in ((g["away"], away_sp, away_rest), (g["home"], home_sp, home_rest)):
        if sp and rest is not None and rest < C.SP_SHORT_REST_DAYS:
            bits.append(f"{sp['name']} on {rest} days rest.")
    bits.append("Confirmed batting orders." if lineup_conf
                else "Projected batting orders - lineups not posted yet.")
    if best and best["tier"] != "PASS":
        gapt = (f", market {best['price_txt']} implies {best['p_market']*100:.1f}%"
                if best.get("p_market") is not None else "")
        bits.append(f"Best number is {best['label']}: model {best['p_final']*100:.1f}%"
                    f"{gapt} - {best['edge_pct']:+.2f}% edge, fair {best['fair_price']}.")
    else:
        bits.append("No qualifying edge - market is priced where the model is.")
    return " ".join(bits)


# ------------------------------------------------------------ power ratings -
def _accumulate(counts_list) -> dict:
    tot = {k: 0.0 for k in ("bb", "k", "s", "d", "t", "hr")}
    for c in counts_list:
        for k in tot:
            tot[k] += c.get(k, 0.0)
    return tot


def _reference_opponent(lg: "League"):
    """
    A single reference team, built exactly the way every rated team is built:
    the composite of all 30 projected batting orders, all 30 top-five rotations,
    and all 30 leverage-weighted bullpens.

    This matters. Rating a team's nine best hitters against a league average
    that includes every bench bat inflates offence, and rating its top five
    starters against that same weak average deflates run prevention - which is
    how you end up telling a 54-74 team it is a 110-win roster. Comparing every
    team against an identically-constructed opponent removes the bias, and the
    league then centres on .500 by construction.
    """
    league = lg.baseline
    slot_c = [[] for _ in range(9)]     # per batting-order slot, not one blob
    slot_pa = [0.0] * 9
    bat_c, bat_pa = [], 0.0
    sp_c, sp_tbf = [], 0.0
    pen_c, pen_tbf = [], 0.0
    for tid, batters in lg.bat.items():
        lineup, _ = T.project_lineup(batters, [])
        for i, b in enumerate(lineup[:9]):
            slot_c[i].append(b["counts"])
            slot_pa[i] += b.get("pa", 0.0)
            bat_c.append(b["counts"])
            bat_pa += b.get("pa", 0.0)
        pitchers = lg.pit.get(tid, [])
        sps = sorted([p for p in pitchers if p.get("is_sp")],
                     key=lambda p: -p.get("tbf", 0))[:5]
        for p in sps:
            sp_c.append(p["counts"])
            sp_tbf += p.get("tbf", 0.0)
        pen = T.bullpen_composite(pitchers)
        pen_c.append(pen["counts"])
        pen_tbf += pen.get("tbf", 0.0)

    ref_bat = R.shrink(_accumulate(bat_c), bat_pa, league, 0.0) if bat_pa else league
    ref_sp = R.shrink(_accumulate(sp_c), sp_tbf, league, 0.0) if sp_tbf else league
    ref_pen = R.shrink(_accumulate(pen_c), pen_tbf, league, 0.0) if pen_tbf else league
    # One reference hitter per lineup slot rather than nine clones of the league
    # mean. Run scoring is convex in on-base ability, so a uniform lineup scores
    # measurably less than a real one with the same average - which would show
    # up as every team out-hitting the reference by a tenth of a run.
    ref_lineup = []
    for i in range(9):
        pa = max(slot_pa[i], 1.0)
        ref_lineup.append({"id": None, "name": f"Reference {i+1}", "pos": "DH",
                           "bats": "R", "pa": pa, "sb": pa * 0.012,
                           "counts": _accumulate(slot_c[i]) if slot_c[i] else {},
                           "ops": 0.0})
    all_bat = [b for v in lg.bat.values() for b in v]
    ref_adv = R.baserunning_index(all_bat) if all_bat else 1.0
    return ref_bat, ref_sp, ref_pen, ref_lineup, ref_adv


def power_ratings(lg: League, sd: dict) -> list[dict]:
    """
    Neutral-park simulation of every roster against one identically-built
    reference team. A true-talent rating, not a standings snapshot: what would
    this roster do against average opposition in an average park.
    """
    out = []
    league = lg.baseline
    ref_bat, ref_sp, ref_pen, ref_lineup, ref_adv = _reference_opponent(lg)

    for tid, batters in lg.bat.items():
        pitchers = lg.pit.get(tid, [])
        lineup, _ = T.project_lineup(batters, [])
        pen = T.bullpen_composite(pitchers)
        sps = sorted([p for p in pitchers if p.get("is_sp")],
                     key=lambda p: -p.get("tbf", 0))[:5]
        if not sps:
            continue
        sp_vec = R.shrink(_accumulate([p["counts"] for p in sps]),
                          sum(p["tbf"] for p in sps), league, C.PRIOR_TBF_SP)
        pen_vec = lg.pitcher_vec(pen, False)

        # the team hits against the reference staff
        off = T.side_matrices(lineup, ref_sp, T.tto_vector(ref_sp), ref_pen,
                              league, "R", 1.0, 1.0, 0.0)
        team_off = SidePack(*off, bf_mean=C.SP_BF_DEFAULT, bf_sd=C.SP_BF_SD,
                            bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX,
                            adv=R.baserunning_index(batters))
        # the reference lineup hits against this team's staff
        deff = T.side_matrices(ref_lineup, sp_vec, T.tto_vector(sp_vec), pen_vec,
                               league, "R", 1.0, 1.0, 0.0)
        opp = SidePack(*deff, bf_mean=C.SP_BF_DEFAULT, bf_sd=C.SP_BF_SD,
                       bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX, adv=ref_adv)

        # Play the pair both ways. The home team skips the bottom of the ninth
        # when it is ahead, which shaves about two tenths of a run off whichever
        # side is at home; rating every team from one seat would bake that
        # structural quirk into the ratings as if it were talent.
        s_away = simulate_game(team_off, opp, C.N_SIMS_RATINGS, seed=C.RANDOM_SEED + tid)
        s_home = simulate_game(opp, team_off, C.N_SIMS_RATINGS, seed=C.RANDOM_SEED + tid + 7919)
        rs = (float(s_away["away"].mean()) + float(s_home["home"].mean())) / 2
        ra = (float(s_away["home"].mean()) + float(s_home["away"].mean())) / 2
        wpct = (float((s_away["away"] > s_away["home"]).mean())
                + float((s_home["home"] > s_home["away"]).mean())) / 2
        st = sd.get(tid, {})
        out.append({
            "team": TEAM_ABBR.get(tid, "?"), "team_id": tid,
            "name": st.get("name", TEAM_ABBR.get(tid, "?")),
            "rs_per_g": round(rs, 2), "ra_per_g": round(ra, 2),
            "net": round(rs - ra, 2), "true_wpct": round(wpct, 4),
            "proj_162": round(wpct * 162, 1),
            "w": st.get("w"), "l": st.get("l"), "diff": st.get("diff"),
            "l10": st.get("l10"), "streak": st.get("streak"),
            "actual_wpct": round(st["w"] / max(st["w"] + st["l"], 1), 4) if st else None,
            "bullpen_era": round(pen.get("era", 0.0), 2),
            "rotation_era": round(sum(p.get("era", 0) * p.get("tbf", 0) for p in sps)
                                  / max(sum(p.get("tbf", 0) for p in sps), 1), 2),
        })
    # Calibrate the two run columns.
    #
    # Two things are true of a real league and not of the raw output. Every run
    # scored is a run allowed, so the league's RS and RA must be the same
    # number; and the reference staff here is a composite of every team's top
    # five starters plus its leverage-weighted bullpen, which is better than the
    # average arm a lineup actually faces, so the raw scale sits below real MLB
    # scoring. An affine calibration fixes both: recentre so the columns
    # balance, then scale so the league lands on the runs per game it has
    # actually produced this season. Each team keeps its distance from league
    # average, which is the entire content of a rating - ordering, spread and
    # true win percentage are untouched.
    if out:
        m_rs = sum(r["rs_per_g"] for r in out) / len(out)
        m_ra = sum(r["ra_per_g"] for r in out) / len(out)
        mid = (m_rs + m_ra) / 2 or 1.0
        tot_r = sum(v.get("rs", 0) for v in sd.values())
        tot_g = sum(max(v.get("w", 0) + v.get("l", 0), 0) for v in sd.values())
        actual_rpg = (tot_r / tot_g) if tot_g else 0.0
        scale = min(max((actual_rpg / mid) if actual_rpg else 1.0, 0.75), 1.40)
        for r in out:
            r["rs_per_g"] = round((r["rs_per_g"] - m_rs + mid) * scale, 2)
            r["ra_per_g"] = round((r["ra_per_g"] - m_ra + mid) * scale, 2)
            r["net"] = round(r["rs_per_g"] - r["ra_per_g"], 2)
            r["scale"] = round(scale, 3)
            r["league_rpg"] = round(actual_rpg, 2)

    out.sort(key=lambda r: -r["true_wpct"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
        r["luck"] = (round((r["actual_wpct"] - r["true_wpct"]) * 162, 1)
                     if r.get("actual_wpct") is not None else None)
    return out


# ----------------------------------------------------------------- one day --
READINESS = {
    "LIVE":   "in progress or final",
    "SET":    "priced, both starters posted, lineups confirmed",
    "PRICED": "priced, both starters posted",
    "EARLY":  "starters posted, no prices yet",
    "PENCIL": "starter not announced",
}


def readiness_of(g, odds, both_sp, lineups_confirmed) -> str:
    if g["abstract"] in ("Live", "Final"):
        return "LIVE"
    priced = bool(odds.get("ml_away") or odds.get("total"))
    if not both_sp:
        return "PENCIL"
    if not priced:
        return "EARLY"
    return "SET" if lineups_confirmed else "PRICED"


def verdict_of(bets, readiness, days_out) -> dict:
    """One line telling you what to do with this game, in plain words."""
    live = [b for b in bets if b["stake"] > 0]
    best = bets[0] if bets else None
    if readiness == "PENCIL":
        return {"action": "WAIT", "text": "No starter announced yet — nothing to price."}
    if readiness == "EARLY":
        if best and best["tier"] != "PASS":
            return {"action": "WATCH",
                    "text": f"No prices posted yet. Model makes it {best['label']} "
                            f"at fair {best['fair_price']} — worth checking when the number lands."}
        return {"action": "WATCH", "text": "No prices posted yet. Fair lines published above."}
    if live:
        b = live[0]
        book = f" at {b['book']}" if b.get("book") else ""
        return {"action": "BET",
                "text": f"Bet {b['label']} at {b['price_txt']}{book} for "
                        f"${b['stake']:.2f} to win ${b['to_win']:.2f} "
                        f"({b['tier']}, {b['edge_pct']:+.2f}% edge)."}
    if best and best["tier"] in ("GOOD", "BEST BET"):
        why = best.get("suppressed") or (best.get("lock_fails") or [None])[0]
        return {"action": "LEAN",
                "text": f"{best['label']} is the number, {best['edge_pct']:+.2f}% edge"
                        + (f" — no stake: {why}." if why else " — no stake today.")}
    if best and best["tier"] == "LEAN":
        return {"action": "LEAN",
                "text": f"Slight lean to {best['label']} ({best['edge_pct']:+.2f}%). "
                        f"Not enough to bet."}
    return {"action": "PASS", "text": "No edge. The market is priced where the model is."}


def build_date(date_str: str, lg: League, sd: dict, manual: dict,
               calib: dict | None = None, days_out: int = 0) -> dict:
    print(f"[{date_str}] fetching schedule…")
    games = schedule(date_str)
    if not games:
        print("  no games")
        return {"date": date_str, "games": [], "generated_at": _now(), "days_out": days_out}

    team_ids = sorted({g["away_id"] for g in games} | {g["home_id"] for g in games})
    print(f"  {len(games)} games, loading {len(team_ids)} rosters…")
    lg.load(team_ids)
    lg.load_form(team_ids, *_recent_window())

    odds_map = {}
    health = {}
    if days_out <= C.ODDS_LOOKAHEAD_DAYS:
        print("  fetching odds…")
        # Odds come from a public feed nobody promises us anything about. A
        # malformed payload must cost us the prices for one day, not the whole
        # build - the model still has a fair line for every market either way.
        try:
            odds_map = espn.odds_for_date(date_str)
            health = espn.feed_health(odds_map, expected_games=len(games))
        except Exception as exc:
            odds_map, health = {}, espn.feed_health({}, expected_games=len(games))
            print(f"  ! odds feed failed ({type(exc).__name__}: {exc})")
            print("  ! continuing with fair lines only")
        else:
            print(f"  odds: {health['priced']}/{len(games)} games priced from "
                  f"{', '.join(health['books']) or 'no books'}"
                  + (f" via {', '.join(health['sources'])}" if health.get("sources") else ""))
            if health["priced"] == 0:
                print("  ! no prices found — the model will publish fair lines only")
    else:
        print("  beyond the odds window — publishing fair lines only")

    calib = calib or {}
    total_adj = float(calib.get("total_adj") or 0.0)
    prob_scale = float(calib.get("prob_scale") or 1.0)
    # A learned runs-per-game correction is applied as a run-environment nudge,
    # not bolted onto the finished score, so it flows through every market the
    # simulation produces instead of only the total.
    calib_env = 1.0 + (total_adj / 8.7) if total_adj else 1.0

    out_games = []
    for gi, g in enumerate(games):
        park = parks.lookup(g["venue"], g.get("lat"), g.get("lon"))
        wx = weather.forecast(park, g["gameDate"])
        o = odds_map.get((g["away"], g["home"]), {})
        g["odds_age_h"] = _age_hours(o.get("fetched_at"))
        g["weather"] = wx

        ab = lg.bat.get(g["away_id"], [])
        hb = lg.bat.get(g["home_id"], [])
        ap = lg.pit.get(g["away_id"], [])
        hp = lg.pit.get(g["home_id"], [])

        missing = [i for i in (g["away_lineup"] + g["home_lineup"])
                   if i not in {b["id"] for b in ab + hb}]
        extra = people_stats(missing, "hitting") if missing else {}

        a_lineup, a_conf = T.project_lineup(ab, g["away_lineup"], extra)
        h_lineup, h_conf = T.project_lineup(hb, g["home_lineup"], extra)

        a_sp = T.starter_profile(ap, g["away_sp"])
        h_sp = T.starter_profile(hp, g["home_sp"])
        need = [x["id"] for x in (g["away_sp"], g["home_sp"])
                if x and not (a_sp if x is g["away_sp"] else h_sp)]
        if need:
            px = people_stats(need, "pitching")
            a_sp = a_sp or (px.get(g["away_sp"]["id"]) if g["away_sp"] else None)
            h_sp = h_sp or (px.get(g["home_sp"]["id"]) if g["home_sp"] else None)

        a_rest, a_pen_mult = T.starter_rest(a_sp, lg.usage, date_str)
        h_rest, h_pen_mult = T.starter_rest(h_sp, lg.usage, date_str)

        a_pen = T.bullpen_composite(ap, exclude_id=(a_sp or {}).get("id"), usage=lg.usage)
        h_pen = T.bullpen_composite(hp, exclude_id=(h_sp or {}).get("id"), usage=lg.usage)

        a_sp_vec = lg.pitcher_vec(a_sp, True, a_pen_mult)
        h_sp_vec = lg.pitcher_vec(h_sp, True, h_pen_mult)
        a_pen_vec = lg.pitcher_vec(a_pen, False)
        h_pen_vec = lg.pitcher_vec(h_pen, False)

        hr_m, hit_m = R.park_weather_mults(park, wx)
        hr_m *= calib_env
        hit_m *= (1.0 + (calib_env - 1.0) * 0.5)

        # each side hits against the other side's defense
        a_def = R.defense_mult(lg.der.get(g["home_id"]), lg.league_der, C.DEFENSE_STRENGTH)
        h_def = R.defense_mult(lg.der.get(g["away_id"]), lg.league_der, C.DEFENSE_STRENGTH)

        h_sp_hand = (h_sp or {}).get("hand") or (g["home_sp"] or {}).get("hand", "R")
        a_sp_hand = (a_sp or {}).get("hand") or (g["away_sp"] or {}).get("hand", "R")
        a_vecs = {("sp", i): lg.batter_vec(b, h_sp_hand) for i, b in enumerate(a_lineup)}
        a_vecs.update({("pen", i): lg.batter_vec(b, None) for i, b in enumerate(a_lineup)})
        h_vecs = {("sp", i): lg.batter_vec(b, a_sp_hand) for i, b in enumerate(h_lineup)}
        h_vecs.update({("pen", i): lg.batter_vec(b, None) for i, b in enumerate(h_lineup)})

        A = T.side_matrices(a_lineup, h_sp_vec, T.tto_vector(h_sp_vec), h_pen_vec,
                            lg.baseline, h_sp_hand, hr_m, hit_m * a_def,
                            -C.HOME_FIELD_ADV, bat_vectors=a_vecs)
        H = T.side_matrices(h_lineup, a_sp_vec, T.tto_vector(a_sp_vec), a_pen_vec,
                            lg.baseline, a_sp_hand, hr_m, hit_m * h_def,
                            +C.HOME_FIELD_ADV, bat_vectors=h_vecs)

        a_pack = SidePack(*A, bf_mean=_bf(h_sp), bf_sd=C.SP_BF_SD,
                          bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX,
                          adv=R.baserunning_index(ab), no_starter=h_sp is None)
        h_pack = SidePack(*H, bf_mean=_bf(a_sp), bf_sd=C.SP_BF_SD,
                          bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX,
                          adv=R.baserunning_index(hb), no_starter=a_sp is None)

        sim = simulate_game(a_pack, h_pack, C.N_SIMS, seed=C.RANDOM_SEED + g["gamePk"])
        man = manual.get(str(g["gamePk"]), {})
        d = derive(sim, o.get("total"), o.get("rl_line", -1.5),
                   (man.get("f5") or {}).get("total"))

        # a learned confidence correction, applied before anything is priced
        if prob_scale != 1.0:
            d["p_home"] = predict.apply_prob_scale(d["p_home"], prob_scale)
            d["p_away"] = 1.0 - d["p_home"]

        both_sp = bool(a_sp and h_sp)
        conf = a_conf and h_conf
        ready = readiness_of(g, o, both_sp, conf)

        bets = price_game(g, d, o, man)
        # Nothing gets staked days ahead of a price that will move, or on a game
        # whose picture is still incomplete. The read is still published.
        if days_out > C.STAKE_MAX_DAYS_OUT or ready in ("EARLY", "PENCIL"):
            for b in bets:
                if b["stake"] > 0:
                    b["stake"] = 0.0
                    b["to_win"] = 0.0
                    b["suppressed"] = ("too far out to size"
                                       if days_out > C.STAKE_MAX_DAYS_OUT
                                       else "waiting on prices and starters")

        p_away_f = next((b["p_final"] for b in bets
                         if b["market"] == "ML" and b["selection"] == g["away"]), d["p_away"])
        p_home_f = next((b["p_final"] for b in bets
                         if b["market"] == "ML" and b["selection"] == g["home"]), d["p_home"])
        d["p_away_final"] = round(p_away_f, 4)
        d["p_home_final"] = round(p_home_f, 4)
        d["p_sim_away"] = round(d["p_away"], 4)
        d["p_sim_home"] = round(d["p_home"], 4)
        best = bets[0] if bets else None

        out_games.append({
            "gamePk": g["gamePk"], "date": date_str, "start": g["gameDate"],
            "status": g["status"], "abstract": g["abstract"], "gameType": g["gameType"],
            "days_out": days_out, "readiness": ready, "readiness_note": READINESS[ready],
            "away": g["away"], "home": g["home"],
            "away_name": g["away_name"], "home_name": g["home_name"],
            "venue": park["name"], "park": {k: park[k] for k in ("run", "hr", "roof", "known")},
            "weather": wx,
            "away_sp": _sp_out(a_sp, g["away_sp"], a_rest),
            "home_sp": _sp_out(h_sp, g["home_sp"], h_rest),
            "away_pen": _pen_out(a_pen), "home_pen": _pen_out(h_pen),
            "away_lineup": _lineup_out(a_lineup), "home_lineup": _lineup_out(h_lineup),
            "lineups_confirmed": conf,
            "defense": {g["away"]: round(lg.der.get(g["away_id"], 0.0), 4),
                        g["home"]: round(lg.der.get(g["home_id"], 0.0), 4),
                        "league": round(lg.league_der or 0.0, 4)},
            "sim": {k: v for k, v in d.items() if k not in ("hist", "margin_hist")},
            "hist": d["hist"], "margin_hist": d["margin_hist"],
            "n_sims": C.N_SIMS,
            "model_line": {"away": fmt_american(prob_to_american(p_away_f)),
                           "home": fmt_american(prob_to_american(p_home_f)),
                           "sim_away": fmt_american(prob_to_american(d["p_sim_away"])),
                           "sim_home": fmt_american(prob_to_american(d["p_sim_home"])),
                           "total": round(d["fair_total"] * 2) / 2},
            "f5_fair": f5_fair(d),
            "derived": derived_lines(d, g["away"], g["home"]),
            # Everything the page needs to run the same game again with an
            # input changed. Rate vectors rather than finished matrices, so the
            # simulator can recompose after a starter is swapped or the wind
            # turns round. Rounded hard - five decimals is far below the
            # Monte Carlo noise floor and keeps the feed small.
            "sim_inputs": {
                "league": _vec(lg.baseline),
                "mults": {"hr": round(hr_m, 5), "hit": round(hit_m, 5),
                          "def_away": round(a_def, 5), "def_home": round(h_def, 5),
                          "hfa": C.HOME_FIELD_ADV,
                          "park_run": park["run"], "park_hr": park["hr"],
                          "wx_run_mult": round(float(wx.get("run_mult", 1.0)), 5),
                          "calib_env": round(calib_env, 5)},
                "away": {
                    "bats": [{"name": b["name"], "pos": b.get("pos"),
                              "bats": b.get("bats"), "pa": int(b.get("pa", 0)),
                              "vs_sp": _vec(a_vecs[("sp", i)]),
                              "vs_pen": _vec(a_vecs[("pen", i)])}
                             for i, b in enumerate(a_lineup)],
                    "opp_sp": _vec(h_sp_vec), "opp_sp3": _vec(T.tto_vector(h_sp_vec)),
                    "opp_pen": _vec(h_pen_vec),
                    "opp_sp_name": (h_sp or {}).get("name", "TBA"),
                    "bf_mean": round(_bf(h_sp), 2), "bf_sd": C.SP_BF_SD,
                    "bf_min": C.SP_BF_MIN, "bf_max": C.SP_BF_MAX,
                    "adv": round(R.baserunning_index(ab), 4),
                    "no_starter": h_sp is None, "hfa_sign": -1},
                "home": {
                    "bats": [{"name": b["name"], "pos": b.get("pos"),
                              "bats": b.get("bats"), "pa": int(b.get("pa", 0)),
                              "vs_sp": _vec(h_vecs[("sp", i)]),
                              "vs_pen": _vec(h_vecs[("pen", i)])}
                             for i, b in enumerate(h_lineup)],
                    "opp_sp": _vec(a_sp_vec), "opp_sp3": _vec(T.tto_vector(a_sp_vec)),
                    "opp_pen": _vec(a_pen_vec),
                    "opp_sp_name": (a_sp or {}).get("name", "TBA"),
                    "bf_mean": round(_bf(a_sp), 2), "bf_sd": C.SP_BF_SD,
                    "bf_min": C.SP_BF_MIN, "bf_max": C.SP_BF_MAX,
                    "adv": round(R.baserunning_index(hb), 4),
                    "no_starter": a_sp is None, "hfa_sign": 1},
                "seed": C.RANDOM_SEED + g["gamePk"],
            },
            "odds": o, "bets": bets, "best": best,
            "verdict": verdict_of(bets, ready, days_out),
            "rationale": rationale(g, d, best, a_sp, h_sp, wx, park, conf,
                                   p_away_f, p_home_f, a_pen, h_pen, a_rest, h_rest),
            "away_score": g["away_score"], "home_score": g["home_score"],
        })
        print(f"  [{gi+1}/{len(games)}] {g['away']}@{g['home']} {ready} "
              f"{d['mean_away']:.2f}-{d['mean_home']:.2f} "
              f"{p_home_f*100:.1f}% home"
              + (f" | {best['label']} {best['edge_pct']:+.2f}% {best['tier']}" if best else ""))

    payload = {"date": date_str, "generated_at": _now(), "games": out_games,
               "n_games": len(out_games), "days_out": days_out,
               "odds_health": health,
               "calibration": {"total_adj": total_adj, "prob_scale": prob_scale,
                               "applied": bool(calib.get("applied"))}}
    notes = portfolio.apply(payload)
    print(f"  portfolio: {notes['n_plays']} plays, ${notes['staked']:.2f} at risk, "
          f"{notes['n_best']} best bet(s)"
          + (", exposure scaled" if notes["exposure_scaled"] else "")
          + (f", median gap {notes['median_gap']*100:.1f}pts"
             if notes.get("median_gap") is not None else "")
          + (" — DIVERGENCE FLAG" if notes["divergence_flag"] else ""))
    return payload


def _recent_window() -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=C.RECENT_WINDOW_DAYS)
    return start.isoformat(), end.isoformat()


def _vec(v) -> list:
    """A rate vector, rounded to where Monte Carlo noise swamps the difference."""
    return [round(float(x), 5) for x in v]


def _bf(sp) -> float:
    if not sp:
        return C.SP_BF_DEFAULT
    v = sp.get("bf_per_start")
    return float(v) if v else C.SP_BF_DEFAULT


def _sp_out(p, meta, rest=None):
    if not p:
        return {"name": (meta or {}).get("name", "TBA"), "posted": bool(meta),
                "era": None, "k9": None, "whip": None, "ip": None,
                "hand": (meta or {}).get("hand"), "rest": rest}
    return {"id": p.get("id"), "name": p.get("name"), "posted": True,
            "hand": p.get("hand"), "era": p.get("era"), "whip": p.get("whip"),
            "k9": p.get("k9"), "bb9": p.get("bb9"), "hr9": p.get("hr9"),
            "ip": p.get("ip"), "gs": p.get("gs"), "tbf": p.get("tbf"),
            "rest": rest,
            "bf_per_start": round(p.get("bf_per_start") or C.SP_BF_DEFAULT, 1)}


def _pen_out(pen):
    return {"era": round(pen.get("era", 0.0), 2), "arms": pen.get("n", 0),
            "unavailable": pen.get("unavailable", []),
            "tired": pen.get("tired", [])}


def _lineup_out(lu):
    return [{"name": b["name"], "pos": b.get("pos"), "bats": b.get("bats"),
             "pa": int(b.get("pa", 0)), "ops": b.get("ops"), "avg": b.get("avg"),
             "hr": int(b["counts"].get("hr", 0))} for b in lu]


def _age_hours(iso):
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - t).total_seconds() / 3600.0, 0.0)
    except Exception:
        return None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- main ---
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=C.LOOKAHEAD_DAYS)
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--out", default=C.DOCS_DATA_DIR)
    args = ap.parse_args(argv)

    t0 = time.time()
    start = args.date or today_et()
    d0 = datetime.strptime(start, "%Y-%m-%d")
    dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max(args.days, 1))]

    sd = standings()
    lg = League()
    manual = load_json(os.path.join(C.DATA_DIR, "manual_odds.json"), {})

    # Grade whatever finished before building, so today's numbers are corrected
    # by everything the model has already got right or wrong.
    predict.grade()
    calib = predict.calibration()
    if calib.get("applied"):
        print(f"calibration: total {calib['total_adj']:+.2f} runs, "
              f"confidence x{calib['prob_scale']:.3f} (from {calib['n']} graded games)")
    else:
        print(f"calibration: not applied - {calib.get('reason', '')}")

    # Recent bullpen usage, read once for every club in the window.
    hist = [(d0 - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(1, C.PEN_LOOKBACK_DAYS + 1)]

    built = []
    for i, ds in enumerate(dates):
        if i == 0:
            first = schedule(ds)
            ids = sorted({g["away_id"] for g in first} | {g["home_id"] for g in first})
            if ids:
                print(f"reading bullpen usage over {len(hist)} day(s)…")
                lg.load_usage(ids, hist)
        # Today is the point of the site: if it cannot be built, the run should
        # go red and say so. A lookahead day is a bonus - losing one of those is
        # not a reason to throw away the six days that did build.
        try:
            payload = build_date(ds, lg, sd, manual, calib, days_out=i)
        except Exception as exc:
            if i == 0:
                raise
            print(f"  ! {ds} failed to build ({type(exc).__name__}: {exc}) — skipping it")
            continue
        save_json(os.path.join(args.out, f"slate-{ds}.json"), payload)
        built.append(payload)

    print("building power ratings…")
    ratings = power_ratings(lg, sd)
    save_json(os.path.join(args.out, "ratings.json"),
              {"generated_at": _now(), "teams": ratings})

    # shadow book: record every call, then grade whatever has finished
    from .grade import record_calls, grade_all, summarise, results_map
    for p in built:
        record_calls(p)
        predict.record(p)
    if not args.no_grade:
        grade_all()
        predict.grade()
    perf = summarise()
    save_json(os.path.join(args.out, "performance.json"), perf)
    save_json(os.path.join(args.out, "results.json"),
              {"generated_at": _now(), "games": results_map()})
    preds = predict.summary()
    preds["calibration"] = predict.calibration()
    save_json(os.path.join(args.out, "predictions.json"), preds)

    # a one-line summary per day so the dashboard's week strip can say what is
    # actually ready on each date rather than just listing dates
    day_summary = {}
    for pl in built:
        gs = pl.get("games", [])
        day_summary[pl["date"]] = {
            "games": len(gs),
            "priced": sum(1 for g in gs if (g.get("odds") or {}).get("n_books")),
            "plays": sum(1 for g in gs for b in g.get("bets", []) if b["stake"] > 0),
            "best": sum(1 for g in gs for b in g.get("bets", []) if b["tier"] == "BEST BET"),
            "staked": round(sum(b["stake"] for g in gs for b in g.get("bets", [])), 2),
        }

    index = {
        "generated_at": _now(),
        "day_summary": day_summary,
        "dates": sorted({*_existing_dates(args.out), *dates}),
        "latest": built[0]["date"] if built else start,
        # The page works out its own date from the viewer's clock; these are
        # published so it can tell how far behind the build is, and so a support
        # question can be answered without guessing.
        "built_for": start,
        "server_today_et": today_et(),
        "server_now_et": now_et().isoformat(timespec="seconds"),
        "bankroll": C.BANKROLL,
        "settings": {"kelly": C.KELLY_FRACTION, "max_stake_pct": C.MAX_STAKE_PCT,
                     "min_stake": C.MIN_STAKE, "stake_rounding": C.STAKE_ROUNDING,
                     "market_blend": C.MARKET_BLEND, "edge_ceiling": C.EDGE_CEILING,
                     "edge_ceiling_total": C.EDGE_CEILING_TOT,
                     "tier_best": C.TIER_BEST, "tier_good": C.TIER_GOOD,
                     "tier_lean": C.TIER_LEAN,
                     "max_best_bets": C.MAX_BEST_BETS_PER_SLATE,
                     "max_plays": C.MAX_PLAYS_PER_SLATE,
                     "max_slate_exposure_pct": C.MAX_SLATE_EXPOSURE_PCT,
                     "divergence_gap": C.DIVERGENCE_MEDIAN_GAP,
                     "stake_max_days_out": C.STAKE_MAX_DAYS_OUT,
                     "n_sims": C.N_SIMS, "season": C.SEASON,
                     "preferred_book": C.PREFERRED_BOOK},
        "record": perf.get("overall", {}),
        "predictions": {k: preds.get("overall", {}).get(k)
                        for k in ("n", "accuracy", "brier", "mae_total")},
        "vs_market": preds.get("vs_market", {}),
        "lookahead_days": len(dates),
        "calibration": preds.get("calibration", {}),
    }
    save_json(os.path.join(args.out, "index.json"), index)
    if built:
        save_json(os.path.join(args.out, "latest.json"), built[0])

    print(f"done in {time.time()-t0:.1f}s -> {args.out}")
    return 0


def _existing_dates(out_dir):
    try:
        return [f[6:-5] for f in os.listdir(out_dir)
                if f.startswith("slate-") and f.endswith(".json")]
    except FileNotFoundError:
        return []


if __name__ == "__main__":
    sys.exit(main())

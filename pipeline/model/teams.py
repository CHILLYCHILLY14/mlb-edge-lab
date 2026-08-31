"""Assemble a team's batting order and pitching staff into model inputs."""
from __future__ import annotations
import numpy as np

from .. import config as C
from . import rates as R

POSITION_PLAYER = lambda p: (p.get("pos") or "?").upper() not in ("P", "SP", "RP", "TWP")


def project_lineup(batters: list[dict], confirmed_ids: list[int],
                   extra: dict | None = None) -> tuple[list[dict], bool]:
    """
    Confirmed batting order if the API has posted one, otherwise the nine
    position players with the most plate appearances. Returns (lineup, confirmed).
    """
    by_id = {b["id"]: b for b in batters}
    if extra:
        by_id.update({k: v for k, v in extra.items() if v})
    if confirmed_ids:
        lu = [by_id[i] for i in confirmed_ids[:9] if i in by_id]
        if len(lu) >= 8:
            while len(lu) < 9:
                lu.append(_replacement())
            return lu[:9], True
    pool = [b for b in batters if POSITION_PLAYER(b) and b["pa"] >= C.MIN_PA_LINEUP]
    pool.sort(key=lambda b: -b["pa"])
    lu = pool[:9]
    while len(lu) < 9:
        lu.append(_replacement())
    return lu, False


def _replacement() -> dict:
    return {"id": None, "name": "Replacement", "pos": "?", "bats": "R", "pa": 0.0,
            "sb": 0.0, "counts": {"bb": 0, "k": 0, "s": 0, "d": 0, "t": 0, "hr": 0},
            "ops": 0.0, "obp": 0.0, "slg": 0.0, "avg": 0.0}


def starter_profile(pitchers: list[dict], sp_meta: dict | None,
                    extra: dict | None = None) -> dict | None:
    """Find the probable starter's season line."""
    if not sp_meta:
        return None
    pid = sp_meta.get("id")
    for p in pitchers:
        if p["id"] == pid:
            return p
    if extra and pid in extra:
        return extra[pid]
    return None


def pen_availability(pitcher_id, usage: dict | None) -> tuple[str, str]:
    """
    Is this arm available tonight? Returns (state, reason).

    A bullpen's season ERA says nothing about whether its best three arms can
    pitch. A closer who threw 38 pitches last night is not in this game, and
    treating him as if he were is one of the easiest ways to misprice a
    one-run market.
    """
    if not usage:
        return "ok", ""
    u = usage.get(pitcher_id)
    if not u:
        return "ok", ""
    if u.get("apps_3", 0) >= C.PEN_OUT_APPS_3D:
        return "out", f"pitched {u['apps_3']} days running"
    if u.get("pitches_1", 0) >= C.PEN_OUT_PITCHES_1D:
        return "out", f"{int(u['pitches_1'])} pitches yesterday"
    if u.get("pitches_2", 0) >= C.PEN_OUT_PITCHES_2D:
        return "out", f"{int(u['pitches_2'])} pitches in two days"
    if u.get("pitches_1", 0) >= 18 or u.get("pitches_2", 0) >= 35:
        return "tired", "worked recently"
    return "ok", ""


def bullpen_composite(pitchers: list[dict], exclude_id=None,
                      usage: dict | None = None) -> dict:
    """
    One synthetic reliever standing in for the whole pen, as it will actually be
    used tonight.

    Relievers are weighted by batters faced and again by how often the manager
    trusts them late - saves plus holds per appearance - so the arms that pitch
    the seventh through ninth of a close game drive the number instead of the
    long man who ate five runs in a blowout. Then anyone who is unavailable from
    yesterday's work is dropped entirely, and anyone merely worked is both
    downweighted and made slightly worse.
    """
    tot = {k: 0.0 for k in ("bb", "k", "s", "d", "t", "hr")}
    denom = 0.0
    n = 0
    era_w, era_d = 0.0, 0.0
    out_arms, tired_arms = [], []
    for p in pitchers:
        if p["id"] == exclude_id or p.get("is_sp"):
            continue
        tbf = p.get("tbf", 0.0)
        if tbf < 15:
            continue
        state, why = pen_availability(p["id"], usage)
        if state == "out":
            out_arms.append({"name": p.get("name"), "why": why})
            continue
        gp = max(p.get("gp", 1.0), 1.0)
        lev = (p.get("saves", 0.0) + p.get("holds", 0.0)) / gp
        w = 1.0 + min(lev, 0.6) * 1.2
        if state == "tired":
            tired_arms.append({"name": p.get("name"), "why": why})
            w *= 0.55
        for k in tot:
            tot[k] += p["counts"].get(k, 0.0) * w
        denom += tbf * w
        era_w += p.get("era", 0.0) * tbf
        era_d += tbf
        n += 1
    return {"counts": tot, "tbf": denom, "n": n,
            "era": (era_w / era_d) if era_d else 0.0,
            "unavailable": out_arms, "tired": tired_arms}


def starter_rest(sp: dict | None, usage: dict | None, game_date: str) -> tuple[int | None, float]:
    """(days of rest, penalty multiplier). Short rest costs a starter."""
    if not sp or not usage:
        return None, 0.0
    u = usage.get(sp.get("id"))
    last = (u or {}).get("last_start")
    if not last:
        return None, 0.0
    try:
        from datetime import date
        a = date.fromisoformat(game_date)
        b = date.fromisoformat(last)
        days = (a - b).days
    except Exception:
        return None, 0.0
    if days <= 0 or days > 12:
        return days, 0.0
    if days < C.SP_SHORT_REST_DAYS:
        short = (C.SP_SHORT_REST_DAYS - days) / C.SP_SHORT_REST_DAYS
        return days, C.SP_SHORT_REST_PEN * short
    return days, 0.0


def side_matrices(lineup, opp_sp_vec, opp_sp3_vec, opp_pen_vec, league,
                  sp_hand, hr_mult, hit_mult, hfa, bat_vectors=None,
                  pen_hand="R"):
    """
    Build the three 9x7 outcome matrices for one batting side.

    Order of operations matters: matchup first (log5 against that pitcher),
    then platoon, then park and weather, then home field. Each step is a
    multiplier on specific buckets with the in-play out bucket absorbing the
    remainder, so every matrix is still a valid probability distribution.
    """
    P_sp = np.zeros((9, R.NOUT))
    P_sp3 = np.zeros((9, R.NOUT))
    P_pen = np.zeros((9, R.NOUT))
    hfa_hr = 1.0 + 2.2 * hfa
    hfa_hit = 1.0 + 0.6 * hfa
    for i, b in enumerate(lineup):
        # Against the starter, use the hitter's real numbers versus that hand
        # when we have them; the generic platoon constant is the fallback.
        vs_sp = (bat_vectors or {}).get(("sp", i))
        if vs_sp is None:
            bat = R.shrink(b["counts"], b["pa"], league, C.PRIOR_PA_BATTER)
            pl_hr, pl_hit, pl_k = R.platoon_mults(b.get("bats", "R"), sp_hand)
        else:
            bat, pl_hr, pl_hit, pl_k = vs_sp, 1.0, 1.0, 1.0

        v = R.log5(bat, opp_sp_vec, league)
        P_sp[i] = R.apply_multipliers(v, hr_mult * pl_hr * hfa_hr,
                                      hit_mult * pl_hit * hfa_hit, pl_k)

        v3 = R.log5(bat, opp_sp3_vec, league)
        P_sp3[i] = R.apply_multipliers(v3, hr_mult * pl_hr * hfa_hr,
                                       hit_mult * pl_hit * hfa_hit, pl_k)

        vs_pen = (bat_vectors or {}).get(("pen", i))
        bat_pen = vs_pen if vs_pen is not None else R.shrink(
            b["counts"], b["pa"], league, C.PRIOR_PA_BATTER)
        vp = R.log5(bat_pen, opp_pen_vec, league)
        P_pen[i] = R.apply_multipliers(vp, hr_mult * hfa_hr, hit_mult * hfa_hit)
    return P_sp, P_sp3, P_pen


def tto_vector(sp_vec: np.ndarray) -> np.ndarray:
    """The starter's rates the third time through the order."""
    t = C.TTO_PENALTY
    return R.apply_multipliers(sp_vec, 1.0 + 2.0 * t, 1.0 + t, 1.0 - 0.5 * t)

"""
Plate-appearance outcome rates: league baselines, shrinkage, and the
batter-vs-pitcher matchup math the simulator consumes.

Outcome vector order is fixed everywhere in this repo:
    0 BB (walk or HBP)   1 K   2 1B   3 2B   4 3B   5 HR   6 OUT (ball in play)
"""
from __future__ import annotations
import numpy as np

from .. import config as C

OUTCOMES = ["bb", "k", "s", "d", "t", "hr", "out"]
NOUT = 7
I_BB, I_K, I_1B, I_2B, I_3B, I_HR, I_OUT = range(7)

# Fallback league baseline (modern MLB). Overwritten by the real league
# aggregate as soon as we have rosters, but keeps the model runnable cold.
LEAGUE_FALLBACK = np.array([0.093, 0.222, 0.140, 0.045, 0.004, 0.034, 0.462])


def counts_to_vec(counts: dict, denom: float) -> np.ndarray:
    """Raw counts -> rate vector, with the OUT bucket as the remainder."""
    v = np.zeros(NOUT)
    if denom <= 0:
        return LEAGUE_FALLBACK.copy()
    v[I_BB] = counts.get("bb", 0.0)
    v[I_K] = counts.get("k", 0.0)
    v[I_1B] = counts.get("s", 0.0)
    v[I_2B] = counts.get("d", 0.0)
    v[I_3B] = counts.get("t", 0.0)
    v[I_HR] = counts.get("hr", 0.0)
    v[I_OUT] = max(denom - v.sum(), 0.0)
    return v / max(v.sum(), 1e-9)


def league_baseline(all_batters: list[dict]) -> np.ndarray:
    """Aggregate every batter's counts into one league-wide rate vector."""
    tot = {k: 0.0 for k in ("bb", "k", "s", "d", "t", "hr")}
    pa = 0.0
    for b in all_batters:
        for k in tot:
            tot[k] += b["counts"].get(k, 0.0)
        pa += b["pa"]
    if pa < 5000:
        return LEAGUE_FALLBACK.copy()
    return counts_to_vec(tot, pa)


def shrink(counts: dict, denom: float, league: np.ndarray, prior: float) -> np.ndarray:
    """Regress a small sample toward the league mean by `prior` PA of it."""
    v = np.zeros(NOUT)
    v[I_BB] = counts.get("bb", 0.0)
    v[I_K] = counts.get("k", 0.0)
    v[I_1B] = counts.get("s", 0.0)
    v[I_2B] = counts.get("d", 0.0)
    v[I_3B] = counts.get("t", 0.0)
    v[I_HR] = counts.get("hr", 0.0)
    v[I_OUT] = max(denom - v.sum(), 0.0)
    v = v + league * prior
    s = v.sum()
    return v / s if s > 0 else league.copy()


def log5(bat: np.ndarray, pit: np.ndarray, lg: np.ndarray) -> np.ndarray:
    """
    Multinomial odds-ratio matchup (the Bill James log5 idea generalised past
    a two-outcome coin flip). Each outcome's expected rate is the batter rate
    times the pitcher rate over the league rate, then renormalised.
    """
    raw = (bat * pit) / np.maximum(lg, 1e-9)
    s = raw.sum()
    return raw / s if s > 0 else lg.copy()


def apply_multipliers(vec: np.ndarray, hr_mult: float = 1.0, hit_mult: float = 1.0,
                      k_mult: float = 1.0, bb_mult: float = 1.0) -> np.ndarray:
    """
    Scale specific buckets (park, weather, platoon, times-through-order) and let
    the in-play OUT bucket absorb the difference so the vector still sums to 1.
    """
    v = vec.copy()
    v[I_HR] *= hr_mult
    v[I_1B] *= hit_mult
    v[I_2B] *= hit_mult
    v[I_3B] *= hit_mult
    v[I_K] *= k_mult
    v[I_BB] *= bb_mult
    non_out = v[:I_OUT].sum()
    if non_out >= 0.98:                      # pathological; renormalise instead
        return v / v.sum()
    v[I_OUT] = 1.0 - non_out
    return v


def park_weather_mults(park: dict, wx: dict) -> tuple[float, float]:
    """
    Convert a park factor and a weather run multiplier into (hr_mult, hit_mult).

    Home runs carry most of both effects; singles/doubles move roughly a fifth
    as much. Park run factor contributes the non-HR share so a Fenway (big run
    factor, small HR factor) reads as doubles rather than homers.
    """
    hr_pf = float(park.get("hr", 100)) / 100.0
    run_pf = float(park.get("run", 100)) / 100.0
    wx_m = float(wx.get("run_mult", 1.0))

    hr_mult = hr_pf * (1.0 + 2.2 * (wx_m - 1.0))
    # non-HR hits: driven by the part of the run factor the HR factor doesn't explain
    hit_mult = (1.0 + 0.75 * (run_pf - 1.0) - 0.25 * (hr_pf - 1.0)) * (1.0 + 0.5 * (wx_m - 1.0))
    return float(np.clip(hr_mult, 0.70, 1.45)), float(np.clip(hit_mult, 0.85, 1.20))


def platoon_mults(bat_side: str, pitch_hand: str) -> tuple[float, float, float]:
    """(hr_mult, hit_mult, k_mult) for a hand matchup. Switch hitters always
    take the platoon advantage, which is the whole point of switch hitting."""
    b = (bat_side or "R").upper()
    p = (pitch_hand or "R").upper()
    if b == "S":
        adv = True
    else:
        adv = (b != p)
    if adv:
        f = 1.0 + C.PLATOON_ADV
        return f, f, 1.0 - C.PLATOON_ADV * 0.6
    f = 1.0 - C.PLATOON_DIS
    return f, f, 1.0 + C.PLATOON_DIS * 0.6


def baserunning_index(batters: list[dict]) -> float:
    """
    Team speed proxy from stolen bases per plate appearance, mapped onto a
    multiplier for extra-base advancement (0.90 slow .. 1.10 fast).
    """
    pa = sum(b["pa"] for b in batters) or 1.0
    sb = sum(b.get("sb", 0.0) for b in batters)
    rate = sb / pa
    return float(np.clip(0.90 + (rate / 0.020) * 0.10, 0.88, 1.12))


def blend_windows(season_counts: dict, season_denom: float,
                  recent_counts: dict | None, recent_denom: float,
                  league: np.ndarray, prior: float, recent_weight: float,
                  min_recent: float) -> np.ndarray:
    """
    Season line with a rolling window mixed in.

    Season-to-date is the right base - it has the sample. But it is slow to
    notice a hitter who stopped hitting in June, and by September a bad month is
    only a tenth of the total. Mixing a weighted copy of the recent window into
    the counts moves the estimate without throwing away the sample.
    """
    counts = dict(season_counts)
    denom = float(season_denom)
    # A feed that returns counts adding up to more than the plate appearances
    # it claims is broken, and blending it produces run environments that are
    # not baseball. Drop the window rather than trust it.
    if recent_counts and sum(recent_counts.values()) > recent_denom * 1.02:
        recent_counts = None
    if recent_counts and recent_denom >= min_recent and recent_weight > 0:
        # add the window a second time, scaled, so it pulls without dominating
        extra = (recent_weight / max(1.0 - recent_weight, 1e-6))
        scale = extra * (season_denom / max(recent_denom, 1.0))
        scale = min(scale, 3.0)
        for k in counts:
            counts[k] = counts.get(k, 0.0) + recent_counts.get(k, 0.0) * scale
        denom += recent_denom * scale
    return shrink(counts, denom, league, prior)


def regress_hr(vec: np.ndarray, league: np.ndarray, strength: float) -> np.ndarray:
    """
    Pull a pitcher's home run rate toward league average.

    Home runs per ball in the air is the noisiest thing a pitcher appears to
    control, and most of a surprising rate is the ballpark, the weather and luck
    rather than the arm. This is the idea behind xFIP, applied directly to the
    outcome vector so it flows through the simulation.
    """
    if strength <= 0:
        return vec
    v = vec.copy()
    target = league[I_HR]
    v[I_HR] = v[I_HR] * (1 - strength) + target * strength
    s = v.sum()
    return v / s if s > 0 else vec


def split_vector(overall_counts: dict, overall_pa: float,
                 split_counts: dict | None, split_pa: float,
                 league: np.ndarray, prior_overall: float,
                 prior_split: float) -> np.ndarray:
    """
    A hitter's rates against this specific hand, regressed toward his own
    overall line rather than toward the league - a split is a small sample of a
    player we already know a lot about.
    """
    overall = shrink(overall_counts, overall_pa, league, prior_overall)
    if not split_counts or split_pa <= 0:
        return overall
    if sum(split_counts.values()) > split_pa * 1.02:      # same guard
        return overall
    v = np.zeros(NOUT)
    v[I_BB] = split_counts.get("bb", 0.0)
    v[I_K] = split_counts.get("k", 0.0)
    v[I_1B] = split_counts.get("s", 0.0)
    v[I_2B] = split_counts.get("d", 0.0)
    v[I_3B] = split_counts.get("t", 0.0)
    v[I_HR] = split_counts.get("hr", 0.0)
    v[I_OUT] = max(split_pa - v.sum(), 0.0)
    v = v + overall * prior_split
    s = v.sum()
    return v / s if s > 0 else overall


def team_der(counts: dict, tbf: float) -> float | None:
    """
    Defensive efficiency: the share of balls put in play that the defense turns
    into outs. Covers the part of run prevention the pitcher's own strikeout,
    walk and home run rates cannot explain.
    """
    if tbf <= 0:
        return None
    bip = tbf - counts.get("k", 0.0) - counts.get("bb", 0.0) - counts.get("hr", 0.0)
    if bip <= 0:
        return None
    hits_in_play = counts.get("s", 0.0) + counts.get("d", 0.0) + counts.get("t", 0.0)
    return float(np.clip(1.0 - hits_in_play / bip, 0.55, 0.80))


def defense_mult(der: float | None, league_der: float | None, strength: float) -> float:
    """A better defense turns more balls in play into outs - so fewer hits."""
    if der is None or league_der is None or league_der <= 0:
        return 1.0
    gap = (der - league_der) / max(1.0 - league_der, 1e-6)
    return float(np.clip(1.0 - gap * strength, 0.90, 1.10))

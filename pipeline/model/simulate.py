"""
Plate-appearance level Monte Carlo baseball simulator.

This is not a run-differential-to-win-probability formula. It plays the game:
nine hitters in order, base-out state, forced advances, sacrifice flies,
double plays, a starter who tires and hands off to a bullpen, the home team
skipping the bottom of the ninth when it is ahead, walk-offs, and the extra
innings ghost runner. Everything is vectorised over simulations with numpy,
so 20,000 trials of a full game costs a fraction of a second.

Outputs the joint distribution of away runs, home runs, and both first-five
totals, which is what lets one engine price the moneyline, the run line, the
total and the F5 markets consistently instead of bolting on conversions.
"""
from __future__ import annotations
import numpy as np

from .rates import I_BB, I_K, I_1B, I_2B, I_3B, I_HR, I_OUT

# base-running constants (league-typical advancement rates)
P_2ND_SCORES_ON_1B = 0.60
P_1ST_TO_3RD_ON_1B = 0.28
P_1ST_SCORES_ON_2B = 0.45
P_GIDP             = 0.11
P_SAC_FLY          = 0.28
P_PRODUCTIVE_OUT   = 0.20
MAX_INNING         = 21     # 12 extra innings is a hard stop


class SidePack:
    """Everything one batting side needs: three 9x7 matrices and a workload."""

    def __init__(self, p_sp, p_sp3, p_pen, bf_mean, bf_sd, bf_min, bf_max, adv=1.0,
                 no_starter=False):
        self.p_sp = np.ascontiguousarray(p_sp, dtype=np.float64)     # (9,7) vs SP, 1st/2nd time
        self.p_sp3 = np.ascontiguousarray(p_sp3, dtype=np.float64)   # (9,7) vs SP, 3rd time+
        self.p_pen = np.ascontiguousarray(p_pen, dtype=np.float64)   # (9,7) vs bullpen
        self.c_sp = self.p_sp.cumsum(1)
        self.c_sp3 = self.p_sp3.cumsum(1)
        self.c_pen = self.p_pen.cumsum(1)
        self.bf_mean, self.bf_sd = bf_mean, bf_sd
        self.bf_min, self.bf_max = bf_min, bf_max
        self.adv = float(adv)               # baserunning index
        self.no_starter = bool(no_starter)  # bullpen game / TBA


class _State:
    def __init__(self, pack: SidePack, n: int, rng: np.random.Generator):
        self.pack = pack
        self.n = n
        self.runs = np.zeros(n, dtype=np.int32)
        self.pos = np.zeros(n, dtype=np.int64)       # lineup slot 0-8
        self.sp_bf = np.zeros(n, dtype=np.int32)     # batters the starter has faced
        if pack.no_starter:
            self.bf_limit = np.zeros(n, dtype=np.int32)
        else:
            lim = rng.normal(pack.bf_mean, pack.bf_sd, n)
            self.bf_limit = np.clip(np.round(lim), pack.bf_min, pack.bf_max).astype(np.int32)


def _half_inning(st: _State, active: np.ndarray, rng: np.random.Generator,
                 ghost: bool = False, walkoff_vs: np.ndarray | None = None) -> None:
    """
    Play one half inning for every simulation flagged in `active`.

    walkoff_vs: opponent run totals. When supplied, a sim stops the moment the
    batting team goes ahead (bottom of the 9th or later).
    """
    n = st.n
    outs = np.zeros(n, dtype=np.int8)
    b1 = np.zeros(n, dtype=bool)
    b2 = np.zeros(n, dtype=bool)
    b3 = np.zeros(n, dtype=bool)
    if ghost:
        b2 |= active

    live = active.copy()
    if walkoff_vs is not None:
        live &= ~(st.runs > walkoff_vs)

    pack = st.pack
    adv = pack.adv
    guard = 0
    while live.any():
        guard += 1
        if guard > 200:                     # impossible, but never hang a CI job
            break
        li = np.flatnonzero(live)
        m = li.size
        idx = st.pos[li]
        bf = st.sp_bf[li]
        lim = st.bf_limit[li]

        use_pen = bf >= lim
        tto3 = (~use_pen) & (bf >= 18)
        cum = np.where(use_pen[:, None], pack.c_pen[idx],
                       np.where(tto3[:, None], pack.c_sp3[idx], pack.c_sp[idx]))
        u = rng.random(m)
        oc = (u[:, None] < cum).argmax(1)

        r1, r2, r3 = b1[li], b2[li], b3[li]
        o = outs[li]
        gained = np.zeros(m, dtype=np.int32)
        n1 = r1.copy(); n2 = r2.copy(); n3 = r3.copy()
        no = o.copy()
        rnd = rng.random((m, 3))

        # ---- walk / HBP: forced advance only
        s = oc == I_BB
        if s.any():
            gained[s] += (r1 & r2 & r3)[s]
            n3[s] = (r3 | (r1 & r2))[s]
            n2[s] = (r2 | r1)[s]
            n1[s] = True

        # ---- strikeout
        s = oc == I_K
        if s.any():
            no[s] += 1

        # ---- single
        s = oc == I_1B
        if s.any():
            sc2 = r2 & (rnd[:, 0] < min(P_2ND_SCORES_ON_1B * adv, 0.85))
            to3 = r1 & (rnd[:, 1] < min(P_1ST_TO_3RD_ON_1B * adv, 0.50))
            gained[s] += (r3.astype(np.int32) + sc2.astype(np.int32))[s]
            n3[s] = ((r2 & ~sc2) | to3)[s]
            n2[s] = (r1 & ~to3)[s]
            n1[s] = True

        # ---- double
        s = oc == I_2B
        if s.any():
            sc1 = r1 & (rnd[:, 0] < min(P_1ST_SCORES_ON_2B * adv, 0.70))
            gained[s] += (r3.astype(np.int32) + r2.astype(np.int32) + sc1.astype(np.int32))[s]
            n3[s] = (r1 & ~sc1)[s]
            n2[s] = True
            n1[s] = False

        # ---- triple
        s = oc == I_3B
        if s.any():
            gained[s] += (r1.astype(np.int32) + r2.astype(np.int32) + r3.astype(np.int32))[s]
            n3[s] = True
            n2[s] = False
            n1[s] = False

        # ---- home run
        s = oc == I_HR
        if s.any():
            gained[s] += (1 + r1.astype(np.int32) + r2.astype(np.int32) + r3.astype(np.int32))[s]
            n1[s] = False; n2[s] = False; n3[s] = False

        # ---- ball in play, out
        s = oc == I_OUT
        if s.any():
            gidp = s & r1 & (o == 0) & (rnd[:, 0] < P_GIDP)
            gidp1 = s & r1 & (o == 1) & (rnd[:, 0] < P_GIDP)
            sacf = s & ~gidp & ~gidp1 & r3 & (o < 2) & (rnd[:, 1] < min(P_SAC_FLY * adv, 0.45))
            prod = s & ~gidp & ~gidp1 & ~sacf & r2 & ~r3 & (o < 2) & (rnd[:, 2] < P_PRODUCTIVE_OUT)
            plain = s & ~gidp & ~gidp1 & ~sacf & ~prod

            # double play with nobody out: two outs, runner from third scores
            no[gidp] += 2
            gained[gidp] += r3[gidp].astype(np.int32)
            n1[gidp] = False
            n3[gidp] = False
            # double play with one out: inning over, nothing scores
            no[gidp1] += 2
            n1[gidp1] = False
            # sacrifice fly
            no[sacf] += 1
            gained[sacf] += 1
            n3[sacf] = False
            # productive groundout moves the runner up
            no[prod] += 1
            n3[prod] = True
            n2[prod] = False
            # ordinary out
            no[plain] += 1

        b1[li], b2[li], b3[li] = n1, n2, n3
        outs[li] = no
        st.runs[li] += gained
        st.pos[li] = (idx + 1) % 9
        st.sp_bf[li] = bf + (~use_pen).astype(np.int32)

        still = outs[li] < 3
        if walkoff_vs is not None:
            still &= ~(st.runs[li] > walkoff_vs[li])
        live[:] = False
        live[li[still]] = True


def simulate_game(away: SidePack, home: SidePack, n_sims: int,
                  seed: int = 0) -> dict:
    """Play `n_sims` complete games. Returns run arrays for both sides."""
    rng = np.random.default_rng(seed)
    A = _State(away, n_sims, rng)
    H = _State(home, n_sims, rng)
    all_on = np.ones(n_sims, dtype=bool)
    f5a = f5h = None
    i1a = i1h = None

    for inning in range(1, 10):
        _half_inning(A, all_on, rng)
        if inning == 9:
            # home bats in the ninth only when it is not already ahead
            need = H.runs <= A.runs
            _half_inning(H, need, rng, walkoff_vs=A.runs)
        else:
            _half_inning(H, all_on, rng)
        if inning == 1:
            i1a, i1h = A.runs.copy(), H.runs.copy()
        if inning == 5:
            f5a, f5h = A.runs.copy(), H.runs.copy()

    inning = 10
    tied = A.runs == H.runs
    while tied.any() and inning <= MAX_INNING:
        _half_inning(A, tied, rng, ghost=True)
        _half_inning(H, tied, rng, ghost=True, walkoff_vs=A.runs)
        tied = (A.runs == H.runs)
        inning += 1
    if tied.any():                      # vanishingly rare; break the tie fairly
        coin = rng.random(tied.sum()) < 0.5
        idx = np.flatnonzero(tied)
        H.runs[idx[coin]] += 1
        A.runs[idx[~coin]] += 1

    return {"away": A.runs.astype(np.int32), "home": H.runs.astype(np.int32),
            "f5_away": f5a.astype(np.int32), "f5_home": f5h.astype(np.int32),
            "i1_away": i1a.astype(np.int32), "i1_home": i1h.astype(np.int32),
            "n": n_sims}


# ------------------------------------------------------------- derivations ---
def derive(sim: dict, market_total: float | None, rl_line: float = -1.5,
           f5_total: float | None = None) -> dict:
    """Turn the raw run distributions into every probability the model prices."""
    a, h = sim["away"], sim["home"]
    n = float(sim["n"])
    tot = a + h
    marg = h - a

    p_home = float((h > a).mean())
    p_away = 1.0 - p_home
    se = float(np.sqrt(max(p_home * (1 - p_home), 1e-9) / n))

    # Run line, handled with a signed line so it is correct whichever side is
    # laying the run: home covers when (home - away) + line > 0.
    p_home_rl = float((marg + rl_line > 0).mean())
    p_away_rl = 1.0 - p_home_rl

    out = {
        "p_home": p_home, "p_away": p_away, "se": se,
        "mean_away": float(a.mean()), "mean_home": float(h.mean()),
        "mean_total": float(tot.mean()), "median_total": float(np.median(tot)),
        "mean_margin": float(marg.mean()),
        "p_home_rl": p_home_rl, "p_away_rl": p_away_rl,
        "p_f5_home": float((sim["f5_home"] > sim["f5_away"]).mean()),
        "p_f5_away": float((sim["f5_away"] > sim["f5_home"]).mean()),
        "p_f5_tie": float((sim["f5_away"] == sim["f5_home"]).mean()),
        "mean_f5_total": float((sim["f5_away"] + sim["f5_home"]).mean()),
        "mean_f5_away": float(sim["f5_away"].mean()),
        "mean_f5_home": float(sim["f5_home"].mean()),
        "hist": _hist(tot, 0, 22),
        "margin_hist": _hist(marg, -10, 10),
        # First-inning market: a scoreless first is the single most-bet MLB
        # derivative and it falls straight out of the same simulation.
        "p_nrfi": float(((sim["i1_away"] + sim["i1_home"]) == 0).mean()),
        "p_yrfi": float(((sim["i1_away"] + sim["i1_home"]) > 0).mean()),
        "mean_i1": float((sim["i1_away"] + sim["i1_home"]).mean()),
        # Team totals, from each side's own run distribution.
        "team_total_away": _team_total(a),
        "team_total_home": _team_total(h),
        "p_away_shutout": float((a == 0).mean()),
        "p_home_shutout": float((h == 0).mean()),
        "p_extras": float((np.abs(marg) == 0).mean()),
    }

    if market_total is not None:
        out.update(_ou(tot, market_total, "total"))
    if f5_total is not None:
        out.update(_ou(sim["f5_away"] + sim["f5_home"], f5_total, "f5"))
    out["fair_total"] = float(np.median(tot))
    out["fair_f5_total"] = float(np.median(sim["f5_away"] + sim["f5_home"]))
    return out


def _team_total(runs: np.ndarray) -> dict:
    """Fair line and over/under probabilities for one team's run total."""
    line = round(float(np.median(runs)) * 2) / 2
    if line == int(line):
        line += 0.5                                   # avoid a pushable line
    return {"line": line,
            "over": float((runs > line).mean()),
            "under": float((runs < line).mean()),
            "mean": float(runs.mean())}


def team_total_ou(runs: np.ndarray, line: float) -> dict:
    over = float((runs > line).mean())
    under = float((runs < line).mean())
    push = float((runs == line).mean())
    if push > 0:
        d = over + under
        over = over / d if d else 0.5
        under = 1.0 - over
    return {"over": over, "under": under, "push": push}


def _ou(tot: np.ndarray, line: float, prefix: str) -> dict:
    over = float((tot > line).mean())
    under = float((tot < line).mean())
    push = float((tot == line).mean())
    if push > 0:                            # integer line: redistribute the push
        denom = over + under
        over_n = over / denom if denom else 0.5
        under_n = 1.0 - over_n
    else:
        over_n, under_n = over, under
    return {f"p_{prefix}_over": over_n, f"p_{prefix}_under": under_n,
            f"p_{prefix}_push": push}


def _hist(x: np.ndarray, lo: int, hi: int) -> list[int]:
    clipped = np.clip(x, lo, hi)
    counts = np.bincount((clipped - lo).astype(np.int64), minlength=hi - lo + 1)
    return counts[: hi - lo + 1].astype(int).tolist()

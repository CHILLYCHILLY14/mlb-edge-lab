"""
Portfolio rules applied across the whole slate, after every game is priced.

A per-bet edge calculation has no idea that you are about to back the same team
on the moneyline and the run line, or that you have found eight best bets on a
Tuesday. These rules run last and are allowed to overrule any individual bet.
"""
from __future__ import annotations

from .. import config as C

SIDE_MARKETS = {"ML", "RL", "F5 ML"}
TOTAL_MARKETS = {"TOTAL", "F5 TOTAL"}


def apply(slate: dict) -> dict:
    """Mutates `slate` in place; returns a summary of what was overruled."""
    games = slate.get("games", [])
    notes = {"correlated_suppressed": 0, "best_downgraded": 0,
             "plays_trimmed": 0, "exposure_scaled": False,
             "divergence_flag": False, "scale_factor": 1.0}

    # ---- 1. one side bet and one total bet per game -------------------------
    if C.ONE_SIDE_BET_PER_GAME:
        for g in games:
            for family in (SIDE_MARKETS, TOTAL_MARKETS):
                live = [b for b in g["bets"]
                        if b["market"] in family and b["stake"] > 0]
                if len(live) <= 1:
                    continue
                live.sort(key=lambda b: -b["edge"])
                keep = live[0]
                for b in live[1:]:
                    b["stake"] = 0.0
                    b["to_win"] = 0.0
                    b["suppressed"] = f"correlated with {keep['label']}"
                    notes["correlated_suppressed"] += 1

    # ---- 2. is the whole slate suspect? -------------------------------------
    # The old test counted how many MARKETS reached best-bet tier and compared
    # that to a multiple of the number of GAMES - incoherent units, and it fired
    # on healthy slates. What actually matters is whether the model is
    # systematically apart from the market or merely disagrees about a few
    # games, so measure the median gap and say what it was.
    gaps = []
    for g in games:
        ml = [b for b in g["bets"] if b["market"] == "ML" and b.get("p_market") is not None]
        if len(ml) == 2:
            gaps.append(abs(ml[0]["p_final"] - ml[0]["p_market"]))
    if len(gaps) >= C.DIVERGENCE_MIN_GAMES:
        gaps.sort()
        mid = len(gaps) // 2
        med = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
        notes["median_gap"] = round(med, 4)
        notes["divergence_flag"] = med > C.DIVERGENCE_MEDIAN_GAP
    notes["priced_games"] = len(gaps)

    bests = [(g, b) for g in games for b in g["bets"] if b["tier"] == "BEST BET"]
    bests.sort(key=lambda t: -t[1]["edge"])
    for _, b in bests[C.MAX_BEST_BETS_PER_SLATE:]:
        b["tier"] = "GOOD"
        b.setdefault("lock_fails", []).append(
            f"only the top {C.MAX_BEST_BETS_PER_SLATE} edges on a slate can be a best bet")
        notes["best_downgraded"] += 1

    # ---- 3. cap the number of staked plays ----------------------------------
    # Rank by tier first, then edge. Two bets can sit a hundredth of a percent
    # apart on compressed edge while one of them cleared every lock rule and the
    # other did not; when the daily limit bites, the one that cleared them wins.
    rank = {"BEST BET": 0, "GOOD": 1, "LEAN": 2, "PASS": 3}
    live = [b for g in games for b in g["bets"] if b["stake"] > 0]
    live.sort(key=lambda b: (rank.get(b["tier"], 9), -b["edge"]))
    for b in live[C.MAX_PLAYS_PER_SLATE:]:
        b["stake"] = 0.0
        b["to_win"] = 0.0
        b["suppressed"] = f"outside the top {C.MAX_PLAYS_PER_SLATE} plays today"
        notes["plays_trimmed"] += 1
    live = live[:C.MAX_PLAYS_PER_SLATE]

    # ---- 4. cap total money at risk on one slate ----------------------------
    total = sum(b["stake"] for b in live)
    cap = C.BANKROLL * C.MAX_SLATE_EXPOSURE_PCT
    if total > cap and total > 0:
        f = cap / total
        notes["exposure_scaled"] = True
        notes["scale_factor"] = round(f, 3)
        for b in live:
            b["stake"] = round(b["stake"] * f / C.STAKE_ROUNDING) * C.STAKE_ROUNDING
            b["to_win"] = round(b["stake"] * (b["decimal"] - 1.0), 2)

    for g in games:
        g["best"] = max(g["bets"], key=lambda b: b["edge"]) if g["bets"] else None

    notes["staked"] = round(sum(b["stake"] for g in games for b in g["bets"]), 2)
    notes["n_plays"] = sum(1 for g in games for b in g["bets"] if b["stake"] > 0)
    notes["n_best"] = sum(1 for g in games for b in g["bets"] if b["tier"] == "BEST BET")
    slate["portfolio"] = notes
    return notes

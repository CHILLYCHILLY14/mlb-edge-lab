"""
Odds mathematics: American prices, vig removal, model/market blending,
edge compression, Kelly staking and tiering.

The compression step is the part that matters. A raw simulator will happily
tell you a 22% edge exists. It almost never does - that number is model error,
not market error. Every edge is squashed through tanh toward a hard ceiling and
the stake is computed from the compressed number, so a wild readout produces a
sane bet instead of a bankroll-ending one.
"""
from __future__ import annotations
import math

from .. import config as C


# ------------------------------------------------------------ price maths ---
def price_ok(a) -> bool:
    """A real American price: not blank, not zero, not beyond any real board.

    Feeds publish 0 for a market they have not hung yet. That is not a price of
    even money - it is the absence of one - and letting it through divides by
    zero on the way to a decimal payout.
    """
    if a is None:
        return False
    try:
        a = float(a)
    except (TypeError, ValueError):
        return False
    if a != a or a in (float("inf"), float("-inf")):
        return False
    return 100.0 <= abs(a) <= 5000.0


def american_to_decimal(a: float) -> float:
    """Decimal payout. Junk in gives 1.0 - stake back, no profit - so a bad
    number can never look like the best price on the board."""
    if not price_ok(a):
        return 1.0
    a = float(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def american_to_prob(a: float) -> float:
    """Implied probability. Junk in gives 0.5 rather than raising."""
    if not price_ok(a):
        return 0.5
    a = float(a)
    return 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)


def prob_to_american(p: float) -> float:
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


def fmt_american(a) -> str:
    if a is None:
        return "-"
    a = float(a)
    return f"{a:+.0f}" if a > 0 else f"{a:.0f}"


def devig_power(p_a: float, p_b: float, iters: int = 60) -> tuple[float, float]:
    """
    Remove vig with the power method: find k such that p_a^k + p_b^k = 1.

    Better than proportional de-vigging because sportsbook margin is not spread
    evenly - favourites carry less of it than longshots, and on a -250 MLB
    moneyline the difference between the two methods is real money.
    """
    if p_a <= 0 or p_b <= 0:
        s = p_a + p_b
        return (p_a / s, p_b / s) if s > 0 else (0.5, 0.5)
    lo, hi = 0.5, 2.0
    for _ in range(iters):
        k = (lo + hi) / 2
        s = p_a ** k + p_b ** k
        if s > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    a, b = p_a ** k, p_b ** k
    s = a + b
    return a / s, b / s


def no_vig(price_a, price_b) -> tuple[float | None, float | None]:
    # Both sides have to be real prices. Half a market de-vigged against a
    # placeholder is not a market opinion, and it would become the number every
    # edge on that game is measured against.
    if not price_ok(price_a) or not price_ok(price_b):
        return None, None
    return devig_power(american_to_prob(price_a), american_to_prob(price_b))


# ----------------------------------------------------------- model blending --
def blend(p_model: float, p_market: float | None, weight: float) -> float:
    """Pull the model toward the no-vig market price by `weight`."""
    if p_market is None:
        return p_model
    return (1.0 - weight) * p_model + weight * p_market


def cap_prob(p: float) -> float:
    return min(max(p, 1.0 - C.MAX_MODEL_PROB), C.MAX_MODEL_PROB)


# ---------------------------------------------------------------- edges ------
def raw_edge(p: float, price: float) -> float:
    """Expected value per $1 staked."""
    return p * american_to_decimal(price) - 1.0


def compress(edge: float, ceiling: float) -> float:
    """tanh squash toward `ceiling`; near zero it is almost the identity."""
    if ceiling <= 0:
        return 0.0
    return ceiling * math.tanh(edge / ceiling)


def kelly_stake(edge_c: float, price: float, bankroll: float) -> tuple[float, float]:
    """
    Fractional Kelly from the COMPRESSED edge. Returns (stake, kelly_fraction).
    """
    if edge_c <= 0:
        return 0.0, 0.0
    dec = american_to_decimal(price)
    b = dec - 1.0
    p_eff = (1.0 + edge_c) / dec        # the probability the compressed edge implies
    q = 1.0 - p_eff
    f = (b * p_eff - q) / b if b > 0 else 0.0
    f = max(f, 0.0) * C.KELLY_FRACTION
    f = min(f, C.MAX_STAKE_PCT)
    stake = bankroll * f
    stake = round(stake / C.STAKE_ROUNDING) * C.STAKE_ROUNDING
    if stake < C.MIN_STAKE:
        stake = 0.0
    return stake, f


# ---------------------------------------------------------------- tiering ----
def tier_for(edge_c: float) -> str:
    if edge_c >= C.TIER_BEST:
        return "BEST BET"
    if edge_c >= C.TIER_GOOD:
        return "GOOD"
    if edge_c >= C.TIER_LEAN:
        return "LEAN"
    return "PASS"


def lock_rules(*, price, p_model, p_market, odds_age_h, sim_se,
               both_sp, precip, is_total=False, total_gap=None) -> list[str]:
    """
    Return the list of reasons this call cannot be a BEST BET. Empty list means
    it survived. Every rule exists because its absence cost money somewhere.
    """
    fails = []
    if price is None:
        return ["no price"]
    if price < C.LOCK_MIN_PRICE:
        fails.append(f"price {fmt_american(price)} heavier than {C.LOCK_MIN_PRICE}")
    if price > C.LOCK_MAX_PRICE:
        fails.append(f"price {fmt_american(price)} longer than +{C.LOCK_MAX_PRICE}")
    if p_market is not None:
        gap = abs(p_model - p_market)
        if gap < C.LOCK_MIN_PROB_GAP:
            fails.append("model too close to market")
        if gap > C.LOCK_MAX_PROB_GAP:
            fails.append(f"model {gap*100:.0f} pts off market - treated as data noise")
    if odds_age_h is not None and odds_age_h > C.LOCK_MAX_ODDS_AGE_H:
        fails.append(f"odds {odds_age_h:.1f}h stale")
    if sim_se is not None and sim_se > C.LOCK_MAX_SIM_SE:
        fails.append("simulation not converged")
    if C.LOCK_REQUIRE_BOTH_SP and not both_sp:
        fails.append("starter not confirmed")
    if precip is not None and precip > C.LOCK_MAX_PRECIP:
        fails.append(f"{precip*100:.0f}% rain risk")
    if is_total and total_gap is not None:
        if total_gap < C.LOCK_MIN_TOTAL_GAP:
            fails.append("total too close to market")
        if total_gap > C.LOCK_MAX_TOTAL_GAP:
            fails.append(f"total {total_gap:.1f} runs off market - treated as data noise")
    return fails

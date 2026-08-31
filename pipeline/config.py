"""
MLB Edge - central configuration.

Everything tunable lives here. Nothing else in the pipeline hard-codes a knob.
"""
from __future__ import annotations
import os
import sys


def _positive_number(name: str, default: float, *, integer: bool = False):
    """Read a friendly GitHub variable such as ``$1,250.50`` safely."""
    raw = os.environ.get(name)
    if raw is None:
        return int(default) if integer else float(default)
    try:
        cleaned = str(raw).strip().replace("$", "").replace(",", "")
        value = int(cleaned) if integer else float(cleaned)
        if value <= 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        print(f"invalid {name}={raw!r}; using {default}", file=sys.stderr)
        return int(default) if integer else float(default)

# ---------------------------------------------------------------- bankroll ---
BANKROLL          = _positive_number("MLB_BANKROLL", 250.0)
KELLY_FRACTION    = 0.25    # fractional Kelly multiplier
MAX_STAKE_PCT     = 0.05    # hard cap: 5% of bankroll on any single bet
MIN_STAKE         = 1.00    # do not record a bet smaller than this
STAKE_ROUNDING    = 0.50    # round stakes to nearest $0.50

# --------------------------------------------------------------- your book ---
# Which sportsbook's number you actually see when you go to bet. The dashboard
# shows THIS book's price wherever it quotes the market, so the number on the
# card is the number in your app. Where it has not posted one, you get the best
# price any book in the feed is showing, labelled with whose it is.
#
# Set to None to always show the best available price across every book.
# Spelling has to match ESPN's, which is why the aliases below exist.
PREFERRED_BOOK    = "DraftKings"
BOOK_ALIASES      = {
    "draftkings": "DraftKings", "dk": "DraftKings",
    "fanduel": "FanDuel", "fd": "FanDuel",
    "espn bet": "ESPN BET", "espnbet": "ESPN BET",
    "caesars": "Caesars", "william hill (new jersey)": "Caesars",
    "betmgm": "BetMGM", "mgm": "BetMGM",
    "bet365": "Bet365", "b365": "Bet365",
}

# A price is a price or it is nothing. Never invent one - a market with no book
# behind it gets the model's own fair line, clearly labelled as a fair line, and
# is not bettable. Leave this False. It exists only to name the rule.
INVENT_MISSING_PRICES = False

# ------------------------------------------------------------------ market ---
# The first 114 head-to-head grades showed the market at a 0.201 Brier score
# and the 40%-anchored model at 0.217. That is useful evidence, not a reason to
# pretend the simulator has more signal than it has. A 55% market anchor keeps
# matchup information in the number while reducing overconfidence until the
# longer calibration history is ready.
MARKET_BLEND      = 0.55
TOTALS_BLEND      = 0.50    # totals markets are sharper still
F5_BLEND          = 0.40    # thinner market, but still anchor noisy inputs

# Edge compression. Raw EV is squashed through tanh toward a hard ceiling so a
# noisy 22% "edge" becomes a believable one. Kelly is sized off the COMPRESSED
# number, never the raw one.
EDGE_CEILING      = 0.055   # moneyline / run line
EDGE_CEILING_TOT  = 0.045   # totals
MAX_MODEL_PROB    = 0.80    # cap modeled win prob before staking

# ------------------------------------------------------------------- tiers ---
TIER_BEST         = 0.035
TIER_GOOD         = 0.025
TIER_LEAN         = 0.012

# BEST BET lock rules - all must pass
LOCK_MIN_PRICE    = -175    # no heavier chalk than this
LOCK_MAX_PRICE    =  160    # no bigger dog than this
LOCK_MIN_PROB_GAP = 0.035   # must actually disagree with the market
LOCK_MAX_PROB_GAP = 0.120   # ...but not absurdly (that means bad data)
LOCK_MAX_ODDS_AGE_H = 6.0   # odds must be fresher than this
LOCK_MAX_SIM_SE   = 0.0040  # Monte Carlo standard error ceiling
LOCK_REQUIRE_BOTH_SP = True # both probable starters must be named
LOCK_MAX_PRECIP   = 0.60    # skip likely rainouts
LOCK_MIN_TOTAL_GAP = 0.50   # totals: model must be >= this far from market
LOCK_MAX_TOTAL_GAP = 2.50   # ...and no further than this

# --------------------------------------------------------------- simulator ---
N_SIMS            = 20000   # Monte Carlo trials per game
N_SIMS_RATINGS    = 6000    # trials per team for neutral-site power ratings
RANDOM_SEED       = 20260101

# Shrinkage priors (regress a player's rates toward league average).
PRIOR_PA_BATTER   = 200     # plate appearances of league-average prior
PRIOR_TBF_SP      = 250     # batters faced prior for starters
PRIOR_TBF_RP      = 130     # batters faced prior for relievers
MIN_PA_LINEUP     = 40      # ignore bench scrubs when projecting a lineup

# Starter workload: batters faced before the bullpen takes over.
SP_BF_SD          = 4.5     # game-to-game noise in starter length
SP_BF_MIN         = 9
SP_BF_MAX         = 30
SP_BF_DEFAULT     = 22.5    # if we have no history for this starter

# Third-time-through-the-order penalty applied to a starter's rates.
TTO_PENALTY       = 0.045   # ~4.5% worse on the third pass

# Platoon (batter hand vs pitcher hand). Modest, generic, applied to rates.
PLATOON_ADV       = 0.035   # opposite hand: batter this much better
PLATOON_DIS       = 0.030   # same hand: batter this much worse

# ----------------------------------------------------------------- weather ---
WEATHER_WEIGHT    = 0.60    # fraction of the computed weather effect we apply
WEATHER_CAP       = 0.12    # +/- 12% run-environment swing, hard cap
TEMP_PER_F        = 0.0060  # run env per degree F away from 70
WIND_OUT_PER_MPH  = 0.0120  # run env per mph blowing out to center
WIND_IN_PER_MPH   = 0.0100  # run env per mph blowing in from center
HUMID_PER_10PCT   = 0.0020
ROOF_CLOSE_TEMP_F = 60.0    # retractable roofs assumed shut below this
ROOF_CLOSE_HOT_F  = 95.0    # ...and above this
ROOF_CLOSE_PRECIP = 0.50    # ...and when rain is likely

# -------------------------------------------------------------------- misc ---
SEASON            = _positive_number("MLB_SEASON", 2026, integer=True)
TZ_DISPLAY        = "America/New_York"
DATA_DIR          = os.environ.get("MLB_DATA_DIR", "data")
DOCS_DATA_DIR     = os.environ.get("MLB_DOCS_DATA_DIR", "docs/data")
HTTP_TIMEOUT      = 25
HTTP_RETRIES      = 3
CACHE_TTL_STATS_H = 8.0     # season stats change slowly; cache them

# ------------------------------------------------------------- home field ---
# Applied as a symmetric offensive nudge (home +x, away -x on hit/HR rates).
# Calibrated so two identical teams produce a ~53.2% home win rate, which is
# where MLB has actually sat in recent seasons, while leaving the total alone.
HOME_FIELD_ADV    = 0.018

# ------------------------------------------------------------- portfolio ---
# The workbook audit showed the damage came from stake size and correlation,
# not from picking losers: a 55% win rate with a -24% ROI is a staking problem.
# These three rules exist to make that arithmetic impossible to repeat.
MAX_BEST_BETS_PER_SLATE = 3     # a real edge is rare; eight a night is a bug
MAX_PLAYS_PER_SLATE     = 6     # total staked positions in one day
MAX_SLATE_EXPOSURE_PCT  = 0.15  # total money at risk across the whole slate
ONE_SIDE_BET_PER_GAME   = True  # never stake both the ML and the run line
# How far the model may typically sit from the market before the whole slate is
# suspect. Measured as the MEDIAN gap between the model's final moneyline
# probability and the no-vig consensus, across every priced game - a median, so
# three genuine disagreements do not trip it but a systematic one does. A model
# anchored 40% to the market should normally sit 1-3 points away; 5+ points on a
# typical game means an input is wrong, not that the market is.
DIVERGENCE_MEDIAN_GAP   = 0.050
DIVERGENCE_MIN_GAMES    = 6     # too few priced games to judge a slate by

# --------------------------------------------------------------- lookahead ---
# The schedule is published for the whole season, probable starters land a few
# days out, and prices a day or two out. The model builds the whole window and
# labels how finished each game's picture is rather than pretending a game with
# no starter named is the same as one an hour from first pitch.
LOOKAHEAD_DAYS    = 7
STAKE_MAX_DAYS_OUT = 1      # only size real stakes on today and tomorrow
ODDS_LOOKAHEAD_DAYS = 3     # beyond this ESPN rarely has prices

# ---------------------------------------------------------- recent form -----
RECENT_WINDOW_DAYS = 30     # rolling window blended on top of season stats
RECENT_WEIGHT_BAT  = 0.30   # how much of a hitter's rate comes from the window
RECENT_WEIGHT_PIT  = 0.25
RECENT_MIN_PA      = 40     # ignore a window this thin
RECENT_MIN_TBF     = 60

# ------------------------------------------------------- platoon splits -----
USE_REAL_SPLITS    = True
SPLIT_PRIOR_PA     = 180    # regress a hitter's split toward his overall line

# ------------------------------------------------- pitcher home run regression
# Home run per fly ball is the noisiest thing a pitcher "owns". Regressing it
# hard toward league average is the whole idea behind xFIP, and it stops a
# lucky-so-far starter being priced as an ace.
HR_REGRESS_PITCHER = 0.55   # 0 = trust the pitcher, 1 = use league average

# --------------------------------------------------------- team defense -----
USE_TEAM_DEFENSE   = True
DEFENSE_STRENGTH   = 0.60   # how much of the measured DER gap to apply

# ---------------------------------------------------- bullpen availability ---
PEN_LOOKBACK_DAYS  = 3
PEN_OUT_PITCHES_1D = 35     # threw this many yesterday -> unavailable today
PEN_OUT_PITCHES_2D = 55     # ...over the last two days
PEN_OUT_APPS_3D    = 3      # pitched three days running -> unavailable
PEN_TIRED_PENALTY  = 0.12   # a merely tired arm is this much worse
SP_SHORT_REST_DAYS = 4      # fewer days than this since his last start
SP_SHORT_REST_PEN  = 0.05   # ...costs him this much

# ------------------------------------------------------------- calibration ---
# Learn from the model's own graded predictions, inside hard bounds, and only
# once there is enough history for the correction to mean anything.
CALIBRATION_ENABLED  = True
CALIBRATION_MIN_GAMES = 150
CALIB_TOTAL_MAX      = 0.50   # cap on the learned runs-per-game correction
CALIB_PROB_MIN       = 0.80   # cap on the learned confidence scaling
CALIB_PROB_MAX       = 1.20

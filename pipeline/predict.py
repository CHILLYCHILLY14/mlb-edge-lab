"""
The prediction ledger.

The shadow book grades *bets*. This grades *games* - every single one on the
schedule, whether there was a price, whether there was an edge, whether anyone
would ever have backed it. That is the only record that says whether the model
understands baseball, as opposed to whether it found soft numbers.

It also answers the question the bet ledger cannot: is the model better than the
market at picking games? Both get scored against the same finals, with a Brier
score, so the comparison is honest rather than anecdotal.

What comes out of it feeds back in. `calibration()` learns two bounded
corrections from the graded history - how far the projected total sits from
reality, and whether the model's confidence is too strong or too weak - and the
build applies them once there is enough history to mean something.
"""
from __future__ import annotations
import json, math, os
from collections import defaultdict
from datetime import datetime, timezone

from . import config as C
from .sources.mlb_api import final_scores

STORE = os.path.join(C.DATA_DIR, "predictions.json")


def _load():
    try:
        with open(STORE) as fh:
            return json.load(fh)
    except Exception:
        return {"games": {}}


def _save(db):
    os.makedirs(C.DATA_DIR, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(db, fh, separators=(",", ":"))
    os.replace(tmp, STORE)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- recording ----
def record(payload: dict) -> int:
    """Snapshot the model's view of every game on a slate."""
    db = _load()
    games = db["games"]
    n = 0
    for g in payload.get("games", []):
        if g.get("gameType") not in ("R", "F", "D", "L", "W", None):
            continue
        key = str(g["gamePk"])
        row = games.get(key, {})
        if row.get("status") == "graded":
            continue
        s = g["sim"]
        o = g.get("odds") or {}
        p_home = s.get("p_home_final", s["p_home"])
        row.update({
            "gamePk": g["gamePk"], "date": g["date"], "start": g["start"],
            "away": g["away"], "home": g["home"],
            "p_home": round(p_home, 4),
            "p_home_sim": round(s["p_home"], 4),
            "p_home_market": (round(o["cons_home"], 4)
                              if o.get("cons_home") is not None else None),
            "proj_away": round(s["mean_away"], 3),
            "proj_home": round(s["mean_home"], 3),
            "proj_total": round(s["mean_total"], 3),
            "market_total": o.get("total"),
            "pick": g["home"] if p_home >= 0.5 else g["away"],
            "pick_conf": round(max(p_home, 1 - p_home), 4),
            "total_lean": (None if o.get("total") is None else
                           ("over" if s["mean_total"] > o["total"] else "under")),
            "readiness": g.get("readiness"),
            "lineups_confirmed": g.get("lineups_confirmed"),
            "days_out": g.get("days_out"),
            "updated_at": _now(),
            "status": row.get("status", "pending"),
        })
        row.setdefault("first_seen", _now())
        games[key] = row
        n += 1
    _save(db)
    return n


# --------------------------------------------------------------- grading ----
def grade(max_dates: int = 21) -> int:
    db = _load()
    games = db["games"]
    pending = [r for r in games.values() if r.get("status") != "graded"]
    if not pending:
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dates = sorted({r["date"] for r in pending if r["date"] <= today})[-max_dates:]
    done = 0
    for ds in dates:
        finals = final_scores(ds)
        if not finals:
            continue
        for r in [x for x in pending if x["date"] == ds]:
            f = finals.get(r["gamePk"])
            if not f or f.get("away") is None or f.get("home") is None:
                continue
            a, h = float(f["away"]), float(f["home"])
            winner = r["home"] if h > a else r["away"]
            total = a + h
            r.update({
                "status": "graded", "graded_at": _now(),
                "final_away": a, "final_home": h, "final_total": total,
                "winner": winner,
                "correct": r["pick"] == winner,
                "home_won": h > a,
                "err_away": round(r["proj_away"] - a, 3),
                "err_home": round(r["proj_home"] - h, 3),
                "err_total": round(r["proj_total"] - total, 3),
                "err_margin": round((r["proj_home"] - r["proj_away"]) - (h - a), 3),
                "total_lean_correct": (
                    None if r.get("total_lean") is None or r.get("market_total") is None
                    or total == r["market_total"]
                    else ((total > r["market_total"]) == (r["total_lean"] == "over"))),
            })
            done += 1
    if done:
        _save(db)
        print(f"  graded {done} game prediction(s)")
    return done


# -------------------------------------------------------------- reporting ---
def _brier(rows, key):
    vals = [(r[key], 1.0 if r["home_won"] else 0.0)
            for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return round(sum((p - y) ** 2 for p, y in vals) / len(vals), 4)


def _logloss(rows, key):
    vals = [(min(max(r[key], 1e-6), 1 - 1e-6), 1.0 if r["home_won"] else 0.0)
            for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return round(-sum(y * math.log(p) + (1 - y) * math.log(1 - p)
                      for p, y in vals) / len(vals), 4)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def summary() -> dict:
    db = _load()
    rows = [r for r in db["games"].values() if r.get("status") == "graded"]
    rows.sort(key=lambda r: (r.get("date", ""), r.get("gamePk", 0)))
    pending = [r for r in db["games"].values() if r.get("status") != "graded"]

    def block(sub):
        if not sub:
            return {"n": 0}
        correct = sum(1 for r in sub if r.get("correct"))
        lean = [r for r in sub if r.get("total_lean_correct") is not None]
        return {
            "n": len(sub),
            "correct": correct,
            "accuracy": round(correct / len(sub), 4),
            "brier": _brier(sub, "p_home"),
            "brier_market": _brier(sub, "p_home_market"),
            "logloss": _brier(sub, "p_home") and _logloss(sub, "p_home"),
            "mae_total": _mean([abs(r["err_total"]) for r in sub]),
            "bias_total": _mean([r["err_total"] for r in sub]),
            "mae_margin": _mean([abs(r["err_margin"]) for r in sub]),
            "bias_margin": _mean([r["err_margin"] for r in sub]),
            "mae_runs": _mean([abs(r["err_away"]) for r in sub]
                              + [abs(r["err_home"]) for r in sub]),
            "total_lean_n": len(lean),
            "total_lean_acc": (round(sum(1 for r in lean if r["total_lean_correct"])
                                     / len(lean), 4) if lean else None),
        }

    # reliability: of the games called at 60%, how many actually happened
    buckets = defaultdict(lambda: {"n": 0, "hit": 0, "sum_p": 0.0})
    for r in rows:
        p = r["pick_conf"]
        b = buckets[min(int(p * 20), 19)]     # 5-point buckets
        b["n"] += 1
        b["hit"] += 1 if r.get("correct") else 0
        b["sum_p"] += p
    reliability = [{"bucket": f"{k*5}-{k*5+5}%", "n": v["n"],
                    "predicted": round(v["sum_p"] / v["n"], 4),
                    "actual": round(v["hit"] / v["n"], 4)}
                   for k, v in sorted(buckets.items()) if v["n"] >= 8]

    # which clubs the model reads wrong
    team = defaultdict(lambda: {"n": 0, "err": 0.0, "abs": 0.0})
    for r in rows:
        for side, err in ((r["away"], r["err_away"]), (r["home"], r["err_home"])):
            t = team[side]
            t["n"] += 1
            t["err"] += err
            t["abs"] += abs(err)
    teams = sorted(({"team": k, "n": v["n"],
                     "bias": round(v["err"] / v["n"], 3),
                     "mae": round(v["abs"] / v["n"], 3)}
                    for k, v in team.items() if v["n"] >= 8),
                   key=lambda x: x["bias"])

    head_to_head = [r for r in rows if r.get("p_home_market") is not None]
    market_correct = sum(1 for r in head_to_head
                         if ((r["p_home_market"] >= 0.5) == r["home_won"]))

    return {
        "generated_at": _now(),
        "overall": block(rows),
        "last30": block(rows[-30:]),
        "last100": block(rows[-100:]),
        "confirmed_lineups": block([r for r in rows if r.get("lineups_confirmed")]),
        "projected_lineups": block([r for r in rows if not r.get("lineups_confirmed")]),
        "reliability": reliability,
        "team_bias": teams,
        "vs_market": {
            "n": len(head_to_head),
            "model_correct": sum(1 for r in head_to_head if r.get("correct")),
            "market_correct": market_correct,
            "model_brier": _brier(head_to_head, "p_home"),
            "market_brier": _brier(head_to_head, "p_home_market"),
        },
        "pending": len(pending),
        "recent": [
            {k: r.get(k) for k in ("date", "away", "home", "pick", "pick_conf",
                                   "proj_away", "proj_home", "final_away",
                                   "final_home", "correct", "err_total",
                                   "market_total", "total_lean",
                                   "total_lean_correct", "lineups_confirmed")}
            for r in rows[-200:]
        ],
    }


# ------------------------------------------------------------ calibration ---
def calibration() -> dict:
    """
    Two bounded corrections learned from the model's own graded games.

    `total_adj` is how many runs the projections have been off by on average -
    if the model has been reading a run high all season, stop reading a run
    high. `prob_scale` stretches or shrinks confidence in log-odds space: above
    one means the model has been too timid, below one means too sure of itself.

    Both are capped, both need a real sample before they do anything, and both
    are published so the number on the page can always be traced.
    """
    db = _load()
    rows = [r for r in db["games"].values() if r.get("status") == "graded"]
    out = {"generated_at": _now(), "n": len(rows), "applied": False,
           "total_adj": 0.0, "prob_scale": 1.0,
           "min_games": C.CALIBRATION_MIN_GAMES, "enabled": C.CALIBRATION_ENABLED}
    if not C.CALIBRATION_ENABLED or len(rows) < C.CALIBRATION_MIN_GAMES:
        out["reason"] = (f"{len(rows)} graded of {C.CALIBRATION_MIN_GAMES} needed"
                         if C.CALIBRATION_ENABLED else "disabled in config")
        return out

    bias = sum(r["err_total"] for r in rows) / len(rows)      # + means we ran high
    out["total_adj"] = round(max(-C.CALIB_TOTAL_MAX, min(C.CALIB_TOTAL_MAX, -bias)), 3)

    # one Newton step on the log-odds slope: y ~ sigmoid(scale * logit(p))
    num = den = 0.0
    for r in rows:
        p = min(max(r["p_home"], 1e-4), 1 - 1e-4)
        x = math.log(p / (1 - p))
        y = 1.0 if r["home_won"] else 0.0
        q = 1.0 / (1.0 + math.exp(-x))
        num += x * (y - q)
        den += x * x * q * (1 - q)
    scale = 1.0 + (num / den if den > 1e-9 else 0.0)
    out["prob_scale"] = round(max(C.CALIB_PROB_MIN, min(C.CALIB_PROB_MAX, scale)), 4)
    out["applied"] = True
    out["measured_total_bias"] = round(bias, 3)
    return out


def apply_prob_scale(p: float, scale: float) -> float:
    """Stretch or shrink a probability in log-odds space."""
    if abs(scale - 1.0) < 1e-6:
        return p
    p = min(max(p, 1e-6), 1 - 1e-6)
    x = math.log(p / (1 - p)) * scale
    return 1.0 / (1.0 + math.exp(-x))

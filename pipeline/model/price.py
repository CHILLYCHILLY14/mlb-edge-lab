"""Turn simulated probabilities plus market prices into graded, staked bets."""
from __future__ import annotations

from .. import config as C
from .market import (american_to_decimal, blend, cap_prob, compress, fmt_american, price_ok,
                     kelly_stake, lock_rules, no_vig, prob_to_american, raw_edge,
                     tier_for)


def _bet(market, selection, label, price, p_model, p_market, blend_w, ceiling,
         ctx, is_total=False, total_gap=None, book=None, line=None,
         best_price=None, best_book=None):
    # Not just None: a feed publishing 0 for a market it has not hung yet, or a
    # typo pasted into manual_odds.json, must not become a bet. Anything that is
    # not a real American price is treated as no price at all - the model still
    # publishes its own fair line for the market, there is just nothing to beat.
    if not price_ok(price):
        return None
    p_final = cap_prob(blend(p_model, p_market, blend_w))

    # Two different edges, and conflating them was a real bug.
    #
    # MODEL EDGE is what the model actually disagrees with the market about:
    # expected value priced at the no-vig consensus. On any two-way market it is
    # positive on at most one side, by construction, because the two model
    # probabilities sum to one and so do the two market probabilities.
    #
    # REALIZED EDGE is what the bet is worth at the number you can actually
    # take. It includes the gap between the consensus and the best price on
    # offer - which is genuinely worth something, but it is line shopping, not
    # handicapping, and it is positive on BOTH sides of a game whenever books
    # differ. Tiering on it made every game look like a play and set the
    # divergence flag off on half the slate.
    #
    # So: qualify on the model edge, size on the realized one.
    e_real = raw_edge(p_final, price)
    e_real_c = compress(e_real, ceiling)
    if p_market is not None and p_market > 0:
        e_model = p_final / p_market - 1.0
    else:
        e_model = e_real
    e_c = compress(e_model, ceiling)
    e_price = round(e_real_c - e_c, 4)

    stake, kf = kelly_stake(min(e_c, e_real_c), price, C.BANKROLL)
    tier = tier_for(e_c)
    fails = []
    if tier == "BEST BET":
        fails = lock_rules(price=price, p_model=p_final, p_market=p_market,
                           odds_age_h=ctx.get("odds_age_h"), sim_se=ctx.get("se"),
                           both_sp=ctx.get("both_sp", False), precip=ctx.get("precip"),
                           is_total=is_total, total_gap=total_gap)
        if fails:
            tier = "GOOD"
    if tier == "PASS":
        stake, kf = 0.0, 0.0
    return {
        "market": market, "selection": selection, "label": label,
        "price": price, "price_txt": fmt_american(price), "book": book,
        # What the same selection costs at the best book on the board. Shown
        # only when it beats your book, so you can see when shopping pays.
        "best_price": best_price if price_ok(best_price) else None,
        "best_price_txt": fmt_american(best_price) if price_ok(best_price) else None,
        "best_book": best_book,
        "shop_gain": (round(american_to_decimal(best_price) - american_to_decimal(price), 3)
                      if price_ok(best_price) and best_price != price else None),
        "line": line,
        "p_model": round(p_model, 4), "p_market": (round(p_market, 4) if p_market is not None else None),
        "p_final": round(p_final, 4),
        "fair_price": fmt_american(prob_to_american(p_final)),
        "edge_raw": round(e_model, 4), "edge": round(e_c, 4),
        "edge_pct": round(e_c * 100, 2),
        "edge_real": round(e_real_c, 4), "edge_real_pct": round(e_real_c * 100, 2),
        "edge_price": e_price, "edge_price_pct": round(e_price * 100, 2),
        "tier": tier, "stake": round(stake, 2), "kelly": round(kf, 4),
        "to_win": round(stake * (american_to_decimal(price) - 1.0), 2),
        "lock_fails": fails,
        "decimal": round(american_to_decimal(price), 3),
    }


def price_game(g: dict, d: dict, odds: dict | None, manual: dict | None = None) -> list[dict]:
    """All priced markets for one game, most edge first."""
    odds = odds or {}
    manual = manual or {}
    ctx = {"odds_age_h": g.get("odds_age_h"), "se": d.get("se"),
           "both_sp": bool(g.get("away_sp") and g.get("home_sp")),
           "precip": (g.get("weather") or {}).get("precip")}
    away, home = g["away"], g["home"]
    bets = []

    # The price we would bet and the price we measure against are different
    # numbers. Best-available is what you get filled at; the consensus of every
    # book ESPN returns is the market's actual opinion. Grading an edge against
    # the best price would invent an edge on every game just by shopping.
    def market(cons_key, a, b):
        c = odds.get(cons_key)
        if c is not None:
            return c
        return no_vig(a, b)[0]

    # ---------------------------------------------------------- moneyline ---
    ml_a, ml_h = odds.get("ml_away"), odds.get("ml_home")
    mk_a = market("cons_away", ml_a, ml_h)
    mk_h = market("cons_home", ml_h, ml_a)
    bets.append(_bet("ML", away, f"{away} ML", ml_a, d["p_away"], mk_a,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx,
                     book=odds.get("ml_away_book"),
                     best_price=odds.get("ml_away_best"),
                     best_book=odds.get("ml_away_best_book")))
    bets.append(_bet("ML", home, f"{home} ML", ml_h, d["p_home"], mk_h,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx,
                     book=odds.get("ml_home_book"),
                     best_price=odds.get("ml_home_best"),
                     best_book=odds.get("ml_home_best_book")))

    # ----------------------------------------------------------- run line ---
    rl = odds.get("rl_line", -1.5)
    rl_h, rl_a = odds.get("rl_home"), odds.get("rl_away")
    mk_rh = market("cons_rl_home", rl_h, rl_a)
    mk_ra = market("cons_rl_away", rl_a, rl_h)
    bets.append(_bet("RL", home, f"{home} {rl:+.1f}", rl_h, d["p_home_rl"], mk_rh,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx, line=rl,
                     book=odds.get("rl_home_book"),
                     best_price=odds.get("rl_home_best"),
                     best_book=odds.get("rl_home_best_book")))
    bets.append(_bet("RL", away, f"{away} {-rl:+.1f}", rl_a, d["p_away_rl"], mk_ra,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx, line=rl,
                     book=odds.get("rl_away_book"),
                     best_price=odds.get("rl_away_best"),
                     best_book=odds.get("rl_away_best_book")))

    # -------------------------------------------------------------- total ---
    tot = odds.get("total")
    if tot is not None and "p_total_over" in d:
        gap = abs(d["mean_total"] - tot)
        mk_o = market("cons_over", odds.get("over"), odds.get("under"))
        mk_u = market("cons_under", odds.get("under"), odds.get("over"))
        bets.append(_bet("TOTAL", "Over", f"Over {tot}", odds.get("over"),
                         d["p_total_over"], mk_o, C.TOTALS_BLEND, C.EDGE_CEILING_TOT,
                         ctx, is_total=True, total_gap=gap, line=tot,
                         book=odds.get("over_book"),
                         best_price=odds.get("over_best"),
                         best_book=odds.get("over_best_book")))
        bets.append(_bet("TOTAL", "Under", f"Under {tot}", odds.get("under"),
                         d["p_total_under"], mk_u, C.TOTALS_BLEND, C.EDGE_CEILING_TOT,
                         ctx, is_total=True, total_gap=gap, line=tot,
                         book=odds.get("under_book"),
                         best_price=odds.get("under_best"),
                         best_book=odds.get("under_best_book")))

    # ---------------------------------------------------------- first five ---
    # ESPN does not publish F5 prices. If you paste them into data/manual_odds.json
    # the model prices them exactly like any other market; without them it still
    # publishes a fair line you can shop against, and the shadow book still grades
    # the F5 call so its accuracy is measured either way.
    f5 = manual.get("f5") or {}
    f5_a, f5_h = f5.get("ml_away"), f5.get("ml_home")
    if f5_a and f5_h:
        mk = no_vig(f5_a, f5_h)
        bets.append(_bet("F5 ML", away, f"{away} F5 ML", f5_a, _f5_side(d, "away"), mk[0],
                         C.F5_BLEND, C.EDGE_CEILING, ctx, book=f5.get("book", "manual")))
        bets.append(_bet("F5 ML", home, f"{home} F5 ML", f5_h, _f5_side(d, "home"), mk[1],
                         C.F5_BLEND, C.EDGE_CEILING, ctx, book=f5.get("book", "manual")))
    f5t = f5.get("total")
    if f5t is not None and "p_f5_over" in d:
        # No -110 default. If you pasted a first-five total without prices, there
        # is a line but no market to beat, and the model says so rather than
        # pretending both sides are -110.
        mk_o, mk_u = no_vig(f5.get("over"), f5.get("under"))
        gap = abs(d["mean_f5_total"] - f5t)
        bets.append(_bet("F5 TOTAL", "Over", f"F5 Over {f5t}", f5.get("over"),
                         d["p_f5_over"], mk_o, C.F5_BLEND, C.EDGE_CEILING_TOT, ctx,
                         is_total=True, total_gap=gap, book=f5.get("book", "manual"), line=f5t))
        bets.append(_bet("F5 TOTAL", "Under", f"F5 Under {f5t}", f5.get("under"),
                         d["p_f5_under"], mk_u, C.F5_BLEND, C.EDGE_CEILING_TOT, ctx,
                         is_total=True, total_gap=gap, book=f5.get("book", "manual"), line=f5t))

    # ---- first inning and team totals -------------------------------------
    # ESPN does not publish these. The model still simulates them and publishes
    # a fair line for both, and prices them properly if you paste a number into
    # data/manual_odds.json.
    nr = manual.get("nrfi") or {}
    if nr.get("yes") and nr.get("no"):
        mk = no_vig(nr["yes"], nr["no"])
        bets.append(_bet("NRFI", "Yes", "No run in the 1st", nr["yes"],
                         d["p_nrfi"], mk[0], C.F5_BLEND, C.EDGE_CEILING_TOT, ctx,
                         book=nr.get("book", "manual")))
        bets.append(_bet("NRFI", "No", "Run in the 1st", nr["no"],
                         d["p_yrfi"], mk[1], C.F5_BLEND, C.EDGE_CEILING_TOT, ctx,
                         book=nr.get("book", "manual")))
    for side, key in ((away, "team_total_away"), (home, "team_total_home")):
        tt = (manual.get("team_totals") or {}).get(side) or {}
        line = tt.get("line")
        if line is None or not (tt.get("over") and tt.get("under")):
            continue
        from .simulate import team_total_ou
        import numpy as _np
        probs = d.get(f"{key}_at_line") or {}
        po, pu = probs.get("over"), probs.get("under")
        if po is None:
            continue
        mk = no_vig(tt["over"], tt["under"])
        gap = abs(d[key]["mean"] - line)
        bets.append(_bet("TEAM TOTAL", f"{side} Over", f"{side} Over {line}",
                         tt["over"], po, mk[0], C.TOTALS_BLEND, C.EDGE_CEILING_TOT,
                         ctx, is_total=True, total_gap=gap,
                         book=tt.get("book", "manual"), line=line))
        bets.append(_bet("TEAM TOTAL", f"{side} Under", f"{side} Under {line}",
                         tt["under"], pu, mk[1], C.TOTALS_BLEND, C.EDGE_CEILING_TOT,
                         ctx, is_total=True, total_gap=gap,
                         book=tt.get("book", "manual"), line=line))

    bets = [b for b in bets if b]
    bets.sort(key=lambda b: -b["edge"])
    return bets


def derived_lines(d: dict, away: str, home: str) -> dict:
    """
    Fair numbers for the markets no free feed carries. Published so they can be
    shopped by hand even with no price to compare against.
    """
    return {
        "nrfi": {"yes": fmt_american(prob_to_american(d["p_nrfi"])),
                 "no": fmt_american(prob_to_american(d["p_yrfi"])),
                 "yes_pct": round(d["p_nrfi"] * 100, 1),
                 "mean_runs": round(d["mean_i1"], 2)},
        "team_totals": {
            away: {"line": d["team_total_away"]["line"],
                   "over": fmt_american(prob_to_american(d["team_total_away"]["over"])),
                   "under": fmt_american(prob_to_american(d["team_total_away"]["under"])),
                   "mean": round(d["team_total_away"]["mean"], 2)},
            home: {"line": d["team_total_home"]["line"],
                   "over": fmt_american(prob_to_american(d["team_total_home"]["over"])),
                   "under": fmt_american(prob_to_american(d["team_total_home"]["under"])),
                   "mean": round(d["team_total_home"]["mean"], 2)},
        },
        "shutout": {away: round(d["p_away_shutout"] * 100, 1),
                    home: round(d["p_home_shutout"] * 100, 1)},
    }


def _f5_side(d: dict, side: str) -> float:
    """F5 moneylines are three-way (ties refund on some books, lose on others).
    We price the two-way 'wins the first five' market, ties excluded."""
    a, h, t = d["p_f5_away"], d["p_f5_home"], d["p_f5_tie"]
    denom = max(a + h, 1e-9)
    return (a / denom) if side == "away" else (h / denom)


def f5_fair(d: dict) -> dict:
    """Fair first-five prices so you can shop them even with no market feed."""
    a = _f5_side(d, "away")
    return {"away": fmt_american(prob_to_american(a)),
            "home": fmt_american(prob_to_american(1 - a)),
            "total": round(d["mean_f5_total"] * 2) / 2,
            "away_pct": round(a * 100, 1), "home_pct": round((1 - a) * 100, 1),
            "tie_pct": round(d["p_f5_tie"] * 100, 1)}

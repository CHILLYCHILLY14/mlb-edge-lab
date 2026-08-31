"""
ESPN public scoreboard - keyless moneyline / run line / total prices.

ESPN has shipped several odds payload shapes over the years; this reads all of
them and returns a single normalised record per game.
"""
from __future__ import annotations
from datetime import datetime, timezone

from .. import config as C
from .http import get_json

SB = ("https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
      "?dates={d}&limit=100")

# ESPN abbreviation -> our canonical abbreviation
ESPN_MAP = {"ARI": "AZ", "CHW": "CWS", "WSH": "WSH", "SFG": "SF", "SDG": "SD",
            "TBR": "TB", "KCR": "KC", "OAK": "ATH", "ATH": "ATH", "LAA": "LAA"}

# books we prefer, best first
BOOK_RANK = ["ESPN BET", "DraftKings", "FanDuel", "Caesars", "BetMGM",
             "William Hill (New Jersey)", "Bet365", "consensus"]


def _num(v):
    try:
        if v is None or v == "" or str(v).upper() in ("EVEN", "EV"):
            return 100.0 if str(v).upper() in ("EVEN", "EV") else None
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _abbr(a: str) -> str:
    a = (a or "").upper()
    return ESPN_MAP.get(a, a)


def _pick_odds(odds_list):
    """Choose the best-ranked odds block that actually carries a moneyline."""
    if not odds_list:
        return None
    def score(o):
        name = ((o.get("provider") or {}).get("name") or "")
        try:
            r = BOOK_RANK.index(name)
        except ValueError:
            r = len(BOOK_RANK)
        has_ml = _ml_from(o)[0] is not None
        return (0 if has_ml else 1, r)
    return sorted(odds_list, key=score)[0]


def _ml_from(o):
    """(away_ml, home_ml) out of whichever shape ESPN used."""
    a = _price((o.get("awayTeamOdds") or {}).get("moneyLine"))
    h = _price((o.get("homeTeamOdds") or {}).get("moneyLine"))

    # The site scoreboard moved its current prices into a top-level
    # ``moneyline`` block in August 2026.  The old summary fields still carry
    # the favourite flags but no longer carry the actual number, so only
    # reading awayTeamOdds/homeTeamOdds turns a fully priced game into an empty
    # market.
    nested = o.get("moneyline") or {}
    if isinstance(nested, dict):
        for side, key in (("away", "a"), ("home", "h")):
            blk = nested.get(side) or {}
            close = blk.get("close") or blk.get("current") or blk
            v = _price((close.get("odds") or close.get("american")
                        or close.get("alternateDisplayValue"))
                       if isinstance(close, dict) else close)
            if key == "a" and a is None:
                a = v
            if key == "h" and h is None:
                h = v

    # The core endpoint carries the same values one level below each team's
    # current block.  Keep this fallback because either ESPN endpoint can be
    # populated first on a newly posted game.
    for side, key in (("awayTeamOdds", "a"), ("homeTeamOdds", "h")):
        blk = o.get(side) or {}
        cur_ml = (blk.get("current") or {}).get("moneyLine") or {}
        v = _price((cur_ml.get("american") or cur_ml.get("alternateDisplayValue"))
                   if isinstance(cur_ml, dict) else cur_ml)
        if key == "a" and a is None:
            a = v
        if key == "h" and h is None:
            h = v

    if a is None or h is None:
        cur = o.get("current") or {}
        a = a if a is not None else _price(((cur.get("away") or {}).get("moneyLine") or {}).get("american")
                                         if isinstance(cur.get("away"), dict) else None)
        h = h if h is not None else _price(((cur.get("home") or {}).get("moneyLine") or {}).get("american")
                                         if isinstance(cur.get("home"), dict) else None)
    if a is None or h is None:
        for side, key in (("awayTeamOdds", "a"), ("homeTeamOdds", "h")):
            blk = o.get(side) or {}
            v = _price((blk.get("moneyLine") or {}).get("american") if isinstance(blk.get("moneyLine"), dict) else None)
            if key == "a" and a is None:
                a = v
            if key == "h" and h is None:
                h = v
    return a, h


def _runline_from(o, home_abbr, away_abbr, ml_away=None, ml_home=None):
    """
    (run_line_as_it_applies_to_HOME, home_price, away_price).

    ESPN publishes the spread from the favourite's point of view and names the
    favourite in `details` ("MIL -1.5"). Taking the raw number as the home line
    silently inverts the run line on every game the road team is favoured in,
    which is roughly half the slate, so the favourite is resolved explicitly.
    """
    det = (o.get("details") or "").strip()
    raw = _num(o.get("spread"))
    point_spread = o.get("pointSpread") or {}
    ps_home = (point_spread.get("home") or {}) if isinstance(point_spread, dict) else {}
    ps_away = (point_spread.get("away") or {}) if isinstance(point_spread, dict) else {}
    ps_home_close = ps_home.get("close") or ps_home.get("current") or ps_home
    ps_away_close = ps_away.get("close") or ps_away.get("current") or ps_away
    nested_home_line = _num((ps_home_close.get("line")
                             or ps_home_close.get("pointSpread"))
                            if isinstance(ps_home_close, dict) else None)
    fav = None
    parts = det.split()
    if len(parts) >= 2:
        fav = _abbr(parts[0])
        if raw is None:
            raw = _num(parts[-1])
    if nested_home_line is not None:
        # Unlike the legacy top-level spread, this number explicitly belongs to
        # the home team, so there is no favourite-sign inference to perform.
        line = nested_home_line
    elif raw is None:
        line = -1.5
    else:
        mag = abs(raw)
        if fav == away_abbr and fav != home_abbr:
            line = +mag                     # home is the underdog: +1.5
        elif fav == home_abbr:
            line = -mag
        elif ml_away is not None and ml_home is not None:
            # No favourite named in `details`, so do not trust the sign - ESPN
            # writes the spread from the favourite's side, and taking it as the
            # home line silently inverts the run line on every game the road
            # team is favoured in. The moneyline says who the favourite is.
            line = -mag if ml_home < ml_away else +mag
        else:                               # nothing to go on; trust the sign
            line = raw
    hp = _price((ps_home_close.get("odds") or ps_home_close.get("american"))
                if isinstance(ps_home_close, dict) else None)
    ap = _price((ps_away_close.get("odds") or ps_away_close.get("american"))
                if isinstance(ps_away_close, dict) else None)
    hp = hp if hp is not None else _price((o.get("homeTeamOdds") or {}).get("spreadOdds"))
    ap = ap if ap is not None else _price((o.get("awayTeamOdds") or {}).get("spreadOdds"))

    # Core odds put the run-line price in team.current.spread and the line in
    # team.current.pointSpread.
    for side, key in (("homeTeamOdds", "h"), ("awayTeamOdds", "a")):
        blk = o.get(side) or {}
        side_cur = blk.get("current") or {}
        sp = side_cur.get("spread") or {}
        v = _price((sp.get("american") or sp.get("alternateDisplayValue"))
                   if isinstance(sp, dict) else sp)
        if key == "h" and hp is None:
            hp = v
        if key == "a" and ap is None:
            ap = v
    core_home_line = (((o.get("homeTeamOdds") or {}).get("current") or {})
                      .get("pointSpread") or {})
    core_home_line = _num((core_home_line.get("american")
                           or core_home_line.get("alternateDisplayValue"))
                          if isinstance(core_home_line, dict) else core_home_line)
    if nested_home_line is None and core_home_line is not None:
        line = core_home_line

    cur = o.get("current") or {}
    if hp is None and isinstance(cur.get("home"), dict):
        hp = _price(((cur["home"].get("close") or cur["home"]).get("odds")))
    if ap is None and isinstance(cur.get("away"), dict):
        ap = _price(((cur["away"].get("close") or cur["away"]).get("odds")))
    return line, hp, ap


def _total_from(o):
    tot = _num(o.get("overUnder"))
    ov = _price((o.get("overOdds")))
    un = _price((o.get("underOdds")))

    # Current scoreboard shape: total.over.close.odds / total.under.close.odds.
    nested = o.get("total") or {}
    if isinstance(nested, dict):
        over = nested.get("over") or {}
        under = nested.get("under") or {}
        over_close = over.get("close") or over.get("current") or over
        under_close = under.get("close") or under.get("current") or under
        if ov is None:
            ov = _price((over_close.get("odds") or over_close.get("american"))
                        if isinstance(over_close, dict) else over_close)
        if un is None:
            un = _price((under_close.get("odds") or under_close.get("american"))
                        if isinstance(under_close, dict) else under_close)
    cur = o.get("current") or {}
    if tot is None and isinstance(cur.get("total"), dict):
        tot = _num((cur["total"].get("alternateDisplayValue") or cur["total"].get("value")))
    if ov is None and isinstance(cur.get("over"), dict):
        ov = _price(cur["over"].get("american") or (cur["over"].get("close") or {}).get("odds"))
    if un is None and isinstance(cur.get("under"), dict):
        un = _price(cur["under"].get("american") or (cur["under"].get("close") or {}).get("odds"))
    return tot, ov, un


def _book_pair_ok(a, h) -> bool:
    """One book quoting both sides must imply more than 100%, or it is not a
    real quote - it is half a market, a placeholder, or a parsing slip."""
    if a is None or h is None:
        return True                     # one-sided is fine; nothing to check
    if a == h or abs(a) < 100 or abs(h) < 100:
        return False
    ia = 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)
    ih = 100.0 / (h + 100.0) if h > 0 else abs(h) / (abs(h) + 100.0)
    return 0.98 <= ia + ih <= 1.25


def _books_from(comp, home_abbr, away_abbr) -> list[dict]:
    """Every provider ESPN returned for this game, normalised."""
    books = []
    for o in (comp.get("odds") or []):
        ml_a, ml_h = _ml_from(o)
        rl, rl_h, rl_a = _runline_from(o, home_abbr, away_abbr, ml_a, ml_h)
        tot, ov, un = _total_from(o)
        name = ((o.get("provider") or {}).get("name") or "book")
        if ml_a is None and tot is None:
            continue
        if not _book_pair_ok(ml_a, ml_h):
            ml_a = ml_h = None          # keep the total, drop the bad moneyline
            if tot is None:
                continue
        books.append({"book": name, "ml_away": ml_a, "ml_home": ml_h,
                      "rl_line": rl, "rl_home": rl_h, "rl_away": rl_a,
                      "total": tot, "over": ov, "under": un})
    return books


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def valid_price(a) -> bool:
    """Is this a real American price?

    A moneyline of 0 is not a price - it is a placeholder some feeds publish
    for a market that has not been hung yet, and it used to reach the decimal
    conversion and divide by zero. Nothing under 100 either way is a real
    American number, and nothing past 5000 belongs on a baseball board.
    """
    if a is None:
        return False
    try:
        a = float(a)
    except (TypeError, ValueError):
        return False
    if a != a or a in (float("inf"), float("-inf")):   # NaN / infinity
        return False
    return 100.0 <= abs(a) <= 5000.0


def _price(v):
    """_num, but it only lets a usable American price through."""
    a = _num(v)
    return a if valid_price(a) else None


def _best_price(prices):
    """The most favourable American price on offer for one selection."""
    prices = [p for p in prices if valid_price(p)]
    if not prices:
        return None, None
    return max(prices, key=american_decimal), None


def american_decimal(a: float) -> float:
    """Decimal payout for an American price. Never raises on junk input.

    Returns 1.0 - a bet that pays back exactly the stake - for anything that
    is not a real price, so a bad number can never win a max() comparison.
    """
    if not valid_price(a):
        return 1.0
    a = float(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def _devig_pair(pa, pb):
    """Power-method de-vig, kept local so this module has no model imports."""
    # Not just None. A pair of zeros used to fall through to the (0.5, 0.5)
    # bail-out at the bottom, which is a fabricated 50/50 market opinion born
    # from a book that had not posted the game at all.
    if not valid_price(pa) or not valid_price(pb):
        return None, None
    ia = 100.0 / (pa + 100.0) if pa > 0 else abs(pa) / (abs(pa) + 100.0)
    ib = 100.0 / (pb + 100.0) if pb > 0 else abs(pb) / (abs(pb) + 100.0)
    if ia <= 0 or ib <= 0:
        s = ia + ib
        return (ia / s, ib / s) if s > 0 else (0.5, 0.5)
    lo, hi = 0.5, 2.0
    for _ in range(60):
        k = (lo + hi) / 2
        if ia ** k + ib ** k > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    a, b = ia ** k, ib ** k
    s = a + b
    return a / s, b / s


def canon_book(name) -> str:
    """ESPN's spelling -> one canonical name, so 'draftkings' and 'DraftKings'
    are the same book when we go looking for yours."""
    n = (name or "").strip()
    return C.BOOK_ALIASES.get(n.lower(), n)


def _pick_side(rows, key):
    """
    Which price to show for one selection, and whose it is.

    Returns (price, book, best_price, best_book).

    Your book's number is the headline whenever it has posted one, because that
    is the number you will see when you go to bet. The best price on the board
    is carried alongside it so the card can tell you when shopping is worth it.
    Nothing here ever invents a number: no book, no price.
    """
    cands = [(float(b[key]), canon_book(b.get("book")))
             for b in rows if valid_price(b.get(key))]
    if not cands:
        return None, None, None, None
    best_p, best_b = max(cands, key=lambda t: american_decimal(t[0]))
    pref = getattr(C, "PREFERRED_BOOK", None)
    if pref:
        want = canon_book(pref).lower()
        for p, bk in cands:
            if bk.lower() == want:
                return p, bk, best_p, best_b
    return best_p, best_b, best_p, best_b


def _side(out, name, rows, key):
    """Write one selection's price, its book, and the best price on the board."""
    p, bk, bp, bb = _pick_side(rows, key)
    out[name] = p
    out[f"{name}_book"] = bk
    out[f"{name}_best"] = bp
    out[f"{name}_best_book"] = bb


def _consensus(books: list[dict], home_abbr, away_abbr) -> dict:
    """
    Collapse several books into one market view.

    Two different numbers come out of this and they do different jobs. The
    CONSENSUS - the median no-vig probability across books - is the market's
    real opinion, and that is what the edge is measured against. The BEST PRICE
    is the number you would actually bet, which is usually not from the same
    book. Measuring edge against the best price instead would manufacture an
    edge on every game simply by shopping.
    """
    if not books:
        return {}
    for b in books:
        b["book"] = canon_book(b.get("book"))
    out = {"books": [b["book"] for b in books], "n_books": len(books)}

    # The whole board, exactly as the feed gave it. Published so every number on
    # a card can be traced to a named book instead of asking you to trust it.
    out["board"] = [{"book": b["book"],
                     "ml_away": b["ml_away"], "ml_home": b["ml_home"],
                     "rl_line": b["rl_line"],
                     "rl_home": b["rl_home"], "rl_away": b["rl_away"],
                     "total": b["total"], "over": b["over"], "under": b["under"]}
                    for b in books]

    # ---- moneyline
    pa = [_devig_pair(b["ml_away"], b["ml_home"])[0] for b in books]
    ph = [_devig_pair(b["ml_away"], b["ml_home"])[1] for b in books]
    out["cons_away"], out["cons_home"] = _median(pa), _median(ph)
    _side(out, "ml_away", books, "ml_away")
    _side(out, "ml_home", books, "ml_home")

    # ---- run line: use the modal line, price only books posting it
    lines = [b["rl_line"] for b in books if b["rl_line"] is not None]
    rl = max(set(lines), key=lines.count) if lines else -1.5
    at = [b for b in books if b["rl_line"] == rl] or books
    out["rl_line"] = rl
    _side(out, "rl_home", at, "rl_home")
    _side(out, "rl_away", at, "rl_away")
    ch = [_devig_pair(b["rl_home"], b["rl_away"])[0] for b in at]
    ca = [_devig_pair(b["rl_home"], b["rl_away"])[1] for b in at]
    out["cons_rl_home"], out["cons_rl_away"] = _median(ch), _median(ca)

    # ---- total: same treatment, modal line
    tots = [b["total"] for b in books if b["total"] is not None]
    if tots:
        t = max(set(tots), key=tots.count)
        at = [b for b in books if b["total"] == t]
        out["total"] = t
        _side(out, "over", at, "over")
        _side(out, "under", at, "under")
        co = [_devig_pair(b["over"], b["under"])[0] for b in at]
        cu = [_devig_pair(b["over"], b["under"])[1] for b in at]
        out["cons_over"], out["cons_under"] = _median(co), _median(cu)
        out["total_book"] = out.get("over_book")
        # how much books disagree - a wide spread of totals means a soft market
        out["total_spread"] = round(max(tots) - min(tots), 1)
    return out


CORE = ("https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb"
        "/events/{event}/competitions/{competition}/odds?lang=en&region=us")


def _core_books(event_id, competition_id, home_abbr, away_abbr) -> list[dict]:
    """
    ESPN's core odds endpoint, per game.

    The scoreboard sometimes ships a game with no odds block at all while this
    endpoint has one, and occasionally the reverse. Reading both and merging is
    strictly better than picking one - but only as long as an empty answer from
    either is treated as "nothing extra", never as "this game has no price".
    Making this the only source is what emptied the feed.
    """
    if not event_id or not competition_id:
        return []
    js = get_json(CORE.format(event=event_id, competition=competition_id),
                  cache_hours=0.05, quiet=True)
    if not js:
        return []
    return _books_from({"odds": js.get("items") or []}, home_abbr, away_abbr)


def _market_score(row: dict, line_key: str, price_a: str, price_b: str) -> int:
    """How complete one book's quote is for a market."""
    return ((1 if row.get(line_key) is not None else 0)
            + (2 if valid_price(row.get(price_a)) else 0)
            + (2 if valid_price(row.get(price_b)) else 0))


def _merge_quote(first: dict, later: dict) -> dict:
    """Combine two sightings of the same sportsbook without losing prices.

    ESPN's scoreboard and core endpoints commonly return the same provider.  A
    line-only scoreboard sighting must not shadow a complete core quote merely
    because it was fetched first.  Markets are selected independently so a
    complete scoreboard moneyline can coexist with a complete core total.
    """
    out = dict(first)

    # Moneyline has no line value to keep in sync.
    first_ml = sum(valid_price(first.get(k)) for k in ("ml_away", "ml_home"))
    later_ml = sum(valid_price(later.get(k)) for k in ("ml_away", "ml_home"))
    if later_ml > first_ml:
        for key in ("ml_away", "ml_home"):
            out[key] = later.get(key)
    else:
        for key in ("ml_away", "ml_home"):
            if not valid_price(out.get(key)) and valid_price(later.get(key)):
                out[key] = later.get(key)

    # Run line and total prices only belong with the line from the same quote.
    for line_key, a_key, b_key in (("rl_line", "rl_home", "rl_away"),
                                   ("total", "over", "under")):
        if _market_score(later, line_key, a_key, b_key) > _market_score(
                first, line_key, a_key, b_key):
            for key in (line_key, a_key, b_key):
                out[key] = later.get(key)
        elif later.get(line_key) == out.get(line_key):
            for key in (a_key, b_key):
                if not valid_price(out.get(key)) and valid_price(later.get(key)):
                    out[key] = later.get(key)
    return out


def _merge_books(*groups) -> list[dict]:
    """Books from every source, one complete entry per provider."""
    positions, out = {}, []
    for group in groups:
        for b in group or []:
            name = canon_book(b.get("book") or "book")
            row = dict(b)
            row["book"] = name
            if name in positions:
                i = positions[name]
                out[i] = _merge_quote(out[i], row)
            else:
                positions[name] = len(out)
                out.append(row)
    return out


# --------------------------------------------------------- feed sanity -----
def has_priced_market(rec: dict | None) -> bool:
    """Is there anything here you could actually bet into?"""
    if not rec:
        return False
    ml = valid_price(rec.get("ml_away")) and valid_price(rec.get("ml_home"))
    rl = (rec.get("rl_line") is not None
          and valid_price(rec.get("rl_home")) and valid_price(rec.get("rl_away")))
    total = (rec.get("total") is not None
             and valid_price(rec.get("over")) and valid_price(rec.get("under")))
    return ml or rl or total


def suspicious_record(rec: dict | None) -> bool:
    """
    Prices that cannot be real.

    Not a taste test - these are arithmetic impossibilities and obvious
    placeholders. A real two-way market implies somewhere between 100% and about
    112%; anything outside that is a parsing error or a stale half-filled quote,
    and pricing a bet off it would invent an edge out of nothing.
    """
    if not rec:
        return True
    a, h = rec.get("ml_away"), rec.get("ml_home")
    if a is not None and h is not None:
        if a == h:                                  # both sides the same price
            return True
        for p in (a, h):
            if abs(p) < 100 or abs(p) > 5000:       # outside any real board
                return True
        # The two-sided sum test only applies to a single book's own pair. Once
        # several books are merged, the best price on each side comes from
        # different places and can legitimately imply under 100% - that is what
        # line shopping IS, and failing the record for it threw away every
        # multi-book game on the slate.
        if int(rec.get("n_books") or 1) <= 1:
            ia = 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)
            ih = 100.0 / (h + 100.0) if h > 0 else abs(h) / (abs(h) + 100.0)
            book_sum = ia + ih
            if book_sum < 0.98 or book_sum > 1.25:
                return True
        else:
            # Across books, the best two prices can imply under 100% - that is
            # ordinary line shopping, and occasionally a genuine arbitrage. Only
            # flag a sum no real board could produce, which means a parse error.
            ia = 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)
            ih = 100.0 / (h + 100.0) if h > 0 else abs(h) / (abs(h) + 100.0)
            if not (0.90 <= ia + ih <= 1.30):
                return True
    t = rec.get("total")
    if t is not None and not (3.0 <= float(t) <= 20.0):
        return True
    return False


def feed_health(records: dict, expected_games: int | None = None) -> dict:
    """
    A plain account of what the odds feed actually gave us, so an empty board
    reads as an empty board rather than as a model with no opinions.
    """
    n = len(records)
    priced = sum(1 for r in records.values() if has_priced_market(r))
    with_ml = sum(1 for r in records.values()
                  if valid_price(r.get("ml_away")) and valid_price(r.get("ml_home")))
    with_rl = sum(1 for r in records.values()
                  if r.get("rl_line") is not None
                  and valid_price(r.get("rl_home")) and valid_price(r.get("rl_away")))
    with_tot = sum(1 for r in records.values()
                   if r.get("total") is not None
                   and valid_price(r.get("over")) and valid_price(r.get("under")))
    books = sorted({b for r in records.values() for b in (r.get("books") or [])})
    return {
        "games_with_odds": n, "expected_games": expected_games,
        "priced": priced, "with_moneyline": with_ml, "with_runline": with_rl,
        "with_total": with_tot,
        "books": books, "n_books": len(books),
        "coverage": (round(priced / expected_games, 3)
                     if expected_games else None),
        "sources": sorted({s for r in records.values()
                           for s in (r.get("sources") or [])}),
    }


def odds_for_date(date_str: str) -> dict:
    """'YYYY-MM-DD' -> {(AWAY,HOME): odds record}."""
    d = date_str.replace("-", "")
    js = get_json(SB.format(d=d))
    out = {}
    if not js:
        return out
    fetched = datetime.now(timezone.utc).isoformat()
    for ev in js.get("events", []):
        for comp in ev.get("competitions", []):
            teams = {}
            for c in comp.get("competitors", []):
                teams[c.get("homeAway")] = _abbr((c.get("team") or {}).get("abbreviation"))
            if "home" not in teams or "away" not in teams:
                continue
            board = _books_from(comp, teams["home"], teams["away"])
            core = _core_books(ev.get("id"), comp.get("id") or ev.get("id"),
                               teams["home"], teams["away"])
            books = _merge_books(board, core)
            if not books:
                continue
            rec = _consensus(books, teams["home"], teams["away"])
            rec["sources"] = ([s for s, g in (("scoreboard", board), ("core", core)) if g])
            # A game with nothing usable is simply a game with no price yet -
            # it still belongs in the feed, and the model still publishes a fair
            # line for it. Only drop records whose numbers cannot be real.
            if not has_priced_market(rec) or suspicious_record(rec):
                continue
            rec.setdefault("rl_line", -1.5)
            # There used to be four lines here that filled a missing run-line or
            # total price in with -110. That was the worst bug in this project.
            # A market nobody has priced is not a market priced at -110: the
            # invented number got de-vigged into a fake 50/50 opinion, the model
            # measured its edge against that, and a real -217 line could show up
            # as a huge edge that never existed. A missing price is now missing.
            # The model still publishes its own fair line for the market; it is
            # labelled a fair line and it is not bettable.
            rec["book"] = (f"{rec['n_books']} books"
                           if rec["n_books"] > 1 else rec["books"][0])
            rec["preferred_book"] = getattr(C, "PREFERRED_BOOK", None)
            rec["fetched_at"] = fetched
            rec["espn_id"] = ev.get("id")
            out[(teams["away"], teams["home"])] = rec
    return out


def scores_for_date(date_str: str) -> dict:
    """Backup final scores if the MLB API is having a day."""
    d = date_str.replace("-", "")
    js = get_json(SB.format(d=d))
    out = {}
    if not js:
        return out
    for ev in js.get("events", []):
        for comp in ev.get("competitions", []):
            st = ((comp.get("status") or {}).get("type") or {})
            if not st.get("completed"):
                continue
            rec = {}
            for c in comp.get("competitors", []):
                rec[c.get("homeAway")] = {"abbr": _abbr((c.get("team") or {}).get("abbreviation")),
                                          "score": _num(c.get("score"))}
            if "home" in rec and "away" in rec:
                out[(rec["away"]["abbr"], rec["home"]["abbr"])] = {
                    "away": rec["away"]["score"], "home": rec["home"]["score"]}
    return out

"""
A synthetic stand-in for every upstream feed, shaped exactly like the real
payloads. Lets the whole pipeline run end-to-end with no network so the maths,
the pricing, the grading and the JSON contract are all testable in CI.
"""
from __future__ import annotations
import json, random, re
import zlib
from datetime import datetime, timedelta, timezone

from pipeline.sources.mlb_api import TEAM_ABBR
from pipeline.sources import parks as P

RNG = random.Random(1234)
TEAM_IDS = list(TEAM_ABBR.keys())

VENUE_FOR = {
    108: "Angel Stadium", 109: "Chase Field", 110: "Oriole Park at Camden Yards",
    111: "Fenway Park", 112: "Wrigley Field", 113: "Great American Ball Park",
    114: "Progressive Field", 115: "Coors Field", 116: "Comerica Park",
    117: "Daikin Park", 118: "Kauffman Stadium", 119: "Dodger Stadium",
    120: "Nationals Park", 121: "Citi Field", 133: "Sutter Health Park",
    134: "PNC Park", 135: "Petco Park", 136: "T-Mobile Park", 137: "Oracle Park",
    138: "Busch Stadium", 139: "George M. Steinbrenner Field", 140: "Globe Life Field",
    141: "Rogers Centre", 142: "Target Field", 143: "Citizens Bank Park",
    144: "Truist Park", 145: "Rate Field", 146: "loanDepot park",
    147: "Yankee Stadium", 158: "American Family Field",
}

# fixed per-team talent so repeated calls agree with each other
TALENT = {tid: {"off": RNG.gauss(0, 0.045), "sp": RNG.gauss(0, 0.050),
                "pen": RNG.gauss(0, 0.040)} for tid in TEAM_IDS}
_PID = {}


def pid(tid, i, kind):
    return _PID.setdefault((tid, i, kind), 100000 + len(_PID))


def _hitting_line(rng, quality):
    pa = rng.randint(220, 620)
    hr = max(0, int(pa * max(0.005, rng.gauss(0.034 * (1 + quality), 0.014))))
    bb = int(pa * max(0.02, rng.gauss(0.085 * (1 + quality * 0.5), 0.028)))
    so = int(pa * min(0.42, max(0.08, rng.gauss(0.222 - quality * 0.03, 0.055))))
    d2 = int(pa * max(0.01, rng.gauss(0.045 * (1 + quality), 0.013)))
    d3 = int(pa * max(0.0, rng.gauss(0.004, 0.003)))
    s1 = int(pa * max(0.05, rng.gauss(0.140 * (1 + quality * 0.6), 0.025)))
    hits = s1 + d2 + d3 + hr
    ab = pa - bb - int(pa * 0.01)
    return {"plateAppearances": pa, "atBats": ab, "hits": hits, "doubles": d2,
            "triples": d3, "homeRuns": hr, "baseOnBalls": bb, "intentionalWalks": 2,
            "hitByPitch": int(pa * 0.01), "strikeOuts": so, "sacFlies": 3,
            "stolenBases": rng.randint(0, 14), "rbi": int(hits * 0.55),
            "runs": int(hits * 0.55), "avg": f"{hits/max(ab,1):.3f}",
            "obp": f"{(hits+bb)/max(pa,1):.3f}",
            "slg": f"{(s1+2*d2+3*d3+4*hr)/max(ab,1):.3f}",
            "ops": f"{((hits+bb)/max(pa,1))+((s1+2*d2+3*d3+4*hr)/max(ab,1)):.3f}"}


def _pitching_line(rng, quality, starter):
    tbf = rng.randint(500, 750) if starter else rng.randint(90, 300)
    gs = rng.randint(18, 30) if starter else 0
    gp = gs if starter else rng.randint(35, 70)
    so = int(tbf * max(0.10, rng.gauss(0.225 + quality * 0.04, 0.045)))
    bb = int(tbf * max(0.02, rng.gauss(0.080 - quality * 0.02, 0.022)))
    hr = int(tbf * max(0.005, rng.gauss(0.032 - quality * 0.012, 0.011)))
    d2 = int(tbf * max(0.015, rng.gauss(0.045 - quality * 0.008, 0.010)))
    d3 = int(tbf * 0.004)
    s1 = int(tbf * max(0.06, rng.gauss(0.140 - quality * 0.02, 0.020)))
    hits = s1 + d2 + d3 + hr
    outs = int(tbf * 0.70)
    ip = f"{outs//3}.{outs%3}"
    er = int((hits * 0.35 + hr * 1.4))
    era = er * 9 / max(outs / 3, 1)
    return {"battersFaced": tbf, "gamesStarted": gs, "gamesPitched": gp,
            "gamesPlayed": gp, "inningsPitched": ip, "strikeOuts": so,
            "baseOnBalls": bb, "intentionalWalks": 1, "hitByPitch": int(tbf * 0.01),
            "hits": hits, "doubles": d2, "triples": d3, "homeRuns": hr,
            "earnedRuns": er, "era": f"{era:.2f}",
            "whip": f"{(hits+bb)/max(outs/3,1):.2f}",
            "strikeoutsPer9Inn": f"{so*9/max(outs/3,1):.2f}",
            "walksPer9Inn": f"{bb*9/max(outs/3,1):.2f}",
            "homeRunsPer9": f"{hr*9/max(outs/3,1):.2f}",
            "saves": rng.randint(0, 25) if not starter else 0,
            "holds": rng.randint(0, 20) if not starter else 0}


def _roster(tid, group):
    rng = random.Random(tid * 31 + (7 if group == "hitting" else 13))
    t = TALENT[tid]
    people = []
    if group == "hitting":
        for i in range(14):
            q = t["off"] + rng.gauss(0, 0.060)
            people.append({
                "person": {"id": pid(tid, i, "b"), "fullName": f"{TEAM_ABBR[tid]} Batter {i+1}",
                           "primaryPosition": {"abbreviation": ["C","1B","2B","3B","SS","LF","CF","RF","DH"][i % 9]},
                           "batSide": {"code": rng.choice("RRRLLS")},
                           "stats": [{"splits": [{"stat": _hitting_line(rng, q)}]}]}})
    else:
        for i in range(6):
            q = t["sp"] + rng.gauss(0, 0.045)
            people.append({"person": {"id": pid(tid, i, "p"), "fullName": f"{TEAM_ABBR[tid]} Starter {i+1}",
                                      "pitchHand": {"code": rng.choice("RRRL")},
                                      "primaryPosition": {"abbreviation": "P"},
                                      "stats": [{"splits": [{"stat": _pitching_line(rng, q, True)}]}]}})
        for i in range(9):
            q = t["pen"] + rng.gauss(0, 0.050)
            people.append({"person": {"id": pid(tid, 100 + i, "p"), "fullName": f"{TEAM_ABBR[tid]} Reliever {i+1}",
                                      "pitchHand": {"code": rng.choice("RRRL")},
                                      "primaryPosition": {"abbreviation": "P"},
                                      "stats": [{"splits": [{"stat": _pitching_line(rng, q, False)}]}]}})
    return {"roster": people}


COUNT_KEYS = ("hits", "doubles", "triples", "homeRuns", "baseOnBalls",
              "intentionalWalks", "hitByPitch", "strikeOuts", "atBats",
              "sacFlies", "stolenBases", "rbi", "runs", "earnedRuns")


def _rescale(st: dict, denom_key: str, target: int) -> dict:
    """Shrink a generated stat line to a smaller sample, counts and all.

    Rewriting the denominator without rewriting the counts produces a hitter
    with a .900 batting average, which is exactly the kind of impossible input
    the model should never see from a fixture pretending to be real.
    """
    old = float(st.get(denom_key) or 1.0)
    f = max(target, 1) / max(old, 1.0)
    out = dict(st)
    for k in COUNT_KEYS:
        if k in out:
            out[k] = int(round(float(out[k]) * f))
    out[denom_key] = int(target)
    return out


def _matchups(date_str):
    rng = random.Random(_h(date_str) & 0xFFFF)
    ids = TEAM_IDS[:]
    rng.shuffle(ids)
    return [(ids[i], ids[i + 1]) for i in range(0, 30, 2)]


def _schedule(date_str, final=False):
    games = []
    base = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc) + timedelta(hours=23)
    for n, (away, home) in enumerate(_matchups(date_str)):
        rng = random.Random(_h(date_str, away, home) & 0xFFFFFF)
        venue = VENUE_FOR[home]
        pk = P.lookup(venue)
        gp = 800000 + n + (_h(date_str) & 0xFFFF)
        g = {
            "gamePk": gp, "gameDate": (base + timedelta(minutes=15 * n)).isoformat().replace("+00:00", "Z"),
            "gameType": "R", "dayNight": "night", "doubleHeader": "N",
            "status": {"detailedState": "Final" if final else "Scheduled",
                       "abstractGameState": "Final" if final else "Preview"},
            "venue": {"name": venue, "location": {"defaultCoordinates":
                      {"latitude": pk["lat"], "longitude": pk["lon"]}}},
            "teams": {
                "away": {"team": {"id": away, "name": TEAM_ABBR[away], "abbreviation": TEAM_ABBR[away]},
                         "probablePitcher": {"id": pid(away, rng.randint(0, 4), "p"),
                                             "fullName": f"{TEAM_ABBR[away]} Starter",
                                             "pitchHand": {"code": "R"}}},
                "home": {"team": {"id": home, "name": TEAM_ABBR[home], "abbreviation": TEAM_ABBR[home]},
                         "probablePitcher": {"id": pid(home, rng.randint(0, 4), "p"),
                                             "fullName": f"{TEAM_ABBR[home]} Starter",
                                             "pitchHand": {"code": "L"}}}},
            "lineups": {} if n % 3 else {
                "awayPlayers": [{"id": pid(away, i, "b")} for i in range(9)],
                "homePlayers": [{"id": pid(home, i, "b")} for i in range(9)]},
        }
        if final:
            ar, hr_ = rng.randint(0, 11), rng.randint(0, 11)
            if ar == hr_:
                hr_ += 1
            g["teams"]["away"]["score"] = ar
            g["teams"]["home"]["score"] = hr_
            g["linescore"] = {"innings": [
                {"away": {"runs": 1 if i < ar else 0}, "home": {"runs": 1 if i < hr_ else 0}}
                for i in range(9)]}
        games.append(g)
    return {"totalGames": len(games), "dates": [{"date": date_str, "games": games}]}


def _standings():
    recs = []
    for i in range(0, 30, 5):
        trs = []
        for tid in TEAM_IDS[i:i + 5]:
            rng = random.Random(tid)
            w = rng.randint(50, 80)
            trs.append({"team": {"id": tid, "name": TEAM_ABBR[tid]}, "wins": w,
                        "losses": 128 - w, "runsScored": rng.randint(480, 660),
                        "runsAllowed": rng.randint(480, 660),
                        "runDifferential": rng.randint(-120, 140),
                        "winningPercentage": f"{w/128:.3f}",
                        "streak": {"streakCode": "W2"}, "gamesBack": "-",
                        "records": {"splitRecords": [{"type": "lastTen", "wins": 5, "losses": 5}]}})
        recs.append({"division": {"id": 200 + i}, "teamRecords": trs})
    return {"records": recs}


def _h(*parts) -> int:
    """A hash that is the same on every run.

    Python randomises str.hash per process, so the fixture used to generate
    different game ids and different odds on every invocation - which makes a
    failing test unreproducible and a passing one worth very little.
    """
    return zlib.crc32("|".join(str(p) for p in parts).encode())


def _espn(date_str):
    events = []
    for away, home in _matchups(date_str):
        rng = random.Random(_h(date_str, away, home, "odds") & 0xFFFFFF)
        # a market that is roughly right, the way a real one is: price off the
        # talent gap plus home field, then add a little noise and some vig
        override = MARKET_OVERRIDE.get((TEAM_ABBR[away], TEAM_ABBR[home]))
        if override is not None:
            p_home = min(max(override + rng.gauss(0, 0.018), 0.20), 0.80)
        else:
            gap = (TALENT[home]["off"] + TALENT[home]["sp"] + TALENT[home]["pen"]
                   - TALENT[away]["off"] - TALENT[away]["sp"] - TALENT[away]["pen"])
            p_home = min(max(0.532 + gap * 1.6 + rng.gauss(0, 0.02), 0.28), 0.72)
        def price(p):
            p = min(max(p * 1.022, 0.02), 0.97)
            return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)
        ml_h, ml_a = price(p_home), price(1 - p_home)
        fav_home = p_home >= 0.5
        # Several books, each a shade different, the way the real feed comes back.
        base_total = rng.choice([7.0, 7.5, 8.0, 8.5, 9.0, 9.5])
        odds_blocks = []
        for name, jitter in (("DraftKings", 0), ("FanDuel", 6), ("ESPN BET", -5)):
            odds_blocks.append({
                "provider": {"name": name},
                "details": f"{TEAM_ABBR[home if fav_home else away]} -1.5",
                "spread": -1.5,
                "overUnder": base_total + rng.choice([0, 0, 0.5]),
                "overOdds": -110 + jitter, "underOdds": -110 - jitter,
                "awayTeamOdds": {"moneyLine": ml_a + jitter,
                                 "spreadOdds": rng.choice([-135, 145])},
                "homeTeamOdds": {"moneyLine": ml_h - jitter,
                                 "spreadOdds": rng.choice([-135, 145])}})
        # Real feeds routinely include a book that has not hung this game yet and
        # publishes 0 across the board. That zero reached the decimal-payout
        # conversion and divided by zero, killing a whole live build, so the
        # fixture carries one on every third game to keep the path exercised.
        # Real books very often post a moneyline and a total LINE while the
        # run-line and over/under PRICES are still absent. That used to be
        # backfilled with an invented -110; the fixture reproduces it so the
        # test can prove nothing is invented any more.
        if (900000 + _h(date_str, away, home) % 90000) % 4 == 1:
            for blk in odds_blocks:
                blk["overOdds"] = blk["underOdds"] = None
                blk["awayTeamOdds"]["spreadOdds"] = None
                blk["homeTeamOdds"]["spreadOdds"] = None
        if len(events) % 3 == 0:
            odds_blocks.insert(0, {
                "provider": {"name": "Unhung Book"},
                "details": f"{TEAM_ABBR[home if fav_home else away]} -1.5",
                "spread": -1.5, "overUnder": base_total,
                "overOdds": 0, "underOdds": 0,
                "awayTeamOdds": {"moneyLine": 0, "spreadOdds": 0},
                "homeTeamOdds": {"moneyLine": 0, "spreadOdds": 0}})
        eid = 900000 + _h(date_str, away, home) % 90000
        CORE_PRICES[eid] = (ml_a, ml_h, base_total)
        events.append({"id": str(eid), "competitions": [{"id": str(eid),
            "competitors": [
                {"homeAway": "home", "team": {"abbreviation": TEAM_ABBR[home]}, "score": None},
                {"homeAway": "away", "team": {"abbreviation": TEAM_ABBR[away]}, "score": None}],
            "odds": odds_blocks,
            "status": {"type": {"completed": False}}}]})
    return {"events": events}


def _weather():
    rng = random.Random(99)
    # A fixed wide window around the dates the suite uses. Anchoring this to
    # datetime.now() meant the fixture silently drifted out from under the tests
    # overnight, which looked like a weather bug and was not one.
    base = datetime(2026, 8, 10, tzinfo=timezone.utc)
    times = [(base + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00")
             for h in range(0, 24 * 30)]
    n = len(times)
    return {"hourly": {"time": times,
                       "temperature_2m": [rng.uniform(55, 95) for _ in range(n)],
                       "relative_humidity_2m": [rng.uniform(25, 90) for _ in range(n)],
                       "precipitation_probability": [rng.choice([0, 0, 5, 10, 40, 70]) for _ in range(n)],
                       "wind_speed_10m": [rng.uniform(0, 18) for _ in range(n)],
                       "wind_direction_10m": [rng.uniform(0, 360) for _ in range(n)],
                       "apparent_temperature": [rng.uniform(55, 95) for _ in range(n)]}}


FINAL_DATES: set[str] = set()

# A real sportsbook is approximately right, so a fake one should be too.
# Populated by tools/make_sample.py with the model's own simulated probability
# from a first pass, then jittered - which makes the preview show the small
# disagreements a live slate actually produces instead of the wild ones a
# hand-rolled linear market invents. Left empty, the linear fallback is used,
# which is what the test suite wants: it exercises the extremes on purpose.
MARKET_OVERRIDE: dict[tuple, float] = {}

# event id -> (away moneyline, home moneyline, total), filled in when the fake
# scoreboard is built so the fake core endpoint can quote the same game.
CORE_PRICES: dict[int, tuple] = {}


def responder(url: str, **kw):
    """Route a URL to a synthetic payload."""
    if "statsapi.mlb.com" in url:
        if "/schedule?" in url:
            d = re.search(r"date=(\d{4}-\d{2}-\d{2})", url).group(1)
            return _schedule(d, final=(d in FINAL_DATES))
        if "/standings?" in url:
            return _standings()
        m = re.search(r"/teams/(\d+)/roster", url)
        if m:
            group = "hitting" if "group=hitting" in url else "pitching"
            return _roster(int(m.group(1)), group)
        if "/people?" in url:
            ids = re.search(r"personIds=([\d,]+)", url)
            group = "hitting" if "group=hitting" in url else "pitching"
            id_list = ids.group(1).split(",") if ids else []
            people = []

            if "type=statSplits" in url:
                for i in id_list:
                    rng = random.Random(int(i) * 7 + 5)
                    splits = []
                    for code, desc, q in (("vl", "vs Left", rng.gauss(0, .06)),
                                          ("vr", "vs Right", rng.gauss(0, .06))):
                        st = _hitting_line(rng, q)
                        st = _rescale(st, "plateAppearances",
                                      int(st["plateAppearances"] * rng.uniform(.25, .5)))
                        splits.append({"split": {"code": code, "description": desc}, "stat": st})
                    people.append({"id": int(i), "fullName": f"Player {i}",
                                   "batSide": {"code": "R"},
                                   "stats": [{"splits": splits}]})
                return {"people": people}

            if "type=byDateRange" in url:
                for i in id_list:
                    rng = random.Random(int(i) * 11 + 3)
                    st = (_hitting_line(rng, rng.gauss(0, .09)) if group == "hitting"
                          else _pitching_line(rng, rng.gauss(0, .09), False))
                    key = "plateAppearances" if group == "hitting" else "battersFaced"
                    st = _rescale(st, key, rng.randint(30, 120) if group == "hitting"
                                  else rng.randint(20, 130))
                    people.append({"id": int(i), "fullName": f"Player {i}",
                                   "batSide": {"code": "R"}, "pitchHand": {"code": "R"},
                                   "stats": [{"splits": [{"stat": st}]}]})
                return {"people": people}

            for i in id_list:
                rng = random.Random(int(i))
                st = _hitting_line(rng, 0) if group == "hitting" else _pitching_line(rng, 0, False)
                people.append({"id": int(i), "fullName": f"Player {i}",
                               "primaryPosition": {"abbreviation": "DH" if group == "hitting" else "P"},
                               "batSide": {"code": "R"}, "pitchHand": {"code": "R"},
                               "stats": [{"splits": [{"stat": st}]}]})
            return {"people": people}

        m = re.search(r"/game/(\d+)/boxscore", url)
        if m:
            gp = int(m.group(1))
            rng = random.Random(gp)
            teams = {}
            for side in ("away", "home"):
                tid = TEAM_IDS[rng.randrange(30)]
                order = [pid(tid, 0, "p")] + [pid(tid, 100 + k, "p")
                                              for k in rng.sample(range(9), 4)]
                players = {}
                for n, q in enumerate(order):
                    pitches = rng.randint(70, 100) if n == 0 else rng.randint(8, 42)
                    outs = rng.randint(15, 21) if n == 0 else rng.randint(1, 6)
                    players[f"ID{q}"] = {"stats": {"pitching": {
                        "numberOfPitches": pitches,
                        "inningsPitched": f"{outs//3}.{outs%3}",
                        "battersFaced": rng.randint(3, 26)}}}
                teams[side] = {"pitchers": order, "players": players}
            return {"teams": teams}
        if "/stats?" in url:
            return {"stats": []}
    if "sports.core.api.espn.com" in url:
        # Priced off the same game the scoreboard priced, jittered. Pricing it
        # at random invented arbitrages that do not happen on a real board and
        # made the sanity guard look broken when it was not.
        # ESPN's per-game odds endpoint. Returns one provider, sometimes none -
        # the model has to survive both, because making this the only source is
        # what emptied the live feed.
        m = re.search(r"/events/(\d+)/", url)
        eid = int(m.group(1)) if m else 0
        quote = CORE_PRICES.get(eid)
        rng = random.Random(eid)
        if quote is None or rng.random() < 0.25:
            return {"count": 0, "items": []}       # ESPN often has nothing here
        ml_a, ml_h, tot = quote
        shade = rng.choice([-6, -3, 0, 3, 6])
        # Real books post a moneyline long before they post a run line or a
        # total price. On a deterministic slice of the slate this feed does the
        # same, so the "no price, therefore no bet" path is covered end to end
        # instead of being papered over by whichever source happens to have one.
        bare = (eid % 4 == 1)
        return {"count": 1, "items": [{
            "provider": {"name": "Core Feed"},
            "details": "HOME -1.5", "spread": -1.5,
            "overUnder": tot,
            "overOdds": None if bare else -110,
            "underOdds": None if bare else -110,
            "awayTeamOdds": {"moneyLine": ml_a + shade,
                             "spreadOdds": None if bare else -130},
            "homeTeamOdds": {"moneyLine": ml_h - shade,
                             "spreadOdds": None if bare else 110}}]}

    if "site.api.espn.com" in url:
        d = re.search(r"dates=(\d{8})", url).group(1)
        return _espn(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    if "api.open-meteo.com" in url:
        return _weather()
    return None

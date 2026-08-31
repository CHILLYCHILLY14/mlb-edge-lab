"""
MLB Stats API - schedule, probable pitchers, lineups, rosters, season stats,
standings and final scores. Keyless and free.
"""
from __future__ import annotations
from datetime import datetime, timezone

from .http import get_json
from ..config import SEASON, CACHE_TTL_STATS_H

BASE = "https://statsapi.mlb.com/api/v1"

# team id -> short code, used everywhere downstream
TEAM_ABBR = {
    108: "LAA", 109: "AZ",  110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC",  119: "LAD",
    120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT", 135: "SD",  136: "SEA",
    137: "SF",  138: "STL", 139: "TB",  140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _ip_to_outs(ip) -> float:
    """MLB reports innings pitched as 130.1 = 130 and 1/3."""
    try:
        s = str(ip)
        whole, _, frac = s.partition(".")
        return float(whole) * 3 + (float(frac[0]) if frac else 0.0)
    except Exception:
        return 0.0


# --------------------------------------------------------------- schedule ---
def schedule(date_str: str) -> list[dict]:
    """Games for a YYYY-MM-DD date, with probables, lineups, venue coords."""
    url = (f"{BASE}/schedule?sportId=1&date={date_str}"
           "&hydrate=probablePitcher(person),lineups,venue(location),team,linescore,"
           "decisions,game(content(summary))")
    js = get_json(url)
    if not js:
        return []
    out = []
    for d in js.get("dates", []):
        for g in d.get("games", []):
            status = (g.get("status") or {})
            venue = g.get("venue") or {}
            coords = ((venue.get("location") or {}).get("defaultCoordinates") or {})
            away, home = g["teams"]["away"], g["teams"]["home"]
            lu = g.get("lineups") or {}

            def sp(side):
                p = side.get("probablePitcher") or {}
                if not p.get("id"):
                    return None
                return {"id": p["id"], "name": p.get("fullName", "TBA"),
                        "hand": ((p.get("pitchHand") or {}).get("code") or "R")}

            out.append({
                "gamePk":    g["gamePk"],
                "gameDate":  g.get("gameDate"),
                "gameType":  g.get("gameType", "R"),
                "dayNight":  g.get("dayNight", "day"),
                "doubleHeader": g.get("doubleHeader", "N"),
                "status":    status.get("detailedState", "Scheduled"),
                "abstract":  status.get("abstractGameState", "Preview"),
                "venue":     venue.get("name", ""),
                "lat":       _f(coords.get("latitude"), None) if coords.get("latitude") is not None else None,
                "lon":       _f(coords.get("longitude"), None) if coords.get("longitude") is not None else None,
                "away_id":   away["team"]["id"], "home_id": home["team"]["id"],
                "away":      TEAM_ABBR.get(away["team"]["id"], away["team"].get("abbreviation", "???")),
                "home":      TEAM_ABBR.get(home["team"]["id"], home["team"].get("abbreviation", "???")),
                "away_name": away["team"].get("name", ""), "home_name": home["team"].get("name", ""),
                "away_sp":   sp(away), "home_sp": sp(home),
                "away_lineup": [p.get("id") for p in (lu.get("awayPlayers") or []) if p.get("id")],
                "home_lineup": [p.get("id") for p in (lu.get("homePlayers") or []) if p.get("id")],
                "away_score": (away.get("score")), "home_score": (home.get("score")),
                "linescore":  g.get("linescore") or {},
            })
    out.sort(key=lambda x: (x["gameDate"] or "", x["gamePk"]))
    return out


# ---------------------------------------------------------------- rosters ---
def team_batters(team_id: int, season: int = SEASON) -> list[dict]:
    url = (f"{BASE}/teams/{team_id}/roster?rosterType=active"
           f"&hydrate=person(stats(type=season,group=hitting,season={season}))")
    js = get_json(url, cache_hours=CACHE_TTL_STATS_H)
    if not js:
        return []
    rows = []
    for r in js.get("roster", []):
        p = r.get("person") or {}
        st = _first_split(p)
        if st is None:
            continue
        pa = _f(st.get("plateAppearances"))
        if pa <= 0:
            continue
        ab, h = _f(st.get("atBats")), _f(st.get("hits"))
        d2, d3, hr = _f(st.get("doubles")), _f(st.get("triples")), _f(st.get("homeRuns"))
        bb, ibb = _f(st.get("baseOnBalls")), _f(st.get("intentionalWalks"))
        hbp, so = _f(st.get("hitByPitch")), _f(st.get("strikeOuts"))
        sf, sb = _f(st.get("sacFlies")), _f(st.get("stolenBases"))
        singles = max(h - d2 - d3 - hr, 0.0)
        rows.append({
            "id": p.get("id"), "name": p.get("fullName", "?"),
            "pos": ((p.get("primaryPosition") or {}).get("abbreviation") or "?"),
            "bats": ((p.get("batSide") or {}).get("code") or "R"),
            "pa": pa, "ab": ab, "sb": sb, "sf": sf,
            "counts": {"bb": bb + hbp, "k": so, "s": singles, "d": d2, "t": d3, "hr": hr},
            "ops": _f(st.get("ops")), "obp": _f(st.get("obp")), "slg": _f(st.get("slg")),
            "avg": _f(st.get("avg")), "rbi": _f(st.get("rbi")), "runs": _f(st.get("runs")),
        })
    rows.sort(key=lambda r: -r["pa"])
    return rows


def team_pitchers(team_id: int, season: int = SEASON) -> list[dict]:
    url = (f"{BASE}/teams/{team_id}/roster?rosterType=active"
           f"&hydrate=person(stats(type=season,group=pitching,season={season}))")
    js = get_json(url, cache_hours=CACHE_TTL_STATS_H)
    if not js:
        return []
    rows = []
    for r in js.get("roster", []):
        p = r.get("person") or {}
        st = _first_split(p)
        if st is None:
            continue
        tbf = _f(st.get("battersFaced"))
        if tbf <= 0:
            continue
        h = _f(st.get("hits"))
        d2, d3, hr = _f(st.get("doubles")), _f(st.get("triples")), _f(st.get("homeRuns"))
        bb, hbp, so = _f(st.get("baseOnBalls")), _f(st.get("hitByPitch")), _f(st.get("strikeOuts"))
        gs, gp = _f(st.get("gamesStarted")), _f(st.get("gamesPitched")) or _f(st.get("gamesPlayed"))
        outs = _ip_to_outs(st.get("inningsPitched"))
        singles = max(h - d2 - d3 - hr, 0.0)
        rows.append({
            "id": p.get("id"), "name": p.get("fullName", "?"),
            "hand": ((p.get("pitchHand") or {}).get("code") or "R"),
            "tbf": tbf, "gs": gs, "gp": gp, "outs": outs,
            "is_sp": gs >= max(3.0, 0.5 * gp),
            "bf_per_start": (tbf / gs) if gs >= 3 else None,
            "counts": {"bb": bb + hbp, "k": so, "s": singles, "d": d2, "t": d3, "hr": hr},
            "era": _f(st.get("era")), "whip": _f(st.get("whip")),
            "k9": _f(st.get("strikeoutsPer9Inn")), "bb9": _f(st.get("walksPer9Inn")),
            "hr9": _f(st.get("homeRunsPer9")), "ip": _f(str(st.get("inningsPitched") or 0).split(".")[0]),
            "saves": _f(st.get("saves")), "holds": _f(st.get("holds")),
        })
    return rows


def _first_split(person: dict):
    for s in (person.get("stats") or []):
        for sp in (s.get("splits") or []):
            st = sp.get("stat")
            if st:
                return st
    return None


# -------------------------------------------------------------- standings ---
def standings(season: int = SEASON) -> dict:
    url = f"{BASE}/standings?leagueId=103,104&season={season}&standingsTypes=regularSeason"
    js = get_json(url, cache_hours=2.0)
    out = {}
    if not js:
        return out
    for rec in js.get("records", []):
        div = (rec.get("division") or {}).get("id")
        for tr in rec.get("teamRecords", []):
            tid = (tr.get("team") or {}).get("id")
            if tid is None:
                continue
            out[tid] = {
                "abbr": TEAM_ABBR.get(tid, "?"),
                "name": (tr.get("team") or {}).get("name", ""),
                "w": int(tr.get("wins", 0)), "l": int(tr.get("losses", 0)),
                "rs": int(tr.get("runsScored", 0) or 0),
                "ra": int(tr.get("runsAllowed", 0) or 0),
                "diff": int(tr.get("runDifferential", 0) or 0),
                "pct": _f(tr.get("winningPercentage")),
                "streak": ((tr.get("streak") or {}).get("streakCode") or ""),
                "l10": _l10(tr), "div_id": div,
                "gb": tr.get("gamesBack", "-"),
            }
    return out


def _l10(tr) -> str:
    for r in ((tr.get("records") or {}).get("splitRecords") or []):
        if r.get("type") == "lastTen":
            return f"{r.get('wins',0)}-{r.get('losses',0)}"
    return ""


# ------------------------------------------------------------------ final ---
def final_scores(date_str: str) -> dict:
    """gamePk -> {away, home, f5_away, f5_home, final} for settled games."""
    out = {}
    for g in schedule(date_str):
        if g["abstract"] != "Final":
            continue
        ls = g.get("linescore") or {}
        innings = ls.get("innings") or []
        f5a = sum(_f((i.get("away") or {}).get("runs")) for i in innings[:5])
        f5h = sum(_f((i.get("home") or {}).get("runs")) for i in innings[:5])
        out[g["gamePk"]] = {
            "away": g["away_score"], "home": g["home_score"],
            "f5_away": f5a, "f5_home": f5h,
            "innings_played": len(innings),
            "final": True, "status": g["status"],
        }
    return out


# ------------------------------------------------------------ people --------
def people_stats(person_ids: list[int], group: str, season: int = SEASON) -> dict:
    """Bulk season stats for arbitrary player ids (lineup players off the
    active roster, traded starters, September call-ups)."""
    ids = [str(i) for i in person_ids if i]
    if not ids:
        return {}
    out = {}
    for chunk in (ids[i:i + 40] for i in range(0, len(ids), 40)):
        url = (f"{BASE}/people?personIds={','.join(chunk)}"
               f"&hydrate=stats(group={group},type=season,season={season})")
        js = get_json(url, cache_hours=CACHE_TTL_STATS_H, quiet=True)
        if not js:
            continue
        for p in js.get("people", []):
            st = _first_split(p)
            if not st:
                continue
            h = _f(st.get("hits"))
            d2, d3, hr = _f(st.get("doubles")), _f(st.get("triples")), _f(st.get("homeRuns"))
            bb, hbp, so = _f(st.get("baseOnBalls")), _f(st.get("hitByPitch")), _f(st.get("strikeOuts"))
            singles = max(h - d2 - d3 - hr, 0.0)
            counts = {"bb": bb + hbp, "k": so, "s": singles, "d": d2, "t": d3, "hr": hr}
            if group == "hitting":
                out[p["id"]] = {
                    "id": p["id"], "name": p.get("fullName", "?"),
                    "pos": ((p.get("primaryPosition") or {}).get("abbreviation") or "?"),
                    "bats": ((p.get("batSide") or {}).get("code") or "R"),
                    "pa": _f(st.get("plateAppearances")), "ab": _f(st.get("atBats")),
                    "sb": _f(st.get("stolenBases")), "sf": _f(st.get("sacFlies")),
                    "counts": counts, "ops": _f(st.get("ops")), "obp": _f(st.get("obp")),
                    "slg": _f(st.get("slg")), "avg": _f(st.get("avg")),
                    "rbi": _f(st.get("rbi")), "runs": _f(st.get("runs")),
                }
            else:
                gs, gp = _f(st.get("gamesStarted")), _f(st.get("gamesPitched")) or _f(st.get("gamesPlayed"))
                out[p["id"]] = {
                    "id": p["id"], "name": p.get("fullName", "?"),
                    "hand": ((p.get("pitchHand") or {}).get("code") or "R"),
                    "tbf": _f(st.get("battersFaced")), "gs": gs, "gp": gp,
                    "outs": _ip_to_outs(st.get("inningsPitched")),
                    "is_sp": gs >= max(3.0, 0.5 * gp),
                    "bf_per_start": (_f(st.get("battersFaced")) / gs) if gs >= 3 else None,
                    "counts": counts, "era": _f(st.get("era")), "whip": _f(st.get("whip")),
                    "k9": _f(st.get("strikeoutsPer9Inn")), "bb9": _f(st.get("walksPer9Inn")),
                    "hr9": _f(st.get("homeRunsPer9")),
                    "ip": _f(str(st.get("inningsPitched") or 0).split(".")[0]),
                }
    return out


def team_season_hitting(team_id: int, season: int = SEASON) -> dict:
    """Whole-team hitting line - used for the power ratings sanity check."""
    url = f"{BASE}/teams/{team_id}/stats?stats=season&group=hitting&season={season}&gameType=R"
    js = get_json(url, cache_hours=CACHE_TTL_STATS_H, quiet=True)
    st = None
    for s in (js or {}).get("stats", []):
        for sp in s.get("splits", []):
            st = sp.get("stat")
    return st or {}


def _counts_from(st: dict) -> dict:
    h = _f(st.get("hits"))
    d2, d3, hr = _f(st.get("doubles")), _f(st.get("triples")), _f(st.get("homeRuns"))
    bb, hbp, so = _f(st.get("baseOnBalls")), _f(st.get("hitByPitch")), _f(st.get("strikeOuts"))
    return {"bb": bb + hbp, "k": so, "s": max(h - d2 - d3 - hr, 0.0),
            "d": d2, "t": d3, "hr": hr}


# ---------------------------------------------------------- platoon splits --
def hitting_splits(person_ids: list[int], season: int = SEASON) -> dict:
    """
    Per-hitter performance against left- and right-handed pitching.

    Replaces a single league-average platoon constant with what the hitter has
    actually done. The constant is fine for a whole lineup; it is badly wrong for
    the individual bats that decide a close projection.
    """
    ids = [str(i) for i in person_ids if i]
    out = {}
    for chunk in (ids[i:i + 40] for i in range(0, len(ids), 40)):
        url = (f"{BASE}/people?personIds={','.join(chunk)}"
               f"&hydrate=stats(group=hitting,type=statSplits,sitCodes=[vl,vr],season={season})")
        js = get_json(url, cache_hours=CACHE_TTL_STATS_H, quiet=True)
        if not js:
            continue
        for p in js.get("people", []):
            rec = {}
            for s in (p.get("stats") or []):
                for sp in (s.get("splits") or []):
                    code = ((sp.get("split") or {}).get("code") or "").lower()
                    st = sp.get("stat") or {}
                    pa = _f(st.get("plateAppearances"))
                    if code in ("vl", "vr") and pa > 0:
                        rec[code] = {"pa": pa, "counts": _counts_from(st)}
            if rec:
                out[p["id"]] = rec
    return out


# ------------------------------------------------------------ recent form --
def stats_by_range(person_ids: list[int], group: str, start: str, end: str,
                   season: int = SEASON) -> dict:
    """
    Stats over a date window only. Season-to-date numbers are the right base but
    they are slow to notice a hitter who stopped hitting in June, so the model
    blends a rolling window on top.
    """
    ids = [str(i) for i in person_ids if i]
    out = {}
    for chunk in (ids[i:i + 40] for i in range(0, len(ids), 40)):
        url = (f"{BASE}/people?personIds={','.join(chunk)}"
               f"&hydrate=stats(group={group},type=byDateRange,"
               f"startDate={start},endDate={end},season={season})")
        js = get_json(url, cache_hours=CACHE_TTL_STATS_H, quiet=True)
        if not js:
            continue
        for p in js.get("people", []):
            st = _first_split(p)
            if not st:
                continue
            denom = _f(st.get("plateAppearances")) if group == "hitting" else _f(st.get("battersFaced"))
            if denom <= 0:
                continue
            out[p["id"]] = {"denom": denom, "counts": _counts_from(st)}
    return out


# ------------------------------------------------------ bullpen availability -
def boxscore_usage(game_pk: int) -> dict:
    """{pitcherId: {pitches, outs, bf, started}} for one finished game."""
    js = get_json(f"{BASE}/game/{game_pk}/boxscore", cache_hours=48.0, quiet=True)
    out = {}
    if not js:
        return out
    for side in ("away", "home"):
        team = (js.get("teams") or {}).get(side) or {}
        players = team.get("players") or {}
        order = team.get("pitchers") or []
        for n, pid in enumerate(order):
            p = players.get(f"ID{pid}") or {}
            st = ((p.get("stats") or {}).get("pitching") or {})
            if not st:
                continue
            out[pid] = {
                "pitches": _f(st.get("numberOfPitches")),
                "outs": _ip_to_outs(st.get("inningsPitched")),
                "bf": _f(st.get("battersFaced")),
                "started": n == 0,
            }
    return out


def recent_workload(team_ids: list[int], dates: list[str]) -> dict:
    """
    Who has thrown, how much, and how recently.

    Returns {pitcherId: {"days": {date: {...}}, "pitches_1": .., "pitches_2": ..,
    "apps_3": .., "last": date, "last_start": date}}.
    """
    want = set(team_ids)
    out: dict[int, dict] = {}
    for i, ds in enumerate(dates):                     # dates newest first
        for g in schedule(ds):
            if g["abstract"] != "Final":
                continue
            if g["away_id"] not in want and g["home_id"] not in want:
                continue
            for pid, u in boxscore_usage(g["gamePk"]).items():
                rec = out.setdefault(pid, {"days": {}, "apps_3": 0, "pitches_1": 0.0,
                                           "pitches_2": 0.0, "pitches_3": 0.0,
                                           "last": None, "last_start": None})
                rec["days"][ds] = u
                if i == 0:
                    rec["pitches_1"] += u["pitches"]
                if i <= 1:
                    rec["pitches_2"] += u["pitches"]
                if i <= 2:
                    rec["pitches_3"] += u["pitches"]
                    rec["apps_3"] += 1
                if rec["last"] is None or ds > rec["last"]:
                    rec["last"] = ds
                if u["started"] and (rec["last_start"] is None or ds > rec["last_start"]):
                    rec["last_start"] = ds
    return out

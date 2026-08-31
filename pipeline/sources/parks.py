"""
Park factors, coordinates, roof status and field orientation.

Keyed by normalised venue NAME (the MLB Stats API gives us the venue name on
every game, and names survive team relocations better than ids do).

run  = multi-year runs park factor, 100 = neutral
hr   = multi-year home-run park factor, 100 = neutral
roof = "open" | "retractable" | "dome"
cf   = approximate compass bearing (degrees) from home plate toward centre
       field. Used to resolve wind into an out-to-CF / in-from-CF component.
       These are approximations of published stadium orientations - edit
       freely, the model reads them straight from this table.
"""
from __future__ import annotations
import re

NEUTRAL = {"run": 100, "hr": 100, "roof": "open", "cf": 60, "lat": None, "lon": None}

PARKS = {
    "angel stadium":                  {"run": 98,  "hr": 103, "roof": "open",        "cf": 45,  "lat": 33.8003, "lon": -117.8827},
    "chase field":                    {"run": 103, "hr": 105, "roof": "retractable", "cf": 0,   "lat": 33.4453, "lon": -112.0667},
    "oriole park at camden yards":    {"run": 100, "hr": 97,  "roof": "open",        "cf": 32,  "lat": 39.2839, "lon": -76.6217},
    "fenway park":                    {"run": 106, "hr": 96,  "roof": "open",        "cf": 45,  "lat": 42.3467, "lon": -71.0972},
    "wrigley field":                  {"run": 101, "hr": 102, "roof": "open",        "cf": 30,  "lat": 41.9484, "lon": -87.6553},
    "great american ball park":       {"run": 104, "hr": 116, "roof": "open",        "cf": 60,  "lat": 39.0975, "lon": -84.5069},
    "progressive field":              {"run": 98,  "hr": 98,  "roof": "open",        "cf": 0,   "lat": 41.4962, "lon": -81.6852},
    "coors field":                    {"run": 112, "hr": 111, "roof": "open",        "cf": 0,   "lat": 39.7559, "lon": -104.9942},
    "comerica park":                  {"run": 98,  "hr": 93,  "roof": "open",        "cf": 30,  "lat": 42.3390, "lon": -83.0485},
    "daikin park":                    {"run": 101, "hr": 106, "roof": "retractable", "cf": 345, "lat": 29.7573, "lon": -95.3555},
    "minute maid park":               {"run": 101, "hr": 106, "roof": "retractable", "cf": 345, "lat": 29.7573, "lon": -95.3555},
    "kauffman stadium":               {"run": 100, "hr": 91,  "roof": "open",        "cf": 45,  "lat": 39.0517, "lon": -94.4803},
    "dodger stadium":                 {"run": 97,  "hr": 106, "roof": "open",        "cf": 25,  "lat": 34.0739, "lon": -118.2400},
    "uniqlo field at dodger stadium": {"run": 97,  "hr": 106, "roof": "open",        "cf": 25,  "lat": 34.0739, "lon": -118.2400},
    "nationals park":                 {"run": 100, "hr": 101, "roof": "open",        "cf": 30,  "lat": 38.8730, "lon": -77.0074},
    "citi field":                     {"run": 97,  "hr": 97,  "roof": "open",        "cf": 25,  "lat": 40.7571, "lon": -73.8458},
    "sutter health park":             {"run": 105, "hr": 108, "roof": "open",        "cf": 45,  "lat": 38.5802, "lon": -121.5133},
    "oakland coliseum":               {"run": 92,  "hr": 90,  "roof": "open",        "cf": 60,  "lat": 37.7516, "lon": -122.2005},
    "pnc park":                       {"run": 98,  "hr": 92,  "roof": "open",        "cf": 65,  "lat": 40.4469, "lon": -80.0057},
    "petco park":                     {"run": 95,  "hr": 96,  "roof": "open",        "cf": 0,   "lat": 32.7073, "lon": -117.1566},
    "t-mobile park":                  {"run": 95,  "hr": 96,  "roof": "retractable", "cf": 0,   "lat": 47.5914, "lon": -122.3325},
    "oracle park":                    {"run": 95,  "hr": 88,  "roof": "open",        "cf": 60,  "lat": 37.7786, "lon": -122.3893},
    "busch stadium":                  {"run": 98,  "hr": 93,  "roof": "open",        "cf": 60,  "lat": 38.6226, "lon": -90.1928},
    "george m. steinbrenner field":   {"run": 103, "hr": 112, "roof": "open",        "cf": 30,  "lat": 27.9800, "lon": -82.5069},
    "steinbrenner field":             {"run": 103, "hr": 112, "roof": "open",        "cf": 30,  "lat": 27.9800, "lon": -82.5069},
    "tropicana field":                {"run": 97,  "hr": 96,  "roof": "dome",        "cf": 45,  "lat": 27.7683, "lon": -82.6534},
    "globe life field":               {"run": 98,  "hr": 100, "roof": "retractable", "cf": 0,   "lat": 32.7473, "lon": -97.0847},
    "rogers centre":                  {"run": 102, "hr": 105, "roof": "retractable", "cf": 0,   "lat": 43.6414, "lon": -79.3894},
    "target field":                   {"run": 99,  "hr": 98,  "roof": "open",        "cf": 15,  "lat": 44.9817, "lon": -93.2776},
    "citizens bank park":             {"run": 102, "hr": 108, "roof": "open",        "cf": 15,  "lat": 39.9061, "lon": -75.1665},
    "truist park":                    {"run": 101, "hr": 102, "roof": "open",        "cf": 40,  "lat": 33.8907, "lon": -84.4677},
    "rate field":                     {"run": 101, "hr": 107, "roof": "open",        "cf": 0,   "lat": 41.8299, "lon": -87.6338},
    "guaranteed rate field":          {"run": 101, "hr": 107, "roof": "open",        "cf": 0,   "lat": 41.8299, "lon": -87.6338},
    "loandepot park":                 {"run": 97,  "hr": 97,  "roof": "retractable", "cf": 40,  "lat": 25.7781, "lon": -80.2197},
    "yankee stadium":                 {"run": 102, "hr": 111, "roof": "open",        "cf": 20,  "lat": 40.8296, "lon": -73.9262},
    "american family field":          {"run": 100, "hr": 103, "roof": "retractable", "cf": 0,   "lat": 43.0280, "lon": -87.9710},
}


def _norm(name: str) -> str:
    name = (name or "").lower().strip()
    name = name.replace("&", "and")
    name = re.sub(r"[^a-z0-9 .\-]", "", name)
    return re.sub(r"\s+", " ", name)


def lookup(venue_name: str, lat=None, lon=None) -> dict:
    """Return park info for a venue name, falling back to neutral."""
    key = _norm(venue_name)
    p = PARKS.get(key)
    if p is None:
        # tolerate 'at'/sponsor prefixes: try the longest known key contained in it
        cands = [k for k in PARKS if k in key or key in k]
        if cands:
            p = PARKS[max(cands, key=len)]
    out = dict(NEUTRAL)
    if p:
        out.update(p)
    out["name"] = venue_name or "Unknown Park"
    out["known"] = p is not None
    if lat is not None:
        out["lat"] = lat
    if lon is not None:
        out["lon"] = lon
    return out

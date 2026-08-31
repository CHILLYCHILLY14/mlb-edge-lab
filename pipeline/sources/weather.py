"""
Open-Meteo (keyless) game-time forecast, resolved into a run-environment
multiplier the simulator can actually use.

The output is deliberately transparent: every game carries the temperature,
wind vector, roof decision and the exact percentage the model applied, so a
number on the dashboard can always be traced back to a reason.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone

from .http import get_json
from .. import config as C

API = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
       "&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,"
       "wind_speed_10m,wind_direction_10m,apparent_temperature"
       "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=UTC"
       "&past_days=2&forecast_days=10")


def _nearest_hour(times: list[str], target: datetime) -> int | None:
    best, bi = None, None
    for i, t in enumerate(times):
        try:
            dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        d = abs((dt - target).total_seconds())
        if best is None or d < best:
            best, bi = d, i
    return bi if best is not None and best <= 6 * 3600 else None


def forecast(park: dict, game_iso: str) -> dict:
    """Return a weather record + run-environment multiplier for one game."""
    neutral = {"ok": False, "temp_f": None, "wind_mph": None, "wind_dir": None,
               "wind_component": 0.0, "precip": None, "humidity": None,
               "roof": park.get("roof", "open"), "roof_closed": False,
               "run_mult": 1.0, "applied_pct": 0.0, "note": "no forecast"}
    lat, lon = park.get("lat"), park.get("lon")
    if lat is None or lon is None or not game_iso:
        return neutral
    js = get_json(API.format(lat=round(float(lat), 3), lon=round(float(lon), 3)),
                  cache_hours=0.75, quiet=True)
    if not js or "hourly" not in js:
        return neutral
    H = js["hourly"]
    try:
        target = datetime.fromisoformat(game_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return neutral
    i = _nearest_hour(H.get("time", []), target)
    if i is None:
        return neutral

    def at(key, d=None):
        arr = H.get(key) or []
        return arr[i] if i < len(arr) and arr[i] is not None else d

    temp = float(at("temperature_2m", 72.0))
    hum = float(at("relative_humidity_2m", 50.0))
    precip = float(at("precipitation_probability", 0.0)) / 100.0
    wspd = float(at("wind_speed_10m", 0.0))
    wdir = float(at("wind_direction_10m", 0.0))   # direction wind comes FROM

    roof = park.get("roof", "open")
    roof_closed = (roof == "dome") or (
        roof == "retractable" and (temp < C.ROOF_CLOSE_TEMP_F or temp > C.ROOF_CLOSE_HOT_F
                                   or precip >= C.ROOF_CLOSE_PRECIP))

    if roof_closed:
        rec = dict(neutral)
        rec.update({"ok": True, "temp_f": 72.0 if roof == "dome" else temp,
                    "wind_mph": 0.0, "wind_dir": None, "precip": precip,
                    "humidity": hum, "roof": roof, "roof_closed": True,
                    "note": "roof closed - weather neutralised"})
        return rec

    # Resolve wind onto the home-plate -> centre-field axis.
    # wdir is where the wind blows FROM; the vector it blows TOWARD is wdir+180.
    cf = float(park.get("cf", 60))
    blow_to = (wdir + 180.0) % 360.0
    ang = math.radians(((blow_to - cf + 180.0) % 360.0) - 180.0)
    comp = wspd * math.cos(ang)          # + = out to centre, - = in from centre

    eff = 0.0
    eff += (temp - 70.0) * C.TEMP_PER_F
    eff += comp * (C.WIND_OUT_PER_MPH if comp >= 0 else C.WIND_IN_PER_MPH)
    eff += ((hum - 50.0) / 10.0) * C.HUMID_PER_10PCT
    eff = max(-C.WEATHER_CAP, min(C.WEATHER_CAP, eff))
    applied = eff * C.WEATHER_WEIGHT

    bits = []
    if abs(temp - 70) >= 8:
        bits.append(f"{temp:.0f}F")
    if abs(comp) >= 6:
        bits.append(f"{abs(comp):.0f}mph {'out' if comp > 0 else 'in'}")
    if precip >= 0.4:
        bits.append(f"{precip*100:.0f}% rain")

    return {"ok": True, "temp_f": round(temp, 1), "wind_mph": round(wspd, 1),
            "wind_dir": round(wdir), "wind_component": round(comp, 1),
            "precip": round(precip, 2), "humidity": round(hum),
            "roof": roof, "roof_closed": False,
            "run_mult": 1.0 + applied, "applied_pct": round(applied * 100, 2),
            "note": ", ".join(bits) if bits else "benign"}

"""
Generate a fully synthetic feed so the dashboard can be previewed before the
first live run. Never used in production.

    python -m tools.make_sample docs/data [days]

Runs the build twice. The first pass produces the model's own simulated
probability for every game; the second pass prices the fake sportsbook off
those numbers plus noise and vig. That matters: a hand-rolled market invents
disagreements a real one never has, which made the preview show a slate-wide
divergence warning that a live slate would not.
"""
from __future__ import annotations
import os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import fake_api
from pipeline.sources import mlb_api, espn, weather
mlb_api.get_json = fake_api.responder
espn.get_json = fake_api.responder
weather.get_json = fake_api.responder

from pipeline import build as B, grade as G, predict as PR, config as C

PAST = ["2026-08-16", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]
TODAY = "2026-08-21"


def _collect_market(feed_dir: str) -> dict:
    """Read a built feed and hand back each game's simulated home win chance."""
    import glob, json
    out = {}
    for f in glob.glob(os.path.join(feed_dir, "slate-*.json")):
        with open(f) as fh:
            for g in json.load(fh).get("games", []):
                out[(g["away"], g["home"])] = g["sim"]["p_sim_home"]
    return out


def _run(out_dir: str, days: int, grade: bool) -> None:
    for d in PAST:
        B.main(["--date", d, "--days", "1", "--out", out_dir, "--no-grade"])
        fake_api.FINAL_DATES.add(d)
    if grade:
        G.grade_all()
        PR.grade()
    B.main(["--date", TODAY, "--days", str(days), "--out", out_dir]
           + ([] if grade else ["--no-grade"]))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/data"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    scratch = tempfile.mkdtemp(prefix="mlbedge-pass1-")
    scratch_data = os.path.join(scratch, "data")
    os.makedirs(scratch_data, exist_ok=True)
    real_data, real_shadow, real_store = C.DATA_DIR, G.SHADOW, PR.STORE

    print("pass 1 — simulating, to find out what a sane market looks like…")
    C.DATA_DIR = B.C.DATA_DIR = scratch_data
    G.SHADOW = os.path.join(scratch_data, "shadow.json")
    PR.STORE = os.path.join(scratch_data, "predictions.json")
    _run(os.path.join(scratch, "feed"), days, grade=False)

    fake_api.MARKET_OVERRIDE = _collect_market(os.path.join(scratch, "feed"))
    print(f"  priced {len(fake_api.MARKET_OVERRIDE)} games off the simulation")

    C.DATA_DIR = B.C.DATA_DIR = real_data
    G.SHADOW, PR.STORE = real_shadow, real_store
    fake_api.FINAL_DATES.clear()
    shutil.rmtree(scratch, ignore_errors=True)

    print("pass 2 — building the sample feed against that market…")
    _run(out, days, grade=True)
    print("sample feed written to", out)

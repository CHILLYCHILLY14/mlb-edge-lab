"""Repository-variable regression tests.

The real workflow injects MLB_BANKROLL and MLB_SEASON only for the live build,
so the ordinary pipeline test cannot catch a badly formatted GitHub variable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def read_config(bankroll: str, season: str):
    env = os.environ.copy()
    env.update({"MLB_BANKROLL": bankroll, "MLB_SEASON": season})
    code = (
        "import json; import pipeline.config as c; "
        "print(json.dumps({'bankroll': c.BANKROLL, 'season': c.SEASON}))"
    )
    run = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(run.stdout), run.stderr


def main() -> int:
    friendly, friendly_err = read_config("$1,250.50", "2026")
    assert friendly == {"bankroll": 1250.5, "season": 2026}, friendly
    assert not friendly_err, friendly_err

    fallback, fallback_err = read_config("not-money", "baseball")
    assert fallback == {"bankroll": 250.0, "season": 2026}, fallback
    assert "invalid MLB_BANKROLL" in fallback_err, fallback_err
    assert "invalid MLB_SEASON" in fallback_err, fallback_err

    nonpositive, nonpositive_err = read_config("0", "-1")
    assert nonpositive == {"bankroll": 250.0, "season": 2026}, nonpositive
    assert "invalid MLB_BANKROLL" in nonpositive_err, nonpositive_err
    assert "invalid MLB_SEASON" in nonpositive_err, nonpositive_err

    print("repository settings: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

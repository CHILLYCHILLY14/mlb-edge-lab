"""Small resilient JSON fetcher with on-disk caching."""
from __future__ import annotations
import hashlib, json, os, time, urllib.error, urllib.request

from ..config import HTTP_TIMEOUT, HTTP_RETRIES

UA = "Mozilla/5.0 (compatible; mlb-edge/1.0; +https://github.com/)"
CACHE_DIR = os.environ.get("MLB_CACHE_DIR", ".cache")


def _cache_path(url: str) -> str:
    h = hashlib.sha1(url.encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{h}.json")


def get_json(url: str, cache_hours: float = 0.0, quiet: bool = False):
    """GET a JSON document. Returns None on failure rather than raising.

    cache_hours > 0 serves a fresh-enough local copy instead of hitting the net.
    Offline fixture mode: set MLB_FIXTURES=<dir> and any cached response there
    will be used, which is how the test-suite replays a real slate.
    """
    fixtures = os.environ.get("MLB_FIXTURES")
    if fixtures:
        fp = os.path.join(fixtures, os.path.basename(_cache_path(url)))
        if os.path.exists(fp):
            with open(fp) as fh:
                return json.load(fh)
        if os.environ.get("MLB_FIXTURES_STRICT"):
            return None

    cp = _cache_path(url)
    if cache_hours > 0 and os.path.exists(cp):
        if (time.time() - os.path.getmtime(cp)) < cache_hours * 3600:
            try:
                with open(cp) as fh:
                    return json.load(fh)
            except Exception:
                pass

    last = None
    for attempt in range(HTTP_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cp, "w") as fh:
                json.dump(data, fh)
            return data
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(1.2 * (attempt + 1))
    if not quiet:
        print(f"  ! fetch failed: {url[:110]} ({type(last).__name__})")
    # stale cache is better than nothing
    if os.path.exists(cp):
        try:
            with open(cp) as fh:
                print("    using stale cache")
                return json.load(fh)
        except Exception:
            pass
    return None


def save_fixture(url: str, out_dir: str) -> None:
    """Copy a cached response into a fixtures directory (test tooling)."""
    os.makedirs(out_dir, exist_ok=True)
    cp = _cache_path(url)
    if os.path.exists(cp):
        with open(cp) as a, open(os.path.join(out_dir, os.path.basename(cp)), "w") as b:
            b.write(a.read())

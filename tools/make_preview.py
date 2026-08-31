"""
Bundle the dashboard and a JSON feed into one self-contained HTML file.

    python -m tools.make_preview docs out/preview.html

Useful for sending someone a snapshot, or for hosting the dashboard somewhere
that cannot serve the data directory.
"""
from __future__ import annotations
import json, os, sys


def bundle(docs_dir: str, out_path: str, body_only: bool = False) -> str:
    html = open(os.path.join(docs_dir, "index.html")).read()
    css = open(os.path.join(docs_dir, "styles.css")).read()
    sch = open(os.path.join(docs_dir, "schedule.js")).read()
    simjs = open(os.path.join(docs_dir, "sim.js")).read()
    led = open(os.path.join(docs_dir, "ledger.js")).read()
    js = open(os.path.join(docs_dir, "app.js")).read()

    data_dir = os.path.join(docs_dir, "data")
    embed = {}
    for fn in sorted(os.listdir(data_dir)):
        if fn.endswith(".json"):
            with open(os.path.join(data_dir, fn)) as fh:
                embed[f"data/{fn}"] = json.load(fh)

    body = html.split("<body>", 1)[1].split("</body>", 1)[0]
    body = body.replace('<script src="schedule.js"></script>', "")
    body = body.replace('<script src="sim.js"></script>', "")
    body = body.replace('<script src="ledger.js"></script>', "")
    body = body.replace('<script src="app.js"></script>', "")
    head_title = ("<title>MLB Edge Desk</title>\n"
                  '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
                  'family=IBM+Plex+Mono:wght@400;600&'
                  'family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">')
    blob = json.dumps(embed, separators=(",", ":")).replace("</", "<\\/")
    payload = (f"{head_title}\n<style>\n{css}\n</style>\n{body}\n"
               f"<script>window.__EMBED__={blob};</script>\n"
               f"<script>\n{sch}\n</script>\n<script>\n{simjs}\n</script>\n"
               f"<script>\n{led}\n</script>\n"
               f"<script>\n{js}\n</script>")

    if body_only:
        out = payload
    else:
        out = ("<!doctype html>\n<html lang=\"en\">\n<head>\n"
               "<meta charset=\"utf-8\">\n"
               "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
               "</head>\n<body>\n" + payload + "\n</body>\n</html>")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(out)
    return out_path


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "docs"
    o = sys.argv[2] if len(sys.argv) > 2 else "preview.html"
    body_only = "--body-only" in sys.argv
    print(bundle(d, o, body_only))

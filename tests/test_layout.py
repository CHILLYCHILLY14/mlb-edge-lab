"""
Layout guards for the dashboard.

Loads the built page in headless Chromium at phone and desktop widths and fails
if it goes back to trapping vertical swipes.

    python -m tools.make_sample docs/data
    python -m tools.make_preview docs preview.html
    python -m tests.test_layout preview.html

Optional: needs `pip install playwright && playwright install chromium`. Skips
cleanly when playwright is absent, so it never blocks a build. It is not part of
the CI workflow for that reason - run it after touching the stylesheet.

The bug it exists to prevent: every wide table lived in its own horizontal
scroller, forty-five of them covered most of a phone screen, and a vertical
swipe that started on one went nowhere.
"""
from __future__ import annotations
import os, sys

TABS = ["Slate", "Best Bets", "Matchups", "Simulator", "My Ledger",
        "Power Ratings", "Accuracy", "Model"]
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(name)


def main(path="preview.html"):
    if not os.path.exists(path):
        print(f"skipped - no built page at {path}")
        return 0
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("skipped - playwright not installed (pip install playwright)")
        return 0

    url = "file://" + os.path.abspath(path)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for width in (360, 390, 414):
            print(f"\n[phone {width}px]")
            ctx = b.new_context(viewport={"width": width, "height": 844},
                                is_mobile=True, has_touch=True)
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(url)
            pg.wait_for_timeout(1800)

            for tab in TABS:
                pg.get_by_role("button", name=tab).click()
                pg.wait_for_timeout(250)
                m = pg.evaluate("""() => ({
                  trapped: [...document.querySelectorAll('.scroll')]
                    .filter(n => n.scrollWidth > n.clientWidth + 2).length,
                  sideways: document.body.scrollWidth > document.documentElement.clientWidth + 1,
                  height: document.documentElement.scrollHeight })""")
                check(f"{tab}: nothing scrolls sideways", m["trapped"] == 0,
                      f"{m['trapped']} container(s)")
                check(f"{tab}: page does not scroll sideways", not m["sideways"])
                check(f"{tab}: under 25 screens tall", m["height"] < 844 * 25, f"{m['height']}px")

            pg.get_by_role("button", name="Slate").click()
            pg.wait_for_timeout(250)
            check("game breakdowns start collapsed on a phone",
                  pg.evaluate("""() => document.querySelectorAll('details.gd').length > 0
                    && document.querySelectorAll('details.gd[open]').length === 0"""))
            pg.evaluate("window.scrollTo(0, 1500)")
            pg.wait_for_timeout(150)
            check("the page scrolls vertically", pg.evaluate("window.scrollY") > 1000)
            check("every stacked cell carries its column label", pg.evaluate("""() => {
              const tds = [...document.querySelectorAll('table.rt tbody td')];
              return !tds.length || tds.filter(td => !td.hasAttribute('data-label')
                && !td.hasAttribute('data-primary') && !td.hasAttribute('data-trail')).length === 0;
            }"""))
            check("no script errors", not errors, errors[0] if errors else "")
            ctx.close()

        print("\n[desktop 1280px]")
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.wait_for_timeout(1800)
        check("game breakdowns start open on a desktop",
              pg.evaluate("document.querySelectorAll('details.gd[open]').length") > 0)
        check("page does not scroll sideways", not pg.evaluate(
            "document.body.scrollWidth > document.documentElement.clientWidth + 1"))
        check("no script errors", not errors, errors[0] if errors else "")

        print("\n[date navigation]")
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(url)
        pg.wait_for_timeout(1800)
        viewer_today = pg.evaluate("MLBSchedule.easternDate()")
        opened = pg.inner_text("#curDate")
        dates = pg.evaluate("(S => S)((window.__EMBED__||{})['data/index.json']||{}).dates || []")
        check("opens on the viewer's own Eastern date when it has been built",
              opened == viewer_today or viewer_today not in (dates or []),
              f"opened {opened}, viewer today {viewer_today}")
        pg.click("#prev")
        pg.wait_for_timeout(400)
        pg.click("#prev")
        pg.wait_for_timeout(400)
        back = pg.inner_text("#curDate")
        check("the arrows move backwards", back < opened, f"{back} vs {opened}")
        # The bug: Today walked backwards a day on every press because it asked
        # the build which day it was instead of the viewer's clock.
        seen = []
        for _ in range(4):
            pg.click("#today")
            pg.wait_for_timeout(400)
            seen.append(pg.inner_text("#curDate"))
        check("Today lands on the same day every time", len(set(seen)) == 1, str(seen))
        check("Today does not walk backwards", seen[0] >= back, f"{seen[0]} vs {back}")
        check("the week strip agrees with the Today button",
              pg.evaluate("""() => {
                const on = document.querySelector('.week button.on');
                return !on || on.dataset.date === document.querySelector('#curDate').textContent;
              }"""))

        print("\n[both themes paint their own background]")
        for scheme in ("dark", "light"):
            ctx = b.new_context(viewport={"width": 900, "height": 700}, color_scheme=scheme)
            pg2 = ctx.new_page()
            pg2.goto(url)
            pg2.wait_for_timeout(1200)
            bg = pg2.evaluate("getComputedStyle(document.body).backgroundColor")
            check(f"{scheme}: body has an explicit background",
                  bg not in ("rgba(0, 0, 0, 0)", "transparent"), bg)
            ctx.close()
        b.close()

    print("\n" + "=" * 60)
    if FAILS:
        print(f"{len(FAILS)} FAILURE(S)")
        return 1
    print("all layout checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "preview.html"))

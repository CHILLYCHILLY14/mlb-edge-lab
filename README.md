# MLB Edge Lab

An automatic MLB betting model. It pulls the schedule, probable starters, batting
orders, season rate stats, standings, sportsbook prices and game-time weather;
simulates every game 20,000 times one plate appearance at a time; prices the
moneyline, run line, total and first five; sizes bets with fractional Kelly under
hard portfolio limits; and grades every call it makes — including the ones it
passed on.

It runs itself on GitHub Actions nine times a day, publishes a JSON feed and a
dashboard to GitHub Pages, and embeds into a Wix site with an iframe. No paid
API keys, no server, no monthly bill.

Same stack as `ncaaf-edge` and `nfl-edge`.

---

## What is different from the workbook

The `MLB_Betting_sheet1.xlsx` model computed a win probability from weighted
differences of team OPS, starter FIP and bullpen ERA, then applied a fixed home
adjustment. That is a regression on aggregates. This one plays the game.

| | Workbook | This model |
|---|---|---|
| Win probability | weighted stat differential | 20,000 simulated games |
| Run line | not priced | from the simulated margin distribution |
| Total | market number only | from the simulated run distribution |
| First five | not priced | separate F5 distribution, fair line published |
| Lineups | staging table, manual | confirmed order pulled automatically, projected until posted |
| Starter workload | not modeled | per-pitcher BF/start, third-time-through penalty, bullpen handoff |
| Handedness | not modeled | platoon adjustment per hitter |
| Weather | manual paste, run adjustment | Open-Meteo at the park, wind resolved onto the CF axis, roof logic |
| De-vig | proportional | power method |
| Staking | Kelly on the raw edge | Kelly on a tanh-compressed edge, 5% cap |
| Correlation | none | never the ML and the run line on the same team |
| Slate exposure | none | max 3 best bets, 6 plays, 15% of bankroll per day |
| Grading | manual ledger | entry-price P/L, every call including PASS, closing price kept only for CLV |
| Bet sizing | one bankroll and risk level | change bankroll and ¼, ½ or full Kelly in the dashboard |
| Your own bets | typed into a sheet | tap **+ Ledger** on any LEAN or better; it settles itself |
| Edge readout | a number | a number on a fixed scale with the tier boundaries marked |
| Every game | not tracked | predicted and scored whether or not you bet it |
| Learning | none | bounded corrections learned from its own graded history |
| Horizon | today | a week ahead, labeled by how complete each game's picture is |
| Books | one number | every provider ESPN returns: consensus to grade against, best price to bet |
| Bullpen | season ERA | who is actually available after the last three days of work |
| Form | season only | season plus a rolling 30-day window |
| Platoon | generic constant | each hitter's real numbers against that hand |

The audit sheet in the workbook said it plainly: a 55% win rate with a −24% ROI
over the last twenty bets. That is not a handicapping problem, it is a staking
problem, and the portfolio rules in `pipeline/model/portfolio.py` exist to make
that arithmetic impossible to repeat.

---

## Setup

**Never used GitHub? Read [SETUP.md](SETUP.md) instead** — the same steps
written out click by click, plus updating, troubleshooting and a glossary.

### 1. Create the repo

```bash
gh repo create mlb-edge-lab --public --source . --push
# or: create it on github.com, then
git init && git add -A && git commit -m "initial" && git branch -M main
git remote add origin https://github.com/<you>/mlb-edge-lab.git && git push -u origin main
```

### 2. Turn on Pages

Settings → Pages → **Source: GitHub Actions**.

### 3. Run it once

Actions → *Build MLB slate* → **Run workflow**. It will self-test, build today
and tomorrow, deploy, and commit the feed back to the repo.

The dashboard is then at `https://<you>.github.io/mlb-edge-lab/`.

### 4. Embed in Wix

Wix editor → **Add → Embed → Embed a Site (iframe)** → paste the Pages URL.
Set the element to full width and about 1200px tall; the page also posts its
height to the parent frame (`mlb-edge-height`) if you want to auto-size it with
Velo.

Useful query parameters:

- `?theme=light` / `?theme=dark` — pin the palette instead of following the
  viewer's system setting, which is usually what you want inside a Wix page
- `?tab=bets` — open straight to today's card (`mine`, `matchups`, `accuracy`
  and `ratings` also work)
- `?date=2026-08-21` — open a specific slate

### 5. Optional settings

Settings → Secrets and variables → Actions → **Variables**:

| Variable | Default | What it does |
|---|---|---|
| `MLB_BANKROLL` | `250` | default bankroll used when the dashboard has no saved local choice |
| `MLB_SEASON` | `2026` | season for stats and standings |

The dashboard's **Staking plan** control changes the working bankroll and selects
quarter, half or full Kelly immediately. Those choices stay in that browser.
Stake caps, edge ceilings, tier thresholds, market blend, weather weights and
portfolio limits live in `pipeline/config.py`.

---

## Running it locally

```bash
pip install -r requirements.txt
python -m tests.test_pipeline        # full offline self-test, no network
node tests/test_ledger.mjs           # the dashboard's settlement rules
node tests/test_staking.mjs          # bankroll/Kelly resizing and exposure caps
node tests/test_schedule.mjs         # date resolution and refresh logic
node tests/test_sim.mjs docs/data   # browser engine vs the build engine
python -m tests.test_layout preview.html   # phone layout guards (needs playwright)
python -m pipeline.build             # today
python -m pipeline.build --date 2026-08-21 --days 3
cd docs && python -m http.server 8000   # then open localhost:8000
```

To see the dashboard populated before you have any real data:

```bash
python -m tools.make_sample docs/data     # synthetic slate, ratings and history
python -m tools.make_preview docs preview.html   # one self-contained file
```

---

## How the number is made

**Inputs.** MLB Stats API for schedule, probable starters, confirmed batting
orders, every hitter's and pitcher's season line, and standings. ESPN's public
scoreboard for prices. Open-Meteo for the forecast at the park's coordinates at
first pitch. Park factors, coordinates, roof type and field orientation are a
table in `pipeline/sources/parks.py` you can edit.

**Matchup.** Each player's plate-appearance outcome rates — walk, strikeout,
single, double, triple, home run, out in play — are regressed toward league
average by a prior of 200 PA for hitters and 250 batters faced for starters.
Hitter and pitcher are combined with a multinomial odds-ratio matchup, then
adjusted for handedness, park, weather and home field. Every adjustment is a
multiplier on a specific outcome, with the in-play out bucket absorbing the
remainder, so every vector is still a valid probability distribution.

**Simulation.** `pipeline/model/simulate.py` plays the game: base-out states,
forced advances on walks, runners going first-to-third, sacrifice flies,
double plays, a starter who tires at his own average workload with game-to-game
noise, a third-time-through penalty, a leverage-weighted bullpen composite, the
home team skipping the bottom of the ninth when it is ahead, walk-offs, and the
extra-innings ghost runner. Everything is vectorised over simulations with numpy.

The moneyline, run line, total and first five all come out of the same joint
distribution, so they cannot contradict each other.

**Market.** Prices are de-vigged with the power method — solve for *k* where
`p_a^k + p_b^k = 1` — which takes more margin off the favorite than off the
underdog. On a −250 baseball moneyline that is worth about a point and a half of
implied probability against splitting the margin evenly. The model is then
pulled 55% of the way toward the no-vig price (50% on totals, 40% on F5). The
stronger anchor follows the first 114 head-to-head grades: market Brier 0.201,
model Brier 0.217. The simulator still contributes signal, but less confidence
is claimed until the calibration history is larger.

**Edge and stake.** Raw expected value is squashed through `tanh` toward a
5.5% ceiling and the stake comes from the compressed number. Quarter Kelly is
the default; the dashboard can switch the approved card to half or full Kelly
and resize it to your bankroll. Every choice remains capped at 5% per bet and
15% across the slate. A 22% readout becomes a 5.5% edge and a small bet.

**Tiers.** BEST BET above 3.5% compressed edge *and* passing every lock rule
(price between −175 and +160, a real but not absurd disagreement with the
market, fresh odds, both starters posted, converged simulation, no rainout
risk). GOOD above 2.5%, LEAN above 1.2%, PASS below.

**Portfolio.** Never the moneyline and the run line on the same team. At most
three best bets and six staked plays on a slate. Total exposure capped at 15% of
bankroll per day. If the model disagrees with the market on more than 40% of the
slate, that is flagged as a likely data problem and best bets are capped.

**Grading.** Every call is written to `data/shadow.json`. When it first carries
a stake, its entry price, line, stake, tier and probability are frozen; that
entry number determines P/L when the game goes final. PASSes are included in
accuracy because tier analysis is meaningless if you only grade bets. The
closing price is stored separately and used only for CLV, never for payout.

---

## What is new in this version

**A week of games, not one.** The schedule is published for the whole season, so
the model builds seven days out and labels each game by how much of the picture
has actually arrived:

| State | Means |
|---|---|
| `ready` | priced, both starters posted, lineups confirmed |
| `priced` | priced, both starters posted, lineups still projected |
| `no prices yet` | starters posted, no book has hung a number |
| `starter TBA` | nobody has announced |
| `live` | in progress or final |

Fair lines are published at every stage. Real stakes are only sized for today and
tomorrow — anything further out is a read, not a bet, because the number will
move.

**Every game is scored, bet or not.** `pipeline/predict.py` records the model's
winner, win probability, projected score and projected total for every game on
the schedule, then grades all of them. That gives a straight-up record, a Brier
score, reliability by confidence bucket, run and total error, and a head-to-head
against the market's own opinion on the same games. The bet ledger tells you
whether the model found soft numbers; this tells you whether it reads baseball.

**It learns from that.** Once 150 games are graded, two bounded corrections come
back into the build: a runs-per-game offset if projections have been running high
or low, and a confidence scaling if the model has been too sure or too timid.
Both are capped, both are shown on the Accuracy tab, and both can be switched off
in `config.py`.

**Better inputs.**

- **Bullpen availability.** Recent boxscores are read to find who threw what. An
  arm over 35 pitches yesterday, 55 across two days, or out three days running is
  dropped from the bullpen composite entirely; a merely worked arm is
  downweighted and made slightly worse. A bullpen's season ERA tells you nothing
  about whether its best three arms can pitch tonight.
- **Starter rest.** Days since his last start, with a penalty on short rest.
- **Recent form.** A rolling 30-day window mixed into season rates, so a hitter
  who stopped hitting in June is not still priced on April.
- **Real platoon splits.** Each hitter's actual line against left- and
  right-handed pitching, regressed toward his own overall numbers, instead of one
  league-average constant.
- **Home run regression.** A pitcher's home run rate is pulled 55% toward league
  average — the idea behind xFIP — so a lucky-so-far starter is not priced as an
  ace.
- **Team defense.** Defensive efficiency, the share of balls in play a club turns
  into outs, adjusts hits on contact.

**The simulator runs in the page.** `docs/sim.js` is a port of the build's
Monte Carlo engine, so the **Simulator** tab replays any game in your browser
with the inputs exposed: swap in a sharper starter, pull him three batters
earlier, sit the leadoff man, turn the wind around, dial the park up or down.
Every number is shown as a change from what the build published, with the
sampling noise stated so you can tell a real move from a rounding wobble.

The two engines are held together by `tests/test_sim.mjs`, which replays every
published game through the JavaScript one and fails if it drifts off Python's
numbers — two hand-written implementations of the same game is exactly the
arrangement that goes quietly wrong.

**Two edges, not one.** The Edge column is the model's disagreement with the
market — expected value priced at the no-vig consensus. Beneath it, where they
differ, is what the bet is worth at the best price on offer. Keeping them apart
matters: the gap between consensus and best price is positive on *both* sides of
a game whenever books disagree, so counting it as model edge made every market
look like a play and set the divergence flag off constantly. Bets qualify on the
first number and are sized on the second, and on any two-way market at most one
side can now show an edge — which the test suite asserts.

**It knows what day it is.** The page works out the Eastern date from the
viewer's own clock rather than asking the build, polls for new builds every five
minutes, rolls over by itself at midnight, refreshes when the tab comes back to
the foreground, and says so plainly when the feed is stale or today's slate has
not been built yet.

**Consensus pricing, still keyless.** ESPN's free scoreboard returns several
sportsbooks in one call. The model de-vigs each, takes the median as the market's
real opinion, and grades edges against that — while showing the best price on
offer and which book has it. Grading against the best price instead would
manufacture an edge on every game just by shopping.

**More markets from the same simulation.** First five innings, no-run-first-inning,
team totals and shutout odds all fall out of the same run distribution, so they
cannot contradict the moneyline. ESPN carries none of them, so fair lines are
published for shopping, and real prices pasted into `data/manual_odds.json` get
priced and graded like any other market.

---

## The seven screens

**Slate** — opens with **What to bet**: the actual card with stakes, and beneath
it the numbers the model likes but will not stake, and why. Then the week strip,
then every game as a card with a one-line verdict — BET, LEAN, WATCH, WAIT or
PASS — both starters, bullpen availability, the run distribution, every priced
market, and a paragraph explaining the number.

**Best Bets** — today's card only. What to bet, at what price, for how much.

**Simulator** — the same engine the build runs, with the inputs exposed. Pick a
game, change the starters, the bullpen, the lineups, the park or the weather,
and watch the projection, the win probability, the run line, the first five and
the first inning move. Reachable from the **Simulate** button on any game card.

**Matchups** — the whole slate on one line each: power ranks, true win
percentages, both rotations and bullpens, park, weather, projection, total
versus the market, and the best number on the game. The matchup column stays
pinned while the rest scrolls, so a phone still works.

**My Ledger** — the bets you actually placed. See below.

**Power Ratings** — every roster simulated against one identically-built
reference opponent, both home and away, in a neutral park.

**Accuracy** — three records, kept deliberately apart. **Game predictions**:
every game scored, bet or not, with calibration, run and total error, and the
head-to-head against the market. **Bet calls**: every call the model made,
PASSes included, by tier and by market. **Your bets**: how the tiers performed
for you, at the stakes you actually took.

**Model** — how the number is made, in plain English.

---

## The edge scale

Every edge appears three ways: the compressed percentage, a meter on a fixed
0-to-5.5% scale with ticks at the tier boundaries, and the tier badge itself.
So a `+2.7%` reads as "just past the GOOD line" at a glance instead of needing
the thresholds memorised.

The meter is a single hue, not four tier colors, on purpose: gold and green sit
about six ΔE apart under protanopia, which is not enough to separate on a 7px
bar. The tier is carried by its text badge, which is unambiguous for everyone.

---

## My Ledger

Tap **+ Ledger** next to any LEAN, GOOD or BEST BET — on the Slate, Best Bets or
Matchups tab — and it lands in your ledger at that price, prefilled with the
model's Kelly-sized stake. Tap the stake to change it if you bet a different
number. Tap the button again to remove it. PASS bets have no button; the page
should not invite a bet the model rejected.

Bets settle themselves. Each build publishes `docs/data/results.json` with the
final and first-five scores, and the page grades your entries against it using
the same rules `pipeline/grade.py` grades the model's own calls with — the two
implementations are pinned against each other by `tests/test_ledger.mjs`.

Storage is your browser's, per device, and nothing leaves the page. **Export
JSON** or **Export CSV** writes the whole ledger out so it survives a cleared
browser or moves to another device — where **Import** merges it back without
duplicating anything you already have. Some embedded viewers block downloads, so
the export panel always shows the text to copy as well.

This is kept separate from the model's shadow book on purpose. The shadow book
answers "is the model any good". Your ledger answers "am I any good at using
it". Answering both with one number hides which is which the first time a month
goes badly.

---

## Layout

```
pipeline/
  config.py            every tunable knob
  build.py             orchestrator
  grade.py             shadow book: record, settle, summarise
  predict.py           game predictions, scoring, and the learned corrections
  sources/
    http.py            cached JSON fetcher with retries and fixture replay
    mlb_api.py         schedule, rosters, stats, standings, final scores
    espn.py            moneyline, run line, total
    weather.py         Open-Meteo -> run environment multiplier
    parks.py           park factors, coordinates, roofs, field orientation
  model/
    rates.py           league baselines, shrinkage, log5, park/weather/platoon
    teams.py           lineups, starters, bullpen composite, matrices
    simulate.py        the Monte Carlo engine
    market.py          odds math, de-vig, compression, Kelly, tiers
    price.py           priced bets per game
    portfolio.py       slate-level correlation and exposure rules
docs/                  the dashboard (this is what Pages serves)
  index.html           shell
  styles.css           tokens and components, light and dark
  schedule.js          which day it is and which slate to open - testable in node
  sim.js               the game simulator, ported from the build engine
  ledger.js            your ledger: storage, settlement, export - testable in node
  app.js               everything else
data/                  shadow book + optional manual F5 odds
tests/                 offline end-to-end self-test (python) + ledger tests (node)
tools/                 sample-feed generator and single-file bundler
```

---

## First five innings

ESPN does not publish F5 prices. The model simulates the first five anyway and
publishes a fair line for both sides and the total, so you can shop it at your
book. If you want F5 graded as a real bet with an edge and a stake, paste the
prices into `data/manual_odds.json` keyed by `gamePk` and the model treats them
like any other market. Either way the F5 call is recorded and graded.

## What it does not know

Bullpen availability after yesterday's usage. Injuries that have not yet hit a
lineup card. Catcher framing. Umpire strike zones. A starter pitching hurt.
Travel and getaway days. Treat the output as one opinion with a spreadsheet
behind it, not a verdict.

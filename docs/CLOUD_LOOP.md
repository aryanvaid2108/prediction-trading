# Cloud paper-trading loop — setup

The loop runs `scripts.run_daily` once a day in a cloud sandbox: it settles
yesterday's positions against the official CLI high, records today's plan
(KNYC + KMDW + KAUS, $1000 bankroll), and commits the updated ledger back to git
so state persists between runs. **No live orders are ever placed.**

## What only you can provide

1. **A git remote.** The cloud sandbox clones the repo and pushes the ledger back,
   so it needs a remote with push access. From the repo root:

   ```bash
   # Option A — GitHub CLI (install once: brew install gh)
   gh auth login
   gh repo create kalshi-weather --private --source=. --remote=origin --push

   # Option B — create an empty private repo on github.com, then:
   git remote add origin git@github.com:<you>/kalshi-weather.git
   git push -u origin main
   ```

   Tip: run these from the Claude prompt by prefixing with `!` so the interactive
   auth happens in this session.

2. **Push credentials for the cloud agent** — a GitHub token/deploy key the routine
   can use to `git push` the updated ledger. (The scheduling step will ask for this.)

## The job each run does (one intraday "tick")

```bash
cd kalshi-weather
git pull --rebase --autostash
python -m scripts.run_daily KNYC KMDW KAUS      # settle finished days, then
                                                # record NEW edge if in-window
git add .cache/paper_ledger.json
git commit -m "paper tick $(date -u +%F' '%H:%MZ)" || true
git push
```

Each run is one stateless tick: it settles any finished days against the CLI,
then (only between 10:00–15:59 ET) re-quotes with the freshest observations and
records any **new** bucket the edge now favours. It will not re-enter a bucket it
already holds, and it caps cumulative daily stake per station at 25% — so running
it several times a day captures the intraday sharpening without over-betting.

Runtime deps (light — the daily path does NOT need eccodes/GRIB):
`pip install numpy scipy pandas requests`

## Schedule — several ticks across the afternoon

Run the job **3× a day** to ride the intraday sharpening (each is one routine
invocation; mind the per-account daily run cap):

| Tick | UTC | ET | Why |
|------|-----|----|-----|
| morning | 14:30 | 10:30am | first in-window read; yesterday's CLI is out |
| midday  | 17:00 | 1:00pm  | distribution tightening |
| late    | 19:00 | 3:00pm  | sharpest — near the intraday edge peak |

### Always-on alternative

On a machine that stays awake (small VM / Pi), a single process can poll the whole
window instead of cron:

```bash
python -m scripts.run_daily watch 30 KNYC KMDW KAUS   # tick every 30 min, 10:00–16:00 ET
```

## Reviewing results

```bash
python -m scripts.paper_trade status     # positions / win-rate / realized P&L / ROI
```

Let the loop run for several weeks. The number that matters is **realized ROI net
of fees** — backtest CRPS is forecast skill, not trading profit.

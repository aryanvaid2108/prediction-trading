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

## The daily job the routine runs

```bash
cd kalshi-weather
git pull --rebase --autostash
python -m scripts.run_daily KNYC KMDW KAUS      # settle -> record -> report
git add .cache/paper_ledger.json
git commit -m "paper loop $(date -u +%F)" || true
git push
```

Runtime deps (light — the daily path does NOT need eccodes/GRIB):
`pip install numpy scipy pandas requests`

## Schedule

Daily at **18:30 UTC (~2:30pm ET)** — mid-afternoon local at all three stations,
so `quote_live` is intraday-active (sharp) and yesterday's CLI is already out.

## Reviewing results

```bash
python -m scripts.paper_trade status     # positions / win-rate / realized P&L / ROI
```

Let the loop run for several weeks. The number that matters is **realized ROI net
of fees** — backtest CRPS is forecast skill, not trading profit.

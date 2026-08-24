# prediction-trading — kalshi-weather

**📈 [Live paper-trading dashboard →](DASHBOARD.md)** (auto-updated every tick by the loop)


A calibrated daily-high-temperature model for Kalshi weather markets. It turns
multi-model weather forecasts into a probability distribution over the settling
NWS daily high, then into per-degree bucket probabilities you can compare to
posted Kalshi prices.

The edge in these markets is **calibration + settlement precision**, not exotic
ML — so this implements the method the forecasting literature actually favors:
EMOS / non-homogeneous Gaussian regression fit by minimizing CRPS on a trailing
window.

## What settles the market (get this exactly right first)

- Daily high/low markets settle on the **NWS Daily Climate Report (CLI)** max/min
  for a named station, read in **whole °F** — *not* the raw METAR feed. The CLI
  is QC'd and issued the next morning; METAR is only a cross-check that can delay
  settlement.
- The CLI observation day runs on **Local Standard Time year-round**. During DST
  the window is `1:00 AM → 12:59 AM` on the wall clock. `wx/settlement.py` bins
  observations by a fixed standard-time offset so a late-evening or pre-dawn
  extreme lands on the correct CLI day.
- **Hourly** temp markets are a different product (settle off The Weather
  Company) — not handled here.
- Settlement rules are verified in depth only for **NHIGH (Central Park)**. Each
  other station's contract PDF must be confirmed before trading real capital
  (`wx/stations.py` has the current best-known station/offset/wfo map).

## Pipeline

```
IEM ASOS obs ─► settlement.daily_high (LST window) ─► realized high  ┐
                                                                     ├─► EMOS.fit (CRPS, trailing window)
multi-model archive forecasts ─► ensemble_features (mean, spread) ───┘
                                                                     ▼
                              live multi-model forecast ─► EMOS.predict ─► bucket probabilities
```

- `wx/obs.py` — IEM ASOS hourly observations (free, no auth).
- `wx/forecast.py` — Open-Meteo clients: live 82-member GEFS+ECMWF ensemble,
  multi-model **historical archive** (backtest), and matching live multi-model
  forecast.
- `wx/emos.py` — closed-form Gaussian CRPS, EMOS fit, predictive distribution,
  per-degree `prob_bucket` / `prob_ge` (integrates over ±0.5°F to match rounding).
- `wx/backtest.py` — walk-forward scoring (CRPS raw vs EMOS, PIT calibration).
- `wx/intraday.py` — intraday sharpening: conditions the daily-high distribution
  on the max observed so far today plus a climatological residual-to-peak.
- `wx/gefs.py` — GEFSv12 reforecast ingester (byte-range GRIB2 from NOAA S3,
  decoded to a point, cached) for a real ensemble spread.
- `wx/emos.py::MixedEMOS` — multi-predictor NGR: learns per-model mean weights
  (and takes multiple spread predictors), shrinking toward the equal-weight blend.
- `wx/trading.py` — decision engine: fee model, per-bucket edge, fractional
  Kelly, σ floor + exposure cap. Pure and unit-tested.
- `wx/kalshi.py` — Kalshi market-data client (public reads) + a DRY-RUN order
  path (live placement needs your own keys and an explicit flag).
- `wx/pipeline.py` — `quote()` (forecast prior) and `quote_live()` which blends
  the prior with today's observed temperatures (precision-weighted, floored at the
  observed max) and applies a data-driven σ calibration.
- `wx/cli.py` — official NWS CLI high/low (the settlement truth) + basis-risk check.
- `wx/paper.py` — paper-trading ledger: record dry-run positions, settle against
  the CLI high, report realized P&L.

## Run

```bash
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/python -m scripts.backtest_demo KNYC 2025-03-01 2025-08-15
./.venv/bin/python -m scripts.live_demo KNYC
```

## Validated results (walk-forward, Mar–Aug 2025)

| Station | CRPS improvement vs raw | MAE (°F) | within 1°F | PIT std (0.29 = ideal) |
|---------|-------------------------|----------|-----------|------------------------|
| KMDW    | 25.5%                   | 1.08     | 78%       | 0.284                  |
| KNYC    | 15.0%                   | 1.24     | 66%       | 0.326                  |
| KDEN    | 10.1%                   | 1.26     | 66%       | 0.295                  |

EMOS reliably beats the raw multi-model ensemble and is well-dispersed.

### Mixed EMOS — learned per-model weights (`scripts.mixed_demo`, Mar–Aug 2025)

Instead of averaging the 6 models then bias-correcting, `MixedEMOS` learns each
model's weight (some models are simply better at a microclimate station), the way
MOS/NBM do. Walk-forward, out-of-sample, vs single-predictor EMOS:

| Station | single EMOS CRPS | Mixed EMOS CRPS | Mixed gain | MAE |
|---------|------------------|-----------------|-----------|-----|
| KAUS    | 0.883            | 0.692           | **+21.6%** | 1.18 → 0.94 |
| KDEN    | 0.939            | 0.846           | **+9.9%**  | 1.28 → 1.15 |
| KMDW    | 0.745            | 0.681           | **+8.6%**  | 1.04 → 0.94 |
| KNYC    | 0.875            | 0.820           | **+6.3%**  | 1.23 → 1.15 |

Matches/beats the literature's "+8–12% Mixed EMOS." Ridge (shrinkage toward the
equal blend) defaults to a conservative 1.0; ~0.3 squeezes a few % more with no
out-of-sample overfit cliff. The framework is general — a second ensemble's
mean/spread drops in as extra predictor columns.

### Trading layer (`scripts.trade_demo`, DRY-RUN)

Ties the model to live Kalshi prices: for each bucket it compares the model
probability to the quoted price, computes the net-of-fee edge, sizes with
fractional Kelly, and prints a dry-run order plan (no orders are ever placed).
Fee model is the verified `ceil(0.07·n·P·(1−P))` dome; risk controls are a σ
floor and an aggregate exposure cap.

**Important caveat the demo makes obvious:** run against a *mid-afternoon* market
with a *morning* forecast, it reports absurd edges (e.g. 50–80% on stake). That is
not alpha — it is the model being stale/overconfident while the market has already
watched the temperature climb. The engine is correct; it must be fed (a) the
intraday-conditioned distribution and (b) a properly calibrated σ before any edge
is real. Treat the raw plan as a miscalibration detector, not a trade list.

### Intraday-conditioned live quote (`scripts.intraday_live_demo`, KNYC 2025-07-15)

`quote_live` blends the forecast prior with the temperatures already observed
today and floors at the observed max. Simulated through a real day (true high 86°F):

| Time (LST) | obs max | predictive | P(=86) | MAE |
|-----------|---------|------------|--------|-----|
| 07:00 | 74 | N(83.2, 1.56) | 5.6% | 2.8 |
| 13:00 | 84 | N(84.2, 0.93) | 10.3% | 1.8 |
| 15:00 | 86 | N(86.0, 0.30) | 95.7% | 0.0 |

By mid-afternoon the predictive nails the exact degree while a morning-only
forecast is still ±1.5°F — that gap, when the Kalshi book still tracks the morning
number, is the tradable edge. Stale buckets (e.g. ≤77°) collapse to 0 once the
observed max passes them.

### Settlement basis risk (`scripts.cli_check`, KNYC May–Aug 2025)

The hourly-ASOS reconstruction matches the official CLI high **exactly only 34%**
of the time and runs a systematic **−0.74°F** cold (CLI catches 1-minute peaks the
hourly METARs miss). Fix: training and backtests now target the **official CLI
high** (`wx/cli.py`), which moved PIT mean from ~0.45 to **0.50** — the model is
now centered on the value that actually settles. Near a bucket boundary, still
discount the edge by the residual ~8% mismatch rate.

### Paper trading (`scripts.paper_trade`)

`record` logs today's dry-run plan (as maker orders where the book allows),
`settle` marks closed positions to the official CLI high, `status` reports realized
P&L / win-rate / ROI. This is how to forward-test real-money outcomes before going
live — no orders are ever placed.

### Intraday sharpening (`scripts.intraday_demo`, KNYC summer 2025)

Daily-high CRPS as the day fills in — this is where the market is least efficient:

| Cutoff (LST) | intraday CRPS | naive floor | note |
|--------------|---------------|-------------|------|
| 08:00 | 1.72 | 7.03 | worse than morning EMOS (~0.9) |
| 12:00 | **0.71** | 0.96 | already beats morning EMOS |
| 14:00 | **0.23** | 0.25 | ~4× sharper than morning |
| 16:00 | 0.01 | 0.01 | essentially resolved |

By early-to-mid afternoon the high is nearly certain while the Kalshi book is
still anchored to the morning forecast. **Trade window ≈ 12–3 PM local.**

### GEFS reforecast (`scripts.gefs_backtest`, KNYC 2019, 31 days ingested)

The ingester works (real 5-member spread, e.g. σ≈1.8°F) and makes the EMOS
variance slope informative (**d ≈ 0.47, vs ~0 for the 6-model proxy**). But
standalone 5-member GEFS *underperforms* the multi-model proxy for next-day point
highs: the 0.25° grid point carries a large microclimate bias (raw MAE ≈ 3.9°F at
Central Park), and 5 members give only a coarse spread. **Conclusion: use GEFS as
an added predictor in a Mixed EMOS alongside the multi-model mean (research: +8–12%),
not as a replacement** — or accumulate the full 31-member operational GEFS.

## Known limitations / next steps

1. **DONE — intraday sharpening** (`wx/intraday.py`). Biggest real edge; see table
   above. Next: fold live METAR-so-far into `live_demo` to price the afternoon.
2. **DONE — GEFS reforecast ingester** (`wx/gefs.py`). Spread is now informative
   (d≈0.47) but standalone GEFS underperforms the proxy at microclimate stations.
3. **DONE — Mixed EMOS** (`wx/emos.py::MixedEMOS`, `scripts.mixed_demo`). Learns
   per-model weights; +6–22% CRPS over single EMOS out-of-sample. A same-period
   second ensemble (operational GEFS via S3, or accumulated live snapshots) drops
   in as extra columns to push further.
4. **DONE — trading layer** (`wx/trading.py`, `wx/kalshi.py`, `scripts.trade_demo`):
   fee-aware edge, fractional Kelly, exposure cap, dry-run orders vs live prices.
5. **DONE — intraday-conditioned live quote** (`quote_live`): the key fix; kills
   stale-forecast edges by folding in observed temperature.
6. **DONE — σ calibration** (`backtest.calibration_factor`): data-driven inflation
   replacing the blunt floor.
7. **DONE — settlement truth** (`wx/cli.py`): train/settle on the official CLI high,
   removing a systematic −0.74°F bias.
8. **DONE — paper trading** (`wx/paper.py`, `scripts.paper_trade`): forward-test P&L
   against CLI settlement.

**Still required before real money:** (a) **run `paper_trade` forward for weeks**
and confirm positive realized ROI net of fees — nothing here proves live P&L yet;
(b) **live maker-order placement + fill tracking** (signing scaffold exists in
`kalshi.place_order`, needs your keys + testing); (c) confirm each non-NYC station's
contract PDF (settlement station, rounding); (d) a **licensed data feed** (Open-Meteo
free tier is non-commercial); (e) risk limits per event and a kill-switch.
4. **Settlement approximation.** Realized highs are reconstructed from hourly
   METAR; the official CLI can differ ~1°F. Cross-check against the actual CLI
   text product before trusting boundary cases.
5. **Data licensing.** Open-Meteo's free tier is non-commercial — fine for
   research/backtest; a licensed feed is needed for live real-money trading.

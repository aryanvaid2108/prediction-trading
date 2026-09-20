# 📈 Live strategy and paper arms

## What the live bot is doing right now

_Generated from the settings the live loop runs with._

- Trades Kalshi daily high-temperature markets for 8 cities: Central Park, NY, Chicago Midway, IL, San Francisco, CA, Denver, CO, Houston Hobby, TX, Dallas-Fort Worth, TX, Minneapolis, MN, New Orleans, LA.
- Looks for trades at 11:00, 13:00, 15:00 ET, and may place them for 75 minutes after each.
- Bankroll $150. A day that loses more than $40 halts trading until you resume it by hand.
- At most one position per city per day, and no more than 25% of bankroll ($37.50) on any city.
- The model's probability is blended 50% model / 50% market price before deciding.
- Buys only when that blended probability beats the ask by at least 5 points or 35% of the ask, whichever is larger, after fees.
- Never buys below 15¢, and never when the model says the market is wrong by more than 2.5×.
- Rejects a trade that would stop being profitable if the forecast were off by 1°F in either direction or toward the market's own view.
- Sizes at 0.25× Kelly. Orders are immediate-or-cancel at the live order book's ask, capped at what is actually resting there.

**Changes to the live rules**

- `2026-09-06` Real money now trades 8 cities (NYC, Chicago, Dallas, Houston, New Orleans, Minneapolis, Denver, San Francisco): the ones positive under BOTH backtest bounds. The archive forecast is fresher than what a live tick has, so a previous-day-runs bound was added; Austin, LA, Philadelphia, Atlanta, Las Vegas, OKC, Phoenix, Seattle are paper-only until the live model-vs-market score says otherwise.
- `2026-09-06` Daily loss kill-switch raised from $15 to $40: the $15 cap was sized for one trade a day and 16 cities make two to three.
- `2026-09-05` Nine cities added (Houston, Atlanta, Dallas, Las Vegas, Minneapolis, New Orleans, Oklahoma City, Phoenix, Seattle): each passed the live-rules backtest with real-volume fill caps and matched Kalshi's settlement 41 of 41 days. Daily budget now goes to the best-EV trades first.
- `2026-09-04` Forecast-error check loosened from 1.5°F to 1.0°F: it was blocking most of the backtest's profit, and the looser check held up under real hourly volume caps.
- `2026-09-04` Trades only inside three tick slots (11:00, 13:00, 15:00 ET); orders re-priced on the live order book and capped at resting depth before sending.
- `2026-08-27` 15¢ price floor, 2.5x disagreement cap, 50/50 blend with the market price, $15 daily loss kill-switch — after the Aug 25-26 losses.
- `2026-08-25` Live with real money at a $150 canary bankroll.

## Paper arms

_Updated 2026-09-20 17:29 UTC · bankroll $150 per arm · stations KNYC · KMDW · KAUS · KLAX · KSFO · KDEN · KPHL · KHOU · KATL · KDFW · KLAS · KMSP · KMSY · KOKC · KPHX · KSEA · fills at the live book's touch, depth-capped · no live orders placed_

| Arm | Differs from control | Realized P&L | ROI (on stake) | Win rate | Closed | Open |
|:--|:--|---:|---:|---:|---:|---:|
| **control** | live config — Exactly the live rules. Every other arm is judged against this one. | **$-56.11** | -15.3% | 24% | 54 | 3 |
| **no_gate** | robust_delta=0.0 — No forecast-error check at all. Backtest's best result; takes ~3 trades a day. | **$-114.33** | -18.8% | 21% | 92 | 3 |
| **gate_15** | robust_delta=1.5 — The stricter 1.5°F check that was live until Sep 4. | **$-30.92** | -12.4% | 24% | 38 | 2 |
| **model_w1** | model_weight=1.0 — Trusts the model fully, no blending with the market price. | **$-139.83** | -19.5% | 31% | 84 | 4 |
| **model_w025** | model_weight=0.25 — Leans 75% on the market price. Fewest trades, smallest drawdown in backtest. | **$+3.54** | +8.8% | 20% | 10 | 1 |
| **early** | ticks=(15, 17) — Enters only at the 11:00 and 13:00 ET ticks, never the afternoon. | **$+44.50** | +18.1% | 35% | 34 | 3 |
| **w15_025** | w15=0.25 — Trusts the model only 25% at the 11:00 ET tick, where it leans on forecasts alone; 50% later. The only 11:00 setting positive under both backtest bounds. | **$-51.93** | -18.0% | 20% | 41 | 2 |

## Control arm

### By station

| Station | Positions | Open | Closed | Realized P&L |
|:--|--:|--:|--:|--:|
| KATL | 7 | 0 | 7 | $+68.84 |
| KAUS | 2 | 0 | 2 | $-9.85 |
| KDEN | 2 | 0 | 2 | $+5.74 |
| KHOU | 4 | 0 | 4 | $-19.20 |
| KLAS | 5 | 0 | 5 | $-20.05 |
| KLAX | 6 | 1 | 5 | $+3.55 |
| KMDW | 4 | 0 | 4 | $-0.93 |
| KMSP | 5 | 1 | 4 | $+6.54 |
| KMSY | 1 | 0 | 1 | $-0.92 |
| KNYC | 4 | 0 | 4 | $-22.08 |
| KOKC | 5 | 0 | 5 | $-7.20 |
| KPHL | 1 | 0 | 1 | $-15.55 |
| KPHX | 3 | 1 | 2 | $-14.27 |
| KSEA | 3 | 0 | 3 | $+7.97 |
| KSFO | 5 | 0 | 5 | $-38.70 |

### Cumulative realized P&L

```mermaid
xychart-beta
  x-axis ["09-05", "09-06", "09-07", "09-08", "09-09", "09-10", "09-11", "09-12", "09-13", "09-14", "09-15", "09-16", "09-17", "09-18", "09-19"]
  y-axis "USD"
  line [-12.66, -60.03, -54.88, -66.03, -30.54, -66.82, -51.90, -60.13, -90.16, -61.28, -92.62, -77.76, -108.45, -97.57, -56.11]
```

### Open positions

| Station | Settles | Bucket | Side | Price | Qty |
|:--|:--|:--|:--|--:|--:|
| KLAX | 2026-09-20 | ≤75° | YES | $0.20 | 45 |
| KMSP | 2026-09-20 | 67–68° | YES | $0.32 | 33 |
| KPHX | 2026-09-20 | 101–102° | NO | $0.22 | 26 |

### Recently settled

| Station | Day | Bucket | Side | Settled high | P&L |
|:--|:--|:--|:--|--:|--:|
| KMSP | 2026-09-14 | ≤63° | NO | 61° | $-3.66 |
| KSEA | 2026-09-14 | 68–69° | NO | 67° | $+16.87 |
| KMDW | 2026-09-15 | 86–87° | YES | 85° | $-9.94 |
| KOKC | 2026-09-15 | 101–102° | NO | 101° | $-5.27 |
| KHOU | 2026-09-15 | 95–96° | NO | 96° | $-1.15 |
| KLAS | 2026-09-15 | 98–99° | NO | 98° | $-10.98 |
| KMSP | 2026-09-15 | 70–71° | YES | 72° | $-2.79 |
| KPHX | 2026-09-15 | 99–100° | YES | 102° | $-1.21 |
| KLAS | 2026-09-16 | 96–97° | NO | 98° | $+15.70 |
| KATL | 2026-09-16 | ≤88° | YES | 89° | $-0.48 |
| KSEA | 2026-09-16 | 72–73° | YES | 74° | $-0.36 |
| KLAS | 2026-09-17 | 94–95° | NO | 94° | $-9.09 |
| KPHX | 2026-09-17 | 95–96° | YES | 90° | $-13.06 |
| KSEA | 2026-09-17 | 76–77° | NO | 77° | $-8.54 |
| KLAX | 2026-09-18 | 78–79° | NO | 77° | $+15.88 |
| KNYC | 2026-09-18 | ≤79° | YES | 80° | $-5.00 |
| KLAX | 2026-09-19 | ≤77° | YES | 77° | $+4.29 |
| KATL | 2026-09-19 | ≤91° | NO | 92° | $+50.46 |
| KNYC | 2026-09-19 | 69–70° | NO | 69° | $-7.23 |
| KHOU | 2026-09-19 | 92–93° | NO | 93° | $-6.06 |

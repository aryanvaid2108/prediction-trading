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

_Updated 2026-09-09 15:33 UTC · bankroll $150 per arm · stations KNYC · KMDW · KAUS · KLAX · KSFO · KDEN · KPHL · KHOU · KATL · KDFW · KLAS · KMSP · KMSY · KOKC · KPHX · KSEA · fills at the live book's touch, depth-capped · no live orders placed_

| Arm | Differs from control | Realized P&L | ROI (on stake) | Win rate | Closed | Open |
|:--|:--|---:|---:|---:|---:|---:|
| **control** | live config — Exactly the live rules. Every other arm is judged against this one. | **$-66.03** | -93.4% | 17% | 12 | 1 |
| **no_gate** | robust_delta=0.0 — No forecast-error check at all. Backtest's best result; takes ~3 trades a day. | **$-92.70** | -57.2% | 9% | 22 | 2 |
| **gate_15** | robust_delta=1.5 — The stricter 1.5°F check that was live until Sep 4. | **$-50.01** | -91.9% | 11% | 9 | 1 |
| **model_w1** | model_weight=1.0 — Trusts the model fully, no blending with the market price. | **$-66.45** | -41.2% | 30% | 20 | 2 |
| **model_w025** | model_weight=0.25 — Leans 75% on the market price. Fewest trades, smallest drawdown in backtest. | **$-6.58** | -106.0% | 0% | 2 | 0 |
| **early** | ticks=(15, 17) — Enters only at the 11:00 and 13:00 ET ticks, never the afternoon. | **$-48.79** | -90.2% | 22% | 9 | 1 |
| **w15_025** | w15=0.25 — Trusts the model only 25% at the 11:00 ET tick, where it leans on forecasts alone; 50% later. The only 11:00 setting positive under both backtest bounds. | **$-10.09** | -61.9% | 33% | 3 | 0 |

## Control arm

### By station

| Station | Positions | Open | Closed | Realized P&L |
|:--|--:|--:|--:|--:|
| KATL | 2 | 1 | 1 | $-4.26 |
| KHOU | 1 | 0 | 1 | $+0.65 |
| KLAS | 1 | 0 | 1 | $-15.37 |
| KLAX | 2 | 0 | 2 | $-10.31 |
| KMDW | 2 | 0 | 2 | $-18.24 |
| KMSY | 1 | 0 | 1 | $-0.92 |
| KNYC | 1 | 0 | 1 | $+5.15 |
| KPHL | 1 | 0 | 1 | $-15.55 |
| KSFO | 2 | 0 | 2 | $-7.18 |

### Cumulative realized P&L

```mermaid
xychart-beta
  x-axis ["09-05", "09-06", "09-07", "09-08"]
  y-axis "USD"
  line [-12.66, -60.03, -54.88, -66.03]
```

### Open positions

| Station | Settles | Bucket | Side | Price | Qty |
|:--|:--|:--|:--|--:|--:|
| KATL | 2026-09-09 | 90–91° | NO | $0.34 | 12 |

### Recently settled

| Station | Day | Bucket | Side | Settled high | P&L |
|:--|:--|:--|:--|--:|--:|
| KMDW | 2026-09-05 | ≤82° | YES | 83° | $-11.74 |
| KMSY | 2026-09-05 | 90–91° | NO | 90° | $-0.92 |
| KMDW | 2026-09-06 | 79–80° | NO | 79° | $-6.50 |
| KLAX | 2026-09-06 | 80–81° | YES | 76° | $-9.83 |
| KHOU | 2026-09-06 | 90–91° | YES | 91° | $+0.65 |
| KLAS | 2026-09-06 | 84–85° | NO | 84° | $-15.37 |
| KSFO | 2026-09-06 | 69–70° | YES | 72° | $-0.77 |
| KPHL | 2026-09-06 | 83–84° | NO | 83° | $-15.55 |
| KNYC | 2026-09-07 | 79–80° | NO | 78° | $+5.15 |
| KLAX | 2026-09-08 | ≥90° | NO | 92° | $-0.48 |
| KSFO | 2026-09-08 | ≥86° | NO | 87° | $-6.41 |
| KATL | 2026-09-08 | 88–89° | NO | 88° | $-4.26 |

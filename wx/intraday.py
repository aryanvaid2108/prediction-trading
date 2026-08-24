"""Intraday sharpening: condition the daily-high distribution on what's already
been observed today.

The final CLI high can never fall below the max already observed, so partway
through the day the distribution collapses toward that floor. We model the
remaining upside as a climatological residual R = final_high - (max observed
through hour h), fit from history for the same station/season/hour, and predict
final_high = observed_max_so_far + R. Kalshi's book is slow to reprice this.
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

from .settlement import lst_day


def prep(obs: pd.DataFrame, std_utc_offset: int, hours) -> pd.DataFrame:
    """Per LST day: the final high and the running max through each cutoff hour."""
    df = obs.dropna(subset=["tmpf"]).copy()
    local = df["valid"] + pd.to_timedelta(std_utc_offset, unit="h")
    df["day"] = lst_day(df["valid"], std_utc_offset)
    df["hour"] = local.dt.hour
    out = df.groupby("day")["tmpf"].max().rename("final").to_frame()
    for h in hours:
        rm = df[df["hour"] <= h].groupby("day")["tmpf"].max().rename(f"rm_{h}")
        out = out.join(rm)
    return out.reset_index()


class IntradayModel:
    """Nonparametric predictive high from a floor plus historical residuals."""

    def __init__(self, residuals, bandwidth: float = None, bw_min: float = 0.4):
        self.r = np.clip(np.asarray(residuals, float), 0, None)  # final >= observed max
        # Adaptive kernel: wide when upside is uncertain (morning), narrowing to a
        # floor near settlement. The floor also stands in for CLI-vs-METAR noise.
        self.bw = bandwidth if bandwidth is not None else max(bw_min, 0.9 * self.r.std())

    def samples(self, observed_max: float) -> np.ndarray:
        return observed_max + self.r

    def crps(self, observed_max: float, y: float) -> float:
        from .emos import crps_samples
        return crps_samples(self.samples(observed_max), y)

    def prob_bucket(self, observed_max: float, lo: int, hi: int) -> float:
        s = self.samples(observed_max)
        hi_cdf = norm.cdf((hi + 0.5 - s) / self.bw).mean()
        lo_cdf = norm.cdf((lo - 0.5 - s) / self.bw).mean()
        return float(hi_cdf - lo_cdf)

    def prob_ge(self, observed_max: float, t: float) -> float:
        return float((1 - norm.cdf((t - self.samples(observed_max)) / self.bw)).mean())


def residuals(table: pd.DataFrame, hour: int) -> np.ndarray:
    col = f"rm_{hour}"
    d = table.dropna(subset=["final", col])
    return (d["final"] - d[col]).to_numpy()


def backtest_by_hour(obs: pd.DataFrame, std_utc_offset: int, hours,
                     min_train: int = 30, window: int = 45) -> pd.DataFrame:
    """Walk-forward CRPS of the intraday model at each cutoff hour, plus the
    naive 'high = observed max so far' baseline, to show upside as the day fills in.
    """
    tab = prep(obs, std_utc_offset, hours).dropna(subset=["final"]).sort_values("day").reset_index(drop=True)
    rows = []
    for h in hours:
        col = f"rm_{h}"
        sub = tab.dropna(subset=[col]).reset_index(drop=True)
        crps_model, crps_naive = [], []
        for i in range(min_train, len(sub)):
            train = sub.iloc[max(0, i - window):i]
            cur = sub.iloc[i]
            m = IntradayModel((train["final"] - train[col]).to_numpy())
            crps_model.append(m.crps(cur[col], cur["final"]))
            crps_naive.append(abs(cur["final"] - cur[col]))
        rows.append({
            "hour_lst": h,
            "n": len(crps_model),
            "crps_intraday": round(float(np.mean(crps_model)), 3),
            "crps_naive_floor": round(float(np.mean(crps_naive)), 3),
            "mean_residual": round(float((sub["final"] - sub[col]).mean()), 2),
        })
    return pd.DataFrame(rows)

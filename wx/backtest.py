from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import cli, emos, obs, settlement
from .forecast import (ensemble_features, fetch_members, fetch_members_archive,
                       member_daily_highs)
from .stations import Station


def realized_high(st: Station, start: date, end: date) -> pd.Series:
    """Authoritative daily high (day -> high): official NWS CLI where available,
    falling back to the ASOS reconstruction. CLI is the settlement truth and runs
    ~0.7F above the hourly-METAR reconstruction, so training on it removes a
    systematic cold bias."""
    o = obs.fetch_asos(st.iem_id, start - timedelta(days=1), end + timedelta(days=2))
    r = settlement.daily_high(o, st.std_utc_offset)
    recon = pd.Series(r["high"].values, index=pd.to_datetime(r["day"]))
    try:
        official = cli.settlement_high(st.icao, start, end)
    except Exception:
        official = pd.Series(dtype=float)
    return official.combine_first(recon)


def build_archive_table_wide(st: Station, start: date, end: date):
    """Per-model daily highs (one column each) + ensemble mean/spread + realized.

    Returns (table, model_cols) so a multi-predictor model can learn per-model
    weights instead of collapsing to an equal-weight mean.
    """
    members = fetch_members_archive(st.lat, st.lon, start, end)
    mh = member_daily_highs(members, st.std_utc_offset)
    wide = mh.pivot(index="day", columns="member", values="high")
    model_cols = list(wide.columns)
    feats = ensemble_features(members, st.std_utc_offset).set_index("day")
    wide.index = pd.to_datetime(wide.index)
    feats.index = pd.to_datetime(feats.index)
    realized = realized_high(st, start, end)
    t = wide.join(feats[["ens_mean", "ens_std"]]).join(realized.rename("high"))
    t = t.dropna(subset=model_cols + ["high"]).reset_index(names="day")
    return t.sort_values("day").reset_index(drop=True), model_cols


def rolling_score_mixed(table: pd.DataFrame, model_cols, min_train: int = 45,
                        window: int = 45, ridge: float = 1.0) -> pd.DataFrame:
    """Walk-forward Mixed EMOS (per-model mean weights + spread variance)."""
    t = table.reset_index(drop=True)
    rows = []
    for i in range(min_train, len(t)):
        tr = t.iloc[max(0, i - window):i]
        cur = t.iloc[i]
        Xm = tr[model_cols].to_numpy()
        Xv = (tr["ens_std"].to_numpy() ** 2).reshape(-1, 1)
        model = emos.fit_mixed(Xm, Xv, tr["high"].to_numpy(), ridge=ridge)
        mu, sigma = model.predict(cur[model_cols].to_numpy(), [[cur["ens_std"] ** 2]])
        rows.append({
            "day": cur["day"], "realized": cur["high"],
            "ens_mean": cur["ens_mean"], "mu": float(mu[0]), "sigma": float(sigma[0]),
            "crps_raw": float(emos.crps_gaussian(cur["high"], cur["ens_mean"], max(cur["ens_std"], 1e-6))),
            "crps_emos": float(emos.crps_gaussian(cur["high"], mu[0], sigma[0])),
            "pit": float(norm.cdf((cur["high"] - mu[0]) / sigma[0])),
        })
    return pd.DataFrame(rows)


def build_archive_table(st: Station, start: date, end: date) -> pd.DataFrame:
    """Real backtest table: multi-model archived forecasts vs realized CLI highs."""
    members = fetch_members_archive(st.lat, st.lon, start, end)
    feats = ensemble_features(members, st.std_utc_offset)
    feats["day"] = pd.to_datetime(feats["day"])
    realized = realized_high(st, start, end).rename("high")
    t = feats.merge(realized, left_on="day", right_index=True, how="inner")
    return t.sort_values("day").reset_index(drop=True)


def build_training_table(st: Station, past_days: int, forecast_days: int = 1) -> pd.DataFrame:
    """Join ensemble daily-high features with realized CLI-style highs.

    NOTE: past_days pulls recent days from the *current* ensemble run, not
    archived forecasts issued at a fixed lead time. That is fine for exercising
    the full fit -> predict -> settle pipeline on real data, but a fair
    backtest needs the GEFS reforecast archive (see load_reforecast, TODO).
    """
    members = fetch_members(st.lat, st.lon, forecast_days=forecast_days, past_days=past_days)
    feats = ensemble_features(members, st.std_utc_offset)

    today = date.today()
    o = obs.fetch_asos(st.iem_id, today - timedelta(days=past_days + 2), today + timedelta(days=1))
    realized = settlement.daily_high(o, st.std_utc_offset)[["day", "high"]]

    t = feats.merge(realized, on="day", how="left").sort_values("day").reset_index(drop=True)
    t["day"] = pd.to_datetime(t["day"])
    return t


def rolling_score(table: pd.DataFrame, min_train: int = 20, window: int = 45) -> pd.DataFrame:
    """Walk forward: fit EMOS on a trailing window, score the next day.

    A trailing window (not an expanding one) is what the EMOS literature
    prescribes: it lets the bias/spread coefficients track seasonal drift.
    """
    trained = table.dropna(subset=["high", "ens_mean", "ens_std"]).reset_index(drop=True)
    rows = []
    for i in range(min_train, len(trained)):
        tr = trained.iloc[max(0, i - window):i]
        cur = trained.iloc[i]
        model = emos.fit(tr["ens_mean"], tr["ens_std"], tr["high"])
        mu, sigma = model.predict(cur["ens_mean"], cur["ens_std"])
        rows.append({
            "day": cur["day"],
            "realized": cur["high"],
            "ens_mean": cur["ens_mean"],
            "mu": float(mu), "sigma": float(sigma),
            "crps_raw": float(emos.crps_gaussian(cur["high"], cur["ens_mean"], max(cur["ens_std"], 1e-6))),
            "crps_emos": float(emos.crps_gaussian(cur["high"], mu, sigma)),
            "pit": float(norm.cdf((cur["high"] - mu) / sigma)),
        })
    return pd.DataFrame(rows)


def calibration_factor(scored: pd.DataFrame) -> float:
    """Variance-inflation multiplier that makes the predictive well-dispersed.

    If standardized errors z=(y-mu)/sigma have std>1 the model is overconfident;
    multiplying sigma by std(z) restores calibration. Clipped to [1, 3] so a noisy
    small sample can't shrink or explode the spread.
    """
    if scored.empty:
        return 1.0
    z = (scored["realized"] - scored["mu"]) / scored["sigma"]
    return float(min(3.0, max(1.0, z.std())))


def summarize(scored: pd.DataFrame) -> dict:
    if scored.empty:
        return {"n": 0}
    raw, em = scored["crps_raw"].mean(), scored["crps_emos"].mean()
    return {
        "n": len(scored),
        "crps_raw": round(raw, 3),
        "crps_emos": round(em, 3),
        "crps_improvement_pct": round(100 * (1 - em / raw), 1),
        "pit_mean": round(scored["pit"].mean(), 3),   # ~0.5 if unbiased
        "pit_std": round(scored["pit"].std(), 3),      # ~0.29 (uniform) if calibrated
        "mae_emos": round((scored["mu"] - scored["realized"]).abs().mean(), 2),
    }

from datetime import date

import pandas as pd
import requests

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
ARCHIVE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Live ensemble: GEFS (31) + ECMWF-ENS (51) = 82 members. The live endpoint only
# retains members for the last several days, so it is for real-time pricing, not
# training (see fetch_members_archive for the backtest source).
MODELS = "gfs025,ecmwf_ifs025"

# Backtest ensemble: independent operational models, archived for years. Their
# spread stands in for ensemble spread in a multi-model EMOS.
ARCHIVE_MODELS = "gfs_seamless,ecmwf_ifs025,icon_seamless,gem_seamless,jma_seamless,meteofrance_seamless"


def _parse_members(hourly: dict) -> pd.DataFrame:
    time = pd.to_datetime(hourly["time"])
    frames = []
    for col, vals in hourly.items():
        if not col.startswith("temperature_2m"):
            continue
        member = col[len("temperature_2m"):].lstrip("_") or "ctl"
        frames.append(pd.DataFrame({"valid": time, "member": member, "tmpf": vals}))
    return pd.concat(frames, ignore_index=True)


def fetch_members(lat: float, lon: float, forecast_days: int = 3,
                  models: str = MODELS, timeout: int = 60,
                  past_days: int = 0) -> pd.DataFrame:
    """Live hourly 2m temperature per ensemble member (long: valid, member, tmpf)."""
    params = {
        "latitude": lat, "longitude": lon, "hourly": "temperature_2m",
        "models": models, "forecast_days": forecast_days, "past_days": past_days,
        "temperature_unit": "fahrenheit", "timezone": "GMT",
    }
    r = requests.get(ENSEMBLE_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return _parse_members(r.json()["hourly"])


def fetch_members_forecast(lat: float, lon: float, forecast_days: int = 2,
                           models: str = ARCHIVE_MODELS, timeout: int = 60) -> pd.DataFrame:
    """Live forward forecast from the same multi-model set used for training."""
    params = {
        "latitude": lat, "longitude": lon, "hourly": "temperature_2m",
        "models": models, "forecast_days": forecast_days,
        "temperature_unit": "fahrenheit", "timezone": "GMT",
    }
    r = requests.get(FORECAST_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return _parse_members(r.json()["hourly"])


def fetch_members_archive(lat: float, lon: float, start: date, end: date,
                          models: str = ARCHIVE_MODELS, timeout: int = 120) -> pd.DataFrame:
    """Archived hourly forecasts from multiple operational models, one per member.

    Each model is treated as an ensemble member so the same daily-high and
    feature code applies. Covers years of history, unlike the live endpoint.
    """
    params = {
        "latitude": lat, "longitude": lon, "hourly": "temperature_2m",
        "models": models, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "temperature_unit": "fahrenheit", "timezone": "GMT",
    }
    r = requests.get(ARCHIVE_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return _parse_members(r.json()["hourly"])


def member_daily_highs(members: pd.DataFrame, std_utc_offset: int) -> pd.DataFrame:
    """Collapse hourly member temps into each member's daily high per LST day."""
    from .settlement import lst_day
    df = members.dropna(subset=["tmpf"]).copy()
    df["day"] = lst_day(df["valid"], std_utc_offset)
    return df.groupby(["day", "member"])["tmpf"].max().rename("high").reset_index()


def ensemble_features(members: pd.DataFrame, std_utc_offset: int) -> pd.DataFrame:
    """Per target day: ensemble mean and spread of the predicted daily high."""
    mh = member_daily_highs(members, std_utc_offset)
    g = mh.groupby("day")["high"]
    return pd.DataFrame({
        "ens_mean": g.mean(),
        "ens_std": g.std(ddof=1),
        "n_members": g.count(),
    }).reset_index()

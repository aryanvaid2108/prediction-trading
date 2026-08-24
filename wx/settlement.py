import pandas as pd


def lst_day(valid_utc: pd.Series, std_utc_offset: int) -> pd.Series:
    """Map UTC timestamps to the CLI observation day.

    The NWS Daily Climate Report bins observations by Local Standard Time
    year-round, so the settlement 'day' ignores DST. This is why during
    daylight time the window shows up as 1:00 AM to 12:59 AM on the wall clock.
    """
    return (valid_utc + pd.to_timedelta(std_utc_offset, unit="h")).dt.date


def daily_high(obs: pd.DataFrame, std_utc_offset: int) -> pd.DataFrame:
    """Reconstruct the CLI-style daily maximum from hourly ASOS observations.

    obs: columns ['valid'] (tz-naive UTC) and ['tmpf'].
    Returns per-LST-day max temperature, raw and rounded to whole degrees F.

    This approximates the settlement value; the official CLI is derived from
    1-minute ASOS data and quality-controlled, so it can differ by a degree.
    """
    df = obs.dropna(subset=["tmpf"]).copy()
    df["day"] = lst_day(df["valid"], std_utc_offset)
    out = df.groupby("day")["tmpf"].max().rename("high_raw").reset_index()
    out["high"] = out["high_raw"].round().astype(int)
    return out


def daily_low(obs: pd.DataFrame, std_utc_offset: int) -> pd.DataFrame:
    df = obs.dropna(subset=["tmpf"]).copy()
    df["day"] = lst_day(df["valid"], std_utc_offset)
    out = df.groupby("day")["tmpf"].min().rename("low_raw").reset_index()
    out["low"] = out["low_raw"].round().astype(int)
    return out

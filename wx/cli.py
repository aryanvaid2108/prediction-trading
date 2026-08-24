"""NWS Daily Climate Report (CLI) — the official value that settles the market.

Cross-checks our ASOS reconstruction against the QC'd CLI high/low so we know the
settlement basis risk (how often, and by how much, they disagree). Near a bucket
boundary that gap is the difference between winning and losing a contract.
"""
import pandas as pd
import requests

CLI_URL = "https://mesonet.agron.iastate.edu/json/cli.py"


def fetch_cli(icao: str, year: int, month: int, timeout: int = 30) -> pd.DataFrame:
    """Official CLI high/low per day for a station-month (icao like 'KNYC').

    A day can have several CLI products (preliminary + corrected); keep the last,
    which is the finalized settlement value.
    """
    r = requests.get(CLI_URL, params={"station": icao, "year": year, "month": month}, timeout=timeout)
    r.raise_for_status()
    rows = [{"day": pd.Timestamp(x["valid"]).date(), "cli_high": x.get("high"), "cli_low": x.get("low")}
            for x in r.json().get("results", [])]
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset="day", keep="last") if not df.empty else df


def fetch_range(icao: str, start, end) -> pd.DataFrame:
    months = pd.period_range(start=start, end=end, freq="M")
    frames = [fetch_cli(icao, p.year, p.month) for p in months]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df[(df["day"] >= start) & (df["day"] <= end)].drop_duplicates(subset="day", keep="last")
    return df


def settlement_high(icao: str, start, end) -> pd.Series:
    """Official CLI daily high as the authoritative settlement value (day -> high)."""
    df = fetch_range(icao, start, end).dropna(subset=["cli_high"])
    return pd.Series(df["cli_high"].values, index=pd.to_datetime(df["day"]), name="high")

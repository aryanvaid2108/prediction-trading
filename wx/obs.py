import time
from datetime import date
from io import StringIO

import pandas as pd
import requests

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UA = {"User-Agent": "kalshi-weather/0.1 (research)"}


def fetch_asos(iem_id: str, start: date, end: date, timeout: int = 60,
               retries: int = 4) -> pd.DataFrame:
    """Hourly ASOS temperature observations from the Iowa Environmental Mesonet.

    Free, no auth. Timestamps are returned in UTC (tz-naive). end is exclusive
    on the returned day boundary per IEM behaviour, so pass end = day after the
    last day you want. Retries with backoff — IEM rate-limits (429) when several
    stations are fetched in quick succession, which would silently drop a station.
    """
    params = {
        "station": iem_id,
        "data": "tmpf",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year, "month2": end.month, "day2": end.day,
        "tz": "UTC",
        "format": "onlycomma",
        "latlon": "no",
        "missing": "empty",
        "trace": "empty",
        "report_type": "3",  # routine hourly METAR
    }
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(IEM_URL, params=params, timeout=timeout, headers=UA)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text))
            df["valid"] = pd.to_datetime(df["valid"])
            df["tmpf"] = pd.to_numeric(df["tmpf"], errors="coerce")
            return df[["valid", "tmpf"]]
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (attempt + 1))   # 429 needs a real pause
    raise last

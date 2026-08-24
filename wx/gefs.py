"""GEFSv12 reforecast ingester: a real ensemble spread for EMOS training.

The 6-model proxy in forecast.py has a weak spread predictor. This pulls the
GEFSv12 reforecast (5 members, 2000-2019) straight from NOAA's public S3 bucket,
byte-range subsetting only the tmax_2m messages that cover the target Local
Standard Time day, and returns each member's forecast daily high. Decoded values
are cached to disk so a backtest fetches each file once.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import eccodes
import numpy as np
import requests

BUCKET = "https://noaa-gefs-retrospective.s3.amazonaws.com"
MEMBERS = ["c00", "p01", "p02", "p03", "p04"]
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "gefs.json"
_STEP = re.compile(r"(\d+)-(\d+) hour max fcst")


def _url(init: date, member: str) -> str:
    d = init.strftime("%Y%m%d")
    return f"{BUCKET}/GEFSv12/reforecast/{init.year}/{d}00/{member}/Days:1-10/tmax_2m_{d}00_{member}.grib2"


def _parse_idx(text: str):
    """Return [(offset, start_h, end_h)] for each tmax interval-max message."""
    out = []
    for line in text.strip().split("\n"):
        f = line.split(":")
        m = _STEP.search(f[5])
        if m:
            out.append((int(f[1]), int(m.group(1)), int(m.group(2))))
    return out


def _point_f(msg_bytes: bytes, lat: float, lon: float) -> float:
    h = eccodes.codes_new_from_message(msg_bytes)
    try:
        n = eccodes.codes_grib_find_nearest(h, lat, lon)[0]
        return (n.value - 273.15) * 9 / 5 + 32
    finally:
        eccodes.codes_release(h)


def member_high(init: date, member: str, lat: float, lon: float,
                std_utc_offset: int, target: date, timeout: int = 60) -> float:
    """Forecast daily high for `target` (LST day) from one member's tmax messages."""
    url = _url(init, member)
    idx = _parse_idx(requests.get(url + ".idx", timeout=timeout).text)
    init_utc = datetime(init.year, init.month, init.day, tzinfo=timezone.utc)

    sel = []  # messages whose interval end falls on the target LST day
    for i, (off, sh, eh) in enumerate(idx):
        end_lst = init_utc + timedelta(hours=eh + std_utc_offset)
        if end_lst.date() == target and (eh - sh) == 6:  # 6h interval maxes tile the day
            nxt = idx[i + 1][0] if i + 1 < len(idx) else None
            sel.append((off, nxt))
    if not sel:
        return float("nan")

    span_start = sel[0][0]
    span_end = sel[-1][1]
    headers = {"Range": f"bytes={span_start}-{span_end - 1}"} if span_end else {"Range": f"bytes={span_start}-"}
    blob = requests.get(url, headers=headers, timeout=timeout).content

    highs = []
    for off, nxt in sel:
        a = off - span_start
        b = (nxt - span_start) if nxt else len(blob)
        highs.append(_point_f(blob[a:b], lat, lon))
    return float(np.max(highs))


def _load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}


def _save_cache(c: dict):
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(c))


def ensemble_high(init: date, lat: float, lon: float, std_utc_offset: int,
                  target: date, cache: dict = None) -> dict:
    """Per-member forecast highs for target day; cached by (init, target, point)."""
    own = cache is None
    cache = _load_cache() if own else cache
    key = f"{init}|{target}|{lat:.3f},{lon:.3f}"
    if key not in cache:
        cache[key] = {m: member_high(init, m, lat, lon, std_utc_offset, target) for m in MEMBERS}
        if own:
            _save_cache(cache)
    return cache[key]


def features(init: date, lat: float, lon: float, std_utc_offset: int,
             target: date, cache: dict = None) -> dict:
    h = ensemble_high(init, lat, lon, std_utc_offset, target, cache)
    vals = np.array([v for v in h.values() if v == v])
    return {"ens_mean": float(vals.mean()), "ens_std": float(vals.std(ddof=1)),
            "n_members": int(len(vals))}

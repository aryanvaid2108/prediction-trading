"""Pre-build the day's history cache (archive tables, CLI series) for every
active station and the shadow model set, so the 15Z live tick starts warm.
Cold, the first run of the day took 15 min for 8 cities; warm it takes ~2.

Usage: python -m scripts.warm_cache
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from wx import pipeline, stations
from wx.forecast import ARCHIVE_MODELS

SHADOW = os.environ.get("WX_SHADOW_MODELS", "")


def warm(icao):
    st = stations.get(icao)
    now = datetime.now(timezone.utc)
    t = time.time()
    try:
        pipeline.quote_live(st, date.today(), now_utc=now)
        if SHADOW:
            pipeline.quote_live(st, date.today(), now_utc=now, models=ARCHIVE_MODELS + "," + SHADOW)
        return f"{icao}: warmed ({time.time() - t:.0f}s)"
    except Exception as e:
        return f"{icao}: {type(e).__name__} ({time.time() - t:.0f}s)"


def main():
    with ThreadPoolExecutor(max_workers=2) as ex:
        for line in ex.map(warm, stations.ACTIVE):
            print(line, flush=True)


if __name__ == "__main__":
    main()

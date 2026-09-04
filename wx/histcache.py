"""Disk cache for history that ends before today, which never changes.

A station quote spends ~40 of its ~50 s refetching the 75-day archive table
(Open-Meteo previous runs + CLI) and the CLI settlement series — identical at
every tick of the day. Cached under .cache/hist/ (gitignored; the workflows
restore it across runner jobs with actions/cache keyed by date).
"""
import pickle
import time
from datetime import date
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / ".cache" / "hist"
KEEP_DAYS = 3


def get(key: str, end: date, build):
    """build() result for a history range ending at `end`; cached iff end < today."""
    if end >= date.today():
        return build()
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / f"{key}.pkl"
    if p.exists():
        return pickle.loads(p.read_bytes())
    v = build()
    p.write_bytes(pickle.dumps(v))
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in DIR.glob("*.pkl"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
    return v

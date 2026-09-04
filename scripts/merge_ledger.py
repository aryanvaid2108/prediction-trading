"""Resolve a rebase conflict on an append-only JSON ledger by UNION of both sides.

Two loop runs that overlap (a dispatch landing while a cron run is still
pushing) each append to the same ledger; git cannot merge pretty-printed JSON
arrays and the losing run's real fills would vanish from the books. Fills are
records, never edits, so the union of both sides is the only correct merge.

Usage (inside a conflicted rebase): python -m scripts.merge_ledger PATH [PATH ...]
"""
import json
import subprocess
import sys


def union(a, b):
    """Records of a followed by records of b not already in a (order kept)."""
    seen = [json.dumps(x, sort_keys=True) for x in a]
    out = list(a)
    for x in b:
        k = json.dumps(x, sort_keys=True)
        if k not in seen:
            seen.append(k); out.append(x)
    return out


def _side(stage, path):
    r = subprocess.run(["git", "show", f":{stage}:{path}"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []


def main(*paths):
    for p in paths:
        merged = union(_side(2, p), _side(3, p))
        with open(p, "w") as fh:
            json.dump(merged, fh, indent=2)
        subprocess.run(["git", "add", p], check=True)
        print(f"{p}: merged {len(merged)} records")


if __name__ == "__main__":
    main(*sys.argv[1:])

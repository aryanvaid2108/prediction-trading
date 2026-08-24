"""Fetch a station's NWS Area Forecast Discussion, parse it with Claude, and print
the uncertainty signal + the sigma multiplier it would apply.

Requires ANTHROPIC_API_KEY (or an `ant auth login` profile).
Usage: python -m scripts.afd_demo [ICAO]
"""
import os
import sys
from datetime import date

from wx import afd
from wx.stations import get


def main(icao="KNYC"):
    st = get(icao)
    text = afd.fetch_afd(st.wfo)
    print(f"{icao} ({st.name}) — AFD from WFO {st.wfo}, {len(text)} chars\n")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.path.exists(os.path.expanduser("~/.config/anthropic"))):
        print("No Claude credentials found. Set ANTHROPIC_API_KEY (or run `ant auth login`) to parse.")
        print("First 500 chars of the discussion:\n")
        i = text.find(".DISCUSSION")
        print(text[i:i + 500] if i >= 0 else text[:500])
        return
    s = afd.parse_afd(text, st.name, date.today())
    print(f"  confidence : {s.temp_confidence}")
    print(f"  lean       : {s.lean}")
    print(f"  risks      : {', '.join(s.risks) or '—'}")
    print(f"  rationale  : {s.rationale}")
    print(f"\n  -> sigma x{afd.sigma_factor(s):.2f}  (widen the distribution on low confidence)")


if __name__ == "__main__":
    main(*sys.argv[1:])

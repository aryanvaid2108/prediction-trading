"""Validate Kalshi credentials with a READ-ONLY signed request.

Hits GET /portfolio/balance, which exercises the exact RSA-PSS signing used for
orders but places nothing and risks no money. Prints the balance on success.

Usage: python -m scripts.check_auth   (needs KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH)
"""
import os
import sys

from wx import kalshi


def main():
    if not os.environ.get("KALSHI_ACCESS_KEY") or not os.environ.get("KALSHI_PRIVATE_KEY_PATH"):
        print("MISSING creds: set KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY_PATH")
        sys.exit(1)
    try:
        b = kalshi.balance()
    except Exception as e:
        print(f"AUTH FAILED: {type(e).__name__}: {e}")
        sys.exit(1)
    cents = b.get("balance")
    print("AUTH OK — signing works, keys are valid.")
    if cents is not None:
        print(f"account balance: ${cents/100:,.2f}")
    else:
        print(f"response: {b}")


if __name__ == "__main__":
    main()

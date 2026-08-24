#!/bin/bash
# Wrapper for the local launchd paper-trading loop.
cd /Users/aryan/Documents/GitHub/kalshi-weather || exit 1
echo "---- launchd fire $(date -u '+%Y-%m-%dT%H:%M:%SZ') ----" >> .cache/loop.log
./.venv/bin/python -m scripts.run_daily >> .cache/loop.log 2>&1

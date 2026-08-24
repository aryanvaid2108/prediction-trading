"""Parse the NWS Area Forecast Discussion (AFD) into a structured uncertainty
signal, using Claude.

The AFD is a meteorologist's free-text reasoning about the forecast — confidence,
competing scenarios, front timing, marine-layer/cloud/convection risks. Our loss
analysis showed our ~1F errors aren't news-driven, but a chunk of the *bigger*
misses (fronts, marine layer) are days the forecaster flags as uncertain. We use
that flag ONLY to widen the predictive distribution (bet less / skip) on shaky
days — never to move the forecast mean or to size a bet. LLM for text parsing,
deterministic engine for everything that touches money.
"""
from datetime import date
from typing import Literal

import requests
from pydantic import BaseModel, Field

AFD_URL = "https://api.weather.gov/products"
UA = {"User-Agent": "prediction-trading/0.1 (weather research)"}

# Confidence -> multiplier applied to the predictive sigma. Low confidence widens
# the distribution so marginal edges no longer clear the threshold.
SIGMA_INFLATION = {"high": 1.0, "medium": 1.15, "low": 1.4}


class AfdSignal(BaseModel):
    temp_confidence: Literal["high", "medium", "low"] = Field(
        description="Forecaster's confidence in the DAILY HIGH temperature for the target day")
    lean: Literal["warmer", "cooler", "neutral"] = Field(
        description="Does the discussion lean the high above (warmer) or below (cooler) raw model guidance, or neither")
    risks: list[str] = Field(
        description="Short tags for factors that could move the high: e.g. 'cold front timing', "
                    "'marine layer', 'cloud cover', 'convection', 'onshore flow'")
    rationale: str = Field(description="One sentence, quoting the discussion where possible")


def fetch_afd(wfo: str, timeout: int = 30) -> str:
    """Latest AFD text for a Weather Forecast Office (e.g. OKX, LOT, MTR)."""
    r = requests.get(f"{AFD_URL}/types/AFD/locations/{wfo}", headers=UA, timeout=timeout)
    r.raise_for_status()
    products = r.json().get("@graph", [])
    if not products:
        raise ValueError(f"no AFD for {wfo}")
    p = requests.get(f"{AFD_URL}/{products[0]['id']}", headers=UA, timeout=timeout)
    p.raise_for_status()
    return p.json()["productText"]


def parse_afd(text: str, station_name: str, target: date,
              model: str = "claude-opus-4-8") -> AfdSignal:
    """Extract the structured uncertainty signal from an AFD via Claude."""
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=model,
        max_tokens=1024,
        system=("You are a meteorologist reading an NWS Area Forecast Discussion. "
                "Assess only the DAILY HIGH TEMPERATURE for the named station and date. "
                "Confidence is 'low' when the discussion flags timing uncertainty, model "
                "disagreement, marine-layer/cloud/convection risk, or a front near the "
                "peak-heating window; 'high' when it reads as routine and settled."),
        messages=[{"role": "user", "content":
                   f"Station: {station_name}\nTarget day: {target:%A %Y-%m-%d}\n\n"
                   f"Area Forecast Discussion:\n{text[:8000]}"}],
        output_format=AfdSignal,
    )
    return resp.parsed_output


def sigma_factor(signal: AfdSignal) -> float:
    """Multiplier to apply to the predictive sigma (>= 1: widen on low confidence)."""
    return SIGMA_INFLATION[signal.temp_confidence]

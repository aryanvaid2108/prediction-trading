from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    icao: str        # 4-letter ICAO (e.g. KNYC) for the official CLI lookup
    kalshi: str      # Kalshi series ticker for the daily-high market
    iem_id: str      # IEM ASOS identifier
    name: str
    lat: float
    lon: float
    std_utc_offset: int  # Local Standard Time offset from UTC, in hours (no DST)
    wfo: str         # NWS office code for the settling CLI report


# Settlement source for each daily high market is the NWS Daily Climate Report (CLI)
# max for that station, read in whole degrees F over the Local-Standard-Time day.
# Verified in depth for NHIGH (Central Park); the others still need their contract
# PDF confirmed individually before trading real capital.
STATIONS = {
    "KNYC": Station("KNYC", "KXHIGHNY", "NYC", "Central Park, NY", 40.7889, -73.9661, -5, "OKX"),
    "KLAX": Station("KLAX", "KXHIGHLAX", "LAX", "Los Angeles, CA", 33.9382, -118.3866, -8, "LOX"),
    "KSFO": Station("KSFO", "KXHIGHTSFO", "SFO", "San Francisco, CA", 37.6189, -122.3750, -8, "MTR"),
    "KMDW": Station("KMDW", "KXHIGHCHI", "MDW", "Chicago Midway, IL", 41.7842, -87.7553, -6, "LOT"),
    "KAUS": Station("KAUS", "KXHIGHAUS", "AUS", "Austin, TX", 30.1975, -97.6664, -6, "EWX"),
    "KDEN": Station("KDEN", "KXHIGHDEN", "DEN", "Denver, CO", 39.8466, -104.6562, -7, "BOU"),
    "KMIA": Station("KMIA", "KXHIGHMIA", "MIA", "Miami, FL", 25.7906, -80.3164, -5, "MFL"),
    "KPHL": Station("KPHL", "KXHIGHPHIL", "PHL", "Philadelphia, PA", 39.8683, -75.2311, -5, "PHI"),
    # Added 2026-09-05 from the Kalshi series sweep (scripts/expand_markets.py,
    # .cache/expand_markets.csv): every entry has a clean data path and 41/41
    # settlement parity Jul 25-Sep 3 (scripts/parity_check.py).
    "KHOU": Station("KHOU", "KXHIGHTHOU", "HOU", "Houston Hobby, TX", 29.6454, -95.2789, -6, "HGX"),
    "KATL": Station("KATL", "KXHIGHTATL", "ATL", "Atlanta, GA", 33.6301, -84.4418, -5, "FFC"),
    "KDFW": Station("KDFW", "KXHIGHTDAL", "DFW", "Dallas-Fort Worth, TX", 32.8978, -97.0189, -6, "FWD"),
    "KLAS": Station("KLAS", "KXHIGHTLV", "LAS", "Las Vegas, NV", 36.0719, -115.1634, -8, "VEF"),
    "KMSP": Station("KMSP", "KXHIGHTMIN", "MSP", "Minneapolis, MN", 44.8831, -93.2289, -6, "MPX"),
    "KMSY": Station("KMSY", "KXHIGHTNOLA", "MSY", "New Orleans, LA", 29.9933, -90.2511, -6, "LIX"),
    "KOKC": Station("KOKC", "KXHIGHTOKC", "OKC", "Oklahoma City, OK", 35.3889, -97.6006, -6, "OUN"),
    "KPHX": Station("KPHX", "KXHIGHTPHX", "PHX", "Phoenix, AZ", 33.4278, -112.0037, -7, "PSR"),
    "KSEA": Station("KSEA", "KXHIGHTSEA", "SEA", "Seattle, WA", 47.4444, -122.3139, -8, "SEW"),
    "KBOS": Station("KBOS", "KXHIGHTBOS", "BOS", "Boston, MA", 42.3606, -71.0097, -5, "BOX"),
    "KDCA": Station("KDCA", "KXHIGHTDC", "DCA", "Washington Reagan, DC", 38.8483, -77.0342, -5, "LWX"),
    "KSAT": Station("KSAT", "KXHIGHTSATX", "SAT", "San Antonio, TX", 29.5443, -98.4839, -6, "EWX"),
}

# Stations the loops trade. Settlement parity (Kalshi == NWS CLI) verified 13/13
# for every entry. DEN + PHL added 2026-08-26 after passing the honest-backtest
# median gate (DEN +$876/67% win, PHL +$560/46%). KMIA verified 13/13 but BENCHED:
# median-day -$3.21 / 28% win (longshot profile, like LAX) — retest before adding.
# 2026-09-05: nine more cities passed the live-config backtest over Jul 1-Sep 4
# (positive as-is AND under 25%-of-hourly-volume fill caps, model Brier below the
# market's, worst day > -$150) plus 41/41 parity. Benched: KBOS (market leads,
# -$21), KDCA (Brier tied), KSAT (market leads, holdout -$40), KMIA (parity 40/41).
ACTIVE = ["KNYC", "KMDW", "KAUS", "KLAX", "KSFO", "KDEN", "KPHL",
          "KHOU", "KATL", "KDFW", "KLAS", "KMSP", "KMSY", "KOKC", "KPHX", "KSEA"]


def get(icao: str) -> Station:
    return STATIONS[icao.upper()]

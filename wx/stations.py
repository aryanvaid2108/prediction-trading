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
}

# Stations the live paper loop trades. Settlement source (Kalshi == NWS CLI)
# verified 13/13 days for all five.
ACTIVE = ["KNYC", "KMDW", "KAUS", "KLAX", "KSFO"]


def get(icao: str) -> Station:
    return STATIONS[icao.upper()]

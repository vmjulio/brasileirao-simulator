"""Where matches are played, and how far a club travels to play them.

Venue cities come from the fixtures exactly as the API spells them; their
coordinates come from `files/datasets/geo/city_coordinates.json`, built by
`entrypoints/build_city_coordinates.py` from the IBGE municipality table. The
map is keyed by that raw spelling, so a fixture row's `fixture_venue_city`
looks up directly - no normalising at the call site.

A club's home is where it most often hosts Série A matches that season, not
its registered address: Flamengo plays some home games in Volta Redonda, and
a club that moves stadium for a season moves with it. Travel for a match is
the great-circle distance from the away club's home to the match's venue.
"""

import collections
import csv
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from brasileirao_simulator.config.settings import DATASETS_PATH

CITY_COORDINATES = f"{DATASETS_PATH}/geo/city_coordinates.json"
EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Place:
    municipality: str
    uf: str
    region: str  # IBGE macro-region: Norte, Nordeste, Centro-Oeste, Sudeste, Sul
    lat: float
    lon: float


@lru_cache(maxsize=None)
def places(path: str = CITY_COORDINATES) -> dict:
    """`{venue city as spelled in the fixtures: Place}`."""
    with open(path, encoding="utf-8") as f:
        cities = json.load(f)["cities"]
    return {city: Place(v["municipality"], v["uf"], v["region"], v["lat"], v["lon"]) for city, v in cities.items()}


def place_of(city: Optional[str]) -> Optional[Place]:
    """The Place for a fixture's venue city, or None for a blank or unmapped one."""
    return places().get((city or "").strip())


def distance_km(a: Place, b: Place) -> float:
    """Great-circle (haversine) distance between two places."""
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi, dlambda = phi2 - phi1, math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def home_regions(season: int, datasets_path: str = DATASETS_PATH) -> dict:
    """`{team_id: IBGE macro-region}` for `season`'s Série A clubs, from each
    club's most frequent home city - what the Elo Sudeste term keys on."""
    counts = collections.defaultdict(collections.Counter)
    with open(f"{datasets_path}/{season}/fixtures.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            place = place_of(row.get("fixture_venue_city"))
            if place:
                counts[int(float(row["teams_home_id"]))][place.region] += 1
    return {team: c.most_common(1)[0][0] for team, c in counts.items()}


def home_places(season: int, datasets_path: str = DATASETS_PATH) -> dict:
    """`{club name: Place}` - each club's most frequent Série A home venue
    city in `season` (see the module docstring for why not its address)."""
    counts = collections.defaultdict(collections.Counter)
    with open(f"{datasets_path}/{season}/fixtures.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            place = place_of(row.get("fixture_venue_city"))
            if place:
                counts[row["teams_home_name"]][place] += 1
    return {club: c.most_common(1)[0][0] for club, c in counts.items()}

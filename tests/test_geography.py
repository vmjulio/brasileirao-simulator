"""Venue coordinates and travel distance."""

import csv
from pathlib import Path

import pytest

from brasileirao_simulator.domain.geography import Place, distance_km, home_places, place_of, places

DATASETS = Path(__file__).resolve().parents[1] / "src" / "files" / "datasets"


def test_every_serie_a_venue_city_2016_2026_has_coordinates():
    missing = set()
    for season in range(2016, 2027):
        with open(DATASETS / str(season) / "fixtures.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                city = (row.get("fixture_venue_city") or "").strip()
                if city and place_of(city) is None:
                    missing.add(city)
    assert not missing


def test_spelling_variants_land_on_one_place():
    assert place_of("Sao Paulo") == place_of("São Paulo, São Paulo")
    assert place_of("Rio de Janeiro") == place_of("Rio de Janeiro, Rio de Janeiro")


def test_ambiguous_names_resolve_to_the_clubs_state():
    assert place_of("Belém").uf == "PA" and place_of("Belém").region == "Norte"
    assert place_of("Boa Vista").uf == "RR"


def test_brasilia_districts_are_brasilia():
    assert place_of("Gama") == place_of("Taguatinga, Distrito Federal")
    assert place_of("Gama").municipality == "Brasília"


def test_blank_and_state_only_strings_have_no_place():
    assert place_of("") is None and place_of(None) is None
    assert place_of("Minas Gerais") is None


def test_known_distances():
    rio, sp = place_of("Rio de Janeiro"), place_of("Sao Paulo")
    assert distance_km(rio, sp) == pytest.approx(360, abs=15)       # ~357 km straight line
    belem, poa = place_of("Belém"), place_of("Porto Alegre")
    assert distance_km(belem, poa) == pytest.approx(3190, abs=60)   # ~3,190 km
    assert distance_km(rio, rio) == 0


def test_distance_is_symmetric():
    a, b = place_of("Recife"), place_of("Curitiba")
    assert distance_km(a, b) == pytest.approx(distance_km(b, a))


def test_home_is_the_most_frequent_home_city():
    homes = home_places(2025)
    assert homes["Flamengo"].municipality == "Rio de Janeiro"
    assert homes["Palmeiras"].municipality == "São Paulo"
    assert all(isinstance(p, Place) for p in homes.values())
    assert len(homes) == 20


def test_map_has_every_region():
    assert {p.region for p in places().values()} == {"Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"}

"""Behaviour tests for the elo-division-seeds ticket: `seed_for` and
`division_table` in `domain/elo_seeds.py`. The median-ordering gate
("after burn-in, median(Serie A) > median(Serie B) > median(cup-only)")
needs `replay`, which is a separate in-flight ticket - deferred to
integration, not written here.
"""

import pandas as pd
import pytest

from brasileirao_simulator.domain.elo import EloParams
from brasileirao_simulator.domain.elo_seeds import division_table, seed_for
from brasileirao_simulator.domain.match_store import MatchStore

_OUTPUT_COLUMNS = [
    "fixture_id",
    "fixture_date",
    "league_id",
    "season",
    "round",
    "home_id",
    "away_id",
    "home_name",
    "away_name",
    "home_goals",
    "away_goals",
    "is_neutral",
    "admitted_by",
    "status",
]

# Arbitrary team ids used across the synthetic fixtures below.
SERIE_A_CLUB = 1001
SERIE_B_CLUB = 1002
CUP_ONLY_CLUB = 1003
FOREIGN_CLUB = 1004
BOTH_A_AND_CUP_CLUB = 1005
BOTH_B_AND_LIBERTADORES_CLUB = 1006
LIBERTADORES_ONLY_CLUB = 1007


def _row(fixture_id, season, league_id, home_id, away_id, date="2025-05-01"):
    return {
        "fixture_id": fixture_id,
        "fixture_date": pd.Timestamp(date, tz="UTC"),
        "league_id": league_id,
        "season": season,
        "round": "Regular Season - 1",
        "home_id": home_id,
        "away_id": away_id,
        "home_name": f"Club {home_id}",
        "away_name": f"Club {away_id}",
        "home_goals": 1,
        "away_goals": 0,
        "is_neutral": False,
        "admitted_by": "test fixture",
        "status": "FT",
    }


def _store(rows):
    """Build a `MatchStore` without touching disk, per the pattern the
    ticket points at: bypass `__init__` (which reads CSV shards) and set
    `_matches` directly to a hand-built frame of the same shape
    `MatchStore.matches` produces."""
    store = MatchStore.__new__(MatchStore)
    store._matches = pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)
    return store


def test_cup_only_club_seeds_at_tier_3():
    # Gate 7: a club whose only appearance that season is a Copa do Brasil
    # tie (league 73) seeds at tier 3, never a hand-typed club table.
    rows = [_row(1, 2025, 73, CUP_ONLY_CLUB, 999)]
    store = _store(rows)
    params = EloParams()
    assert seed_for(CUP_ONLY_CLUB, 2025, store, params) == params.seeds[3]


def test_tier_absent_from_seeds_raises_key_error_no_silent_default():
    # Gate 8: with a seeds mapping missing the tier-3 key, a tier-3 club
    # raises KeyError - it must never fall back to the notebook's silent
    # 800 default or any other value absent from params.seeds.
    rows = [_row(1, 2025, 73, CUP_ONLY_CLUB, 999)]
    store = _store(rows)
    params = EloParams(seeds={1: 1500.0, 2: 1400.0})
    with pytest.raises(KeyError):
        seed_for(CUP_ONLY_CLUB, 2025, store, params)


def test_club_with_no_match_in_season_raises_value_error():
    rows = [_row(1, 2025, 71, SERIE_A_CLUB, 999)]
    store = _store(rows)
    params = EloParams()
    with pytest.raises(ValueError):
        seed_for(SERIE_A_CLUB, 2024, store, params)


def test_club_in_serie_a_and_cup_seeds_at_tier_1():
    rows = [
        _row(1, 2025, 71, BOTH_A_AND_CUP_CLUB, 999),
        _row(2, 2025, 73, BOTH_A_AND_CUP_CLUB, 998),
    ]
    store = _store(rows)
    params = EloParams()
    assert seed_for(BOTH_A_AND_CUP_CLUB, 2025, store, params) == params.seeds[1]


def test_club_in_serie_b_and_libertadores_seeds_at_tier_2():
    rows = [
        _row(1, 2025, 72, BOTH_B_AND_LIBERTADORES_CLUB, 999),
        _row(2, 2025, 13, BOTH_B_AND_LIBERTADORES_CLUB, 998),
    ]
    store = _store(rows)
    params = EloParams()
    assert seed_for(BOTH_B_AND_LIBERTADORES_CLUB, 2025, store, params) == params.seeds[2]


def test_club_only_in_libertadores_seeds_foreign():
    rows = [_row(1, 2025, 13, LIBERTADORES_ONLY_CLUB, 999)]
    store = _store(rows)
    params = EloParams()
    assert seed_for(LIBERTADORES_ONLY_CLUB, 2025, store, params) == params.seeds["foreign"]


def test_division_is_per_season_not_per_club():
    # Same club: Serie B in 2024, Serie A in 2025. The seed must track the
    # season being asked about, not some club-wide division.
    rows = [
        _row(1, 2024, 72, SERIE_A_CLUB, 999),
        _row(2, 2025, 71, SERIE_A_CLUB, 998),
    ]
    store = _store(rows)
    params = EloParams()
    assert seed_for(SERIE_A_CLUB, 2024, store, params) == 1400.0
    assert seed_for(SERIE_A_CLUB, 2025, store, params) == 1500.0


def test_division_table_shape_and_tiers():
    rows = [
        _row(1, 2025, 71, SERIE_A_CLUB, 999),
        _row(2, 2025, 72, SERIE_B_CLUB, 998),
        _row(3, 2025, 73, CUP_ONLY_CLUB, 997),
        _row(4, 2025, 13, FOREIGN_CLUB, 996),
    ]
    store = _store(rows)
    table = division_table(store)

    assert list(table.columns) == ["season", "team_id", "tier"]
    assert not table.duplicated(subset=["season", "team_id"]).any()

    by_team = table.set_index("team_id")["tier"]
    assert by_team[SERIE_A_CLUB] == 1
    assert by_team[SERIE_B_CLUB] == 2
    assert by_team[CUP_ONLY_CLUB] == 3
    assert by_team[FOREIGN_CLUB] == "foreign"


def test_division_table_result_is_a_defensive_copy():
    rows = [_row(1, 2025, 71, SERIE_A_CLUB, 999)]
    store = _store(rows)
    table = division_table(store)
    table["tier"] = table["tier"].astype(object)
    table.loc[:, "tier"] = "mutated"
    # A second call must not see the mutation above - the cache is not the
    # same object handed back to callers.
    assert (division_table(store)["tier"] != "mutated").all()


@pytest.mark.slow
def test_division_table_on_real_store_2025_tier_counts():
    store = MatchStore()
    table = division_table(store)

    assert not table.duplicated(subset=["season", "team_id"]).any()

    season_2025 = table[table["season"] == 2025]
    tier_counts = season_2025["tier"].value_counts()

    assert tier_counts.get(1, 0) == 20
    assert tier_counts.get(2, 0) == 20
    assert tier_counts.get(3, 0) > 0
    assert tier_counts.get("foreign", 0) > 0

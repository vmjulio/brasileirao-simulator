"""Characterization test: the 2025 season must simulate identically before and
after season parameterization. This file is the contract that the refactor did
not change behaviour for the season that already worked."""

import numpy as np
import pytest

from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)


BRASILEIRAO_TEAM_COUNT = 20


@pytest.fixture
def mid_season_date() -> str:
    """A date with roughly half the 2025 season played, so the simulation has
    both real results behind it and fixtures left to simulate."""
    return "2025-08-31"


def test_2025_standings_have_twenty_teams(mid_season_date):
    np.random.seed(42)
    tables = Tables(SeasonData(2025))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=mid_season_date)
    remaining = tables.remaining_games(blank_from_date=mid_season_date)

    adapter = PoissonSameVenueAverageAdapter("average", season=2025)
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    standings = adapter.get_brasileirao_standings(simulated)

    assert len(standings) == BRASILEIRAO_TEAM_COUNT
    assert standings["rank_"].min() == 1
    assert standings["rank_"].max() == BRASILEIRAO_TEAM_COUNT


def test_2025_blanking_leaves_games_to_simulate(mid_season_date):
    """The as-of-date feature: results after the date are blanked, so those
    fixtures come back as games still to play."""
    tables = Tables(SeasonData(2025))
    remaining = tables.remaining_games(blank_from_date=mid_season_date)

    assert len(remaining) > 0, "blanking produced no remaining games to simulate"
    assert remaining["goals_for"].isnull().all()


def test_2025_every_team_gets_a_full_season(mid_season_date):
    np.random.seed(42)
    tables = Tables(SeasonData(2025))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=mid_season_date)
    remaining = tables.remaining_games(blank_from_date=mid_season_date)

    adapter = PoissonSameVenueAverageAdapter("average", season=2025)
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    standings = adapter.get_brasileirao_standings(simulated)

    assert (standings["g"] == 38).all(), "a simulated season must have 38 games per team"

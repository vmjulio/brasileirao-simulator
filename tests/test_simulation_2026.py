"""The new season must simulate as completely as the old one."""

import numpy as np

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


BRASILEIRAO_TEAM_COUNT = 20
AS_OF_DATE = "2026-03-01"


def test_2026_simulates_a_full_season_as_of_an_early_date():
    """Early in a season the lookback window reaches into 2025 for its averages.
    A full 20-team, 38-game table proves that fallback worked."""
    np.random.seed(42)
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF_DATE)
    remaining = tables.remaining_games(blank_from_date=AS_OF_DATE)

    adapter = PoissonSameVenueAverageAdapter("average", season=2026)
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    standings = adapter.get_brasileirao_standings(simulated)

    assert len(standings) == BRASILEIRAO_TEAM_COUNT
    assert (standings["g"] == 38).all()


def test_2026_standings_contain_no_2025_rows():
    """The adapter's season filter and the standings query must agree; a leak
    would show up as more than 20 teams or as a team with more than 38 games."""
    np.random.seed(42)
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF_DATE)
    remaining = tables.remaining_games(blank_from_date=AS_OF_DATE)

    adapter = PoissonSameVenueAverageAdapter("average", season=2026)
    simulated = adapter.simulate_fixtures(fixtures, remaining)

    assert (simulated["season"] == 2026).all()

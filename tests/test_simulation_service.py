"""End to end: the service turns a season plus an as-of date into probabilities."""

import numpy as np
import pytest

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService


ITERATIONS = 4


def _run(season: int, as_of: str, tmp_path) -> dict:
    np.random.seed(42)
    params = SimulationParams(
        season=season,
        iterations=ITERATIONS,
        max_batch_size=ITERATIONS,
        ignore_results_after=as_of,
        load_results=False,
    )
    persistence = PickleAdapter(str(tmp_path), season)
    SimulationService(
        persistence_adapter=persistence,
        simulator_adapter=PoissonSameVenueAverageAdapter(params.strategy, season),
        params=params,
    ).run_simulation()

    return persistence.load_results(params.strategy, suffix=as_of)


def test_2026_title_probabilities_sum_to_one_hundred_percent(tmp_path):
    results = _run(2026, "2026-03-01", tmp_path)
    titles = results["brasileirao_title"]

    assert sum(titles.values()) == ITERATIONS, "every iteration must crown one champion"
    assert all(team for team in titles), "a blank team name means the season filter dropped rows"


def test_2026_relegation_has_four_teams_per_iteration(tmp_path):
    """Positions 17 through 20 go down, so four teams are relegated per run."""
    results = _run(2026, "2026-03-01", tmp_path)

    assert sum(results["brasileirao_relegation"].values()) == ITERATIONS * 4


def test_2025_still_produces_probabilities(tmp_path):
    """The same guarantee for the season that already worked."""
    results = _run(2025, "2025-08-31", tmp_path)

    assert sum(results["brasileirao_title"].values()) == ITERATIONS


def test_adapter_season_mismatch_raises(tmp_path):
    """A simulator adapter built for one season and params for another must not
    be allowed to silently write one season's table into another's folder."""
    params = SimulationParams(season=2026, iterations=ITERATIONS, max_batch_size=ITERATIONS)

    with pytest.raises(ValueError, match="2025.*2026|2026.*2025"):
        SimulationService(
            persistence_adapter=PickleAdapter(str(tmp_path), 2026),
            simulator_adapter=PoissonSameVenueAverageAdapter(params.strategy, 2025),
            params=params,
        )

"""The batch adapters, and their agreement with the per-season path."""

import numpy as np
import pytest

from brasileirao_simulator.adapters.batch_poisson_adapter import (
    FullVectorAdapter,
    IterationBatchAdapter,
)
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.result_logger import ResultLogger
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


AS_OF = "2026-05-03"
BRASILEIRAO_TEAM_COUNT = 20


def _frames():
    tables = Tables(SeasonData(2026))
    return (
        tables.enriched_tidy_fixtures(blank_from_date=AS_OF),
        tables.remaining_games(blank_from_date=AS_OF),
    )


def test_both_adapters_expose_the_season_the_service_checks():
    """SimulationService rejects an adapter whose season differs from params."""
    assert IterationBatchAdapter("average", 2026).season == 2026
    assert FullVectorAdapter("average", 2026).season == 2026


def test_adapter_produces_a_full_batch():
    fixtures, remaining = _frames()
    outcome = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 20)

    assert outcome.rank.shape == (20, BRASILEIRAO_TEAM_COUNT)
    for row in outcome.rank:
        assert sorted(row) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))


@pytest.mark.slow  # drives the reference adapter's Python-loop-plus-SQL path 120 times
def test_batch_agrees_with_the_per_season_adapter():
    """The reference implementation and the batch path are the same model, so
    their champion distributions must agree within sampling error.

    120 reference iterations is a cost/power trade-off: the reference path
    runs a Python fixture loop plus a SQL round trip per season (~0.36s each),
    so 400 was expensive. At n=120 the standard error on a proportion is
    ~4.5 percentage points per side; 0.15 is roughly a 2-sigma band on that,
    so do not tighten it without re-deriving the band, and do not loosen it
    further - a real divergence between the two paths must still trip it.
    """
    fixtures, remaining = _frames()

    np.random.seed(5)
    reference = PoissonSameVenueAverageAdapter("average", 2026)
    counts = {}
    for _ in range(120):
        standings = reference.get_brasileirao_standings(
            reference.simulate_fixtures(fixtures, remaining)
        )
        champion = standings.iloc[0]["team_name"]
        counts[champion] = counts.get(champion, 0) + 1

    outcome = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 120)
    teams = sorted(fixtures[fixtures["season"] == 2026]["team_name"].unique())
    batch = {teams[i]: int((outcome.rank[:, i] == 1).sum()) for i in range(len(teams))}

    for team in set(counts) | set(batch):
        assert abs(counts.get(team, 0) - batch.get(team, 0)) / 120 < 0.15


def test_logger_counts_a_batch_the_same_way_it_counts_one_season():
    fixtures, remaining = _frames()
    outcome = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 30)

    logger = ResultLogger()
    logger.log_batch(outcome)
    results = logger.get_results()

    assert sum(results["brasileirao_title"].values()) == 30
    assert sum(results["brasileirao_relegation"].values()) == 30 * 4
    for team in outcome.baseline.teams:
        assert sum(results["brasileirao_positions"][team].values()) == 30


def test_log_batch_fills_every_counter_the_exports_read():
    fixtures, remaining = _frames()
    adapter = IterationBatchAdapter("average", 2026)
    outcome = adapter.simulate_batch(fixtures, remaining, 25)

    logger = ResultLogger()
    logger.log_batch(outcome)
    results = logger.get_results()

    assert results["brasileirao_relegation_points"], "relegation points never populated"
    assert results["match_results"], "match results never populated"

    a_match = next(iter(results["match_results"].values()))
    assert a_match["home"] + a_match["draw"] + a_match["away"] == 25
    assert "round_" in a_match


@pytest.mark.slow  # 1500 iterations per adapter to get a stable champion share
def test_full_vector_adapter_matches_the_iteration_adapter():
    """1500 iterations per side and a 0.06 tolerance, for the same reasons as
    test_both_strategies_agree_on_the_distribution in test_batch_simulation.py
    - both adapters are fast, so the cost here is iteration count squared
    against wall time, not the SQL round trip that limits the test above."""
    fixtures, remaining = _frames()
    looped = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 1500)
    vector = FullVectorAdapter("average", 2026).simulate_batch(fixtures, remaining, 1500)

    looped_share = (looped.rank == 1).mean(axis=0)
    vector_share = (vector.rank == 1).mean(axis=0)

    assert np.abs(looped_share - vector_share).max() < 0.06

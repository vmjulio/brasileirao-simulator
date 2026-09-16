"""The batch adapters, and their agreement with the per-season path."""

import pickle

import numpy as np
import pytest

from brasileirao_simulator.adapters.batch_poisson_adapter import (
    FullVectorAdapter,
    IterationBatchAdapter,
)
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
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

    Both sides are seeded: np.random.seed(5) below drives the reference path
    (it uses numpy's legacy global RandomState), and the batch adapter is
    given its own np.random.default_rng(5) - a separate PCG64 stream that the
    legacy seed call has no effect on. Without pinning both, this test drew
    independent samples on each run and was flaky.
    """
    fixtures, remaining = _frames()
    # tidy_fixtures.sql UNIONs two branches with no ORDER BY, so the row order
    # DuckDB hands back is not guaranteed stable across runs. That would still
    # matter downstream even with both RNGs pinned: many of a round's fixtures
    # share one placeholder kickoff time, so the reference and batch adapters'
    # sort_values(by=["fixture_date"]) can't fully break the tie either, and
    # falls back to whatever order it received. Pin a canonical order here -
    # both keys are unique (checked empirically) - so the two adapters see
    # identical input on every run, not just on this one.
    fixtures = fixtures.sort_values(["fixture_id", "team_name"]).reset_index(drop=True)
    remaining = remaining.sort_values("fixture_id").reset_index(drop=True)

    np.random.seed(5)
    reference = PoissonSameVenueAverageAdapter("average", 2026)
    counts = {}
    for _ in range(120):
        standings = reference.get_brasileirao_standings(
            reference.simulate_fixtures(fixtures, remaining)
        )
        champion = standings.iloc[0]["team_name"]
        counts[champion] = counts.get(champion, 0) + 1

    outcome = IterationBatchAdapter(
        "average", 2026, rng=np.random.default_rng(5)
    ).simulate_batch(fixtures, remaining, 120)
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


def test_log_batch_matches_a_real_pickles_match_results():
    """Compares contents, not shape.

    The review gate this was supposed to catch compared only top-level pickle
    keys, so a batch run that silently dropped every already-played fixture
    from match_results (137 of the real pickle's 380 entries at this as-of
    date - everything but the 243 still-remaining fixtures) passed unnoticed.
    Here every entry the real, loop-written pickle has must be present in the
    batch-written one, and each entry's home/draw/away split must sum to the
    iteration count - 200, matching how every existing 2026 pickle was built.
    """
    fixtures, remaining = _frames()
    iterations = 200
    outcome = IterationBatchAdapter("average", 2026).simulate_batch(
        fixtures, remaining, iterations
    )

    logger = ResultLogger()
    logger.log_batch(outcome)
    fresh = logger.get_results()

    with open(f"{RESULTS_DIRECTORY}/2026/average_results_{AS_OF}.pkl", "rb") as f:
        existing = pickle.load(f)

    assert len(fresh["match_results"]) == len(existing["match_results"]) == 380
    for key, entry in fresh["match_results"].items():
        assert key in existing["match_results"]
        assert entry["home"] + entry["draw"] + entry["away"] == iterations


def test_zero_remaining_fixtures_does_not_crash():
    """A completed season's final backfill date has no remaining fixtures -
    reproduced on 2025-12-07, 2025's last date. Nothing in this branch
    exercised that edge before this test: _fixture_arrays built an empty
    Python list into a float64 numpy array, and np.add.at's fancy indexing
    raised IndexError because it requires integer-typed index arrays. The
    batch path must instead return a complete, sensible standings table -
    the played table alone, since there is nothing left to simulate.
    """
    as_of = "2025-12-07"
    tables = Tables(SeasonData(2025))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of)
    remaining = tables.remaining_games(blank_from_date=as_of)
    assert remaining.empty, "this test requires an as-of date with nothing left to simulate"

    outcome = IterationBatchAdapter("average", 2025).simulate_batch(fixtures, remaining, 5)

    assert outcome.rank.shape == (5, BRASILEIRAO_TEAM_COUNT)
    for row in outcome.rank:
        assert sorted(row) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))

"""collect_forecasts and true_outcomes - the harness that replays a season and
joins its forecasts to what actually happened. Neither had any tests before
this file: a name-space drift between the forecast keys and the final table
would previously have silently forecast 0.0 for every team and looked like a
suspiciously GOOD Brier score rather than an obvious failure (see the guard
asserts inside collect_forecasts's date loop).
"""

import shutil

import pytest

from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.calibration_backtest import (
    collect_forecasts,
    true_outcomes,
)


SEASON = 2025
BRASILEIRAO_TEAM_COUNT = 20
SCRATCH_ROOT = "files/pkl_calibration_test"


@pytest.fixture
def scratch_root():
    yield SCRATCH_ROOT
    shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)


@pytest.fixture
def deterministic_fixture_order(monkeypatch):
    """DuckDB does not guarantee row order for a query with no ORDER BY, and
    enriched_tidy_fixtures.sql has none over its final result - a property
    unrelated to this fix wave, and out of scope to change since it flows
    into the protected build_baseline/IterationBatchAdapter path. Two
    independent Tables() instances for the same as-of date can therefore
    hand build_baseline fixtures in a different order, which - because the
    Poisson draw is one rng.poisson call per fixture position - reassigns
    which SPECIFIC drawn goals go to which SPECIFIC fixture between runs.

    That is real: it means the common-random-numbers fix (item 4) only
    truly lines up two variants' streams when both read the identical
    physical row order. This fixture pins that order (memoising
    enriched_tidy_fixtures per season/date within the test) purely so THIS
    test can isolate "is the seed threaded through correctly" from that
    unrelated, pre-existing source of noise - it is not a claim that
    production runs get this for free.
    """
    original = Tables.enriched_tidy_fixtures
    cache = {}

    def cached(self, blank_from_date=None):
        key = (self.season_data.season, blank_from_date)
        if key not in cache:
            cache[key] = original(self, blank_from_date)
        return cache[key].copy()

    monkeypatch.setattr(Tables, "enriched_tidy_fixtures", cached)


# ---------------------------------------------------------------------------
# true_outcomes
# ---------------------------------------------------------------------------


def test_true_outcomes_reads_off_the_complete_2025_table():
    outcome = true_outcomes(SEASON)

    assert len(outcome.teams) == BRASILEIRAO_TEAM_COUNT
    assert outcome.champion in outcome.teams
    assert len(outcome.relegated) == 4
    assert outcome.relegated <= set(outcome.teams)


def test_true_outcomes_rejects_a_season_still_in_progress():
    """2026 is not complete, so scoring against it would not be scoring
    against ground truth at all."""
    with pytest.raises(ValueError, match="does not look complete"):
        true_outcomes(2026)


# ---------------------------------------------------------------------------
# collect_forecasts
# ---------------------------------------------------------------------------


def test_collect_forecasts_returns_one_row_per_date_and_team(scratch_root):
    forecasts = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=20,
        scratch_root=scratch_root,
        seed=1,
        dates=["2025-08-31"],
    )

    assert len(forecasts) == BRASILEIRAO_TEAM_COUNT
    assert set(forecasts["team"]) <= set(true_outcomes(SEASON).teams)
    assert (forecasts["date"] == "2025-08-31").all()


def test_collect_forecasts_title_probabilities_sum_to_one_per_date(scratch_root):
    """Every iteration has exactly one champion, so summed across all 20
    teams a date's title_prob must total 1.0 - the same invariant the
    in-loop assert on title_counts checks before the join, expressed here on
    the join's own output."""
    forecasts = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=20,
        scratch_root=scratch_root,
        seed=1,
        dates=["2025-08-31"],
    )

    assert forecasts["title_prob"].sum() == pytest.approx(1.0)


def test_collect_forecasts_relegation_probabilities_sum_to_four_per_date(scratch_root):
    """Every iteration relegates exactly four teams."""
    forecasts = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=20,
        scratch_root=scratch_root,
        seed=1,
        dates=["2025-08-31"],
    )

    assert forecasts["relegation_prob"].sum() == pytest.approx(4.0)


def test_collect_forecasts_marks_the_real_champion_and_relegated_sides(scratch_root):
    """title_outcome/relegation_outcome must come from the REAL final table,
    not from anything the simulation itself produced."""
    outcome = true_outcomes(SEASON)
    forecasts = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=20,
        scratch_root=scratch_root,
        seed=1,
        dates=["2025-08-31"],
    )

    champion_row = forecasts[forecasts["team"] == outcome.champion].iloc[0]
    assert champion_row["title_outcome"] == 1.0

    non_champions = forecasts[forecasts["team"] != outcome.champion]
    assert (non_champions["title_outcome"] == 0.0).all()

    for team in outcome.relegated:
        row = forecasts[forecasts["team"] == team].iloc[0]
        assert row["relegation_outcome"] == 1.0


def test_collect_forecasts_same_seed_reproduces_identical_forecasts(
    scratch_root, deterministic_fixture_order
):
    """Common random numbers (fix 4): the same seed must give byte-identical
    forecasts run to run, or a published Brier pair can never be reproduced.
    deterministic_fixture_order pins the one unrelated source of run-to-run
    noise (see that fixture's docstring) so this isolates the seed-threading
    behaviour this fix actually owns."""
    first = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=25,
        scratch_root=scratch_root,
        seed=7,
        dates=["2025-08-31"],
    )
    shutil.rmtree(scratch_root, ignore_errors=True)
    second = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=25,
        scratch_root=scratch_root,
        seed=7,
        dates=["2025-08-31"],
    )

    assert first["title_prob"].tolist() == second["title_prob"].tolist()
    assert first["relegation_prob"].tolist() == second["relegation_prob"].tolist()


def test_collect_forecasts_a_different_seed_changes_the_forecasts(
    scratch_root, deterministic_fixture_order
):
    """The seed must actually be threaded through, not silently ignored."""
    seed0 = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=25,
        scratch_root=scratch_root,
        seed=0,
        dates=["2025-08-31"],
    )
    shutil.rmtree(scratch_root, ignore_errors=True)
    seed1 = collect_forecasts(
        season=SEASON,
        simulator="batch",
        iterations=25,
        scratch_root=scratch_root,
        seed=123,
        dates=["2025-08-31"],
    )

    assert seed0["title_prob"].tolist() != seed1["title_prob"].tolist()

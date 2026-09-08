"""Turning one as-of date into arrays.

The whole batch design rests on one property: every remaining fixture's lambda
pair is constant across iterations, because team_params is computed from the
UNSIMULATED fixtures and never re-read inside the loop. These tests pin the
lambdas to the existing adapter's, so the two paths are provably the same model
rather than merely similar.
"""

import numpy as np

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.batch_simulation import (
    ADJUSTMENT_WEIGHT,
    MISSING_TEAM_AVERAGE,
    build_baseline,
)
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


AS_OF = "2026-05-03"


def _setup():
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    remaining = tables.remaining_games(blank_from_date=AS_OF)
    adapter = PoissonSameVenueAverageAdapter("average", 2026)
    team_params = adapter.get_team_params(fixtures.copy())
    return fixtures, remaining, team_params, adapter


def test_lambdas_match_the_existing_adapter_exactly():
    """Not 'close': bit-identical. Any difference means a different model."""
    fixtures, remaining, team_params, adapter = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    games = remaining.sort_values(by=["fixture_date"]).to_dict(orient="records")
    expected = np.array(
        [adapter._calculate_adjusted_averages(game, team_params) for game in games]
    )

    assert np.array_equal(baseline.lam_home, expected[:, 0])
    assert np.array_equal(baseline.lam_away, expected[:, 1])


def test_baseline_covers_every_team_and_remaining_fixture():
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    assert len(baseline.teams) == 20
    assert len(baseline.lam_home) == len(remaining)
    assert baseline.home_team.max() < 20
    assert baseline.away_team.max() < 20


def test_played_results_are_carried_in_as_the_starting_table():
    """The as-of table must equal what the SQL says it is, or every simulated
    season starts from the wrong place.

    Checked against an independent oracle rather than re-derived: SQL already
    computes running cum_sum_points / cum_sum_wins / cum_sum_goals_for per team
    and season, ordered by fixture_date. Each team's last played row (its
    highest fixture_date among rows with a non-null goals_for, within season
    2026) carries that team's season-to-date totals, since later (blanked,
    future) rows in the window ordering can't affect an earlier row's running
    sum. A shared bug in the scoring rule between build_baseline and this
    query would still be caught, because the two are computed by unrelated
    code paths (a Python loop vs. a SQL window function).
    """
    fixtures, remaining, team_params, adapter = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    played = fixtures[(fixtures["season"] == 2026) & (fixtures["goals_for"].notnull())]
    for position, team in enumerate(baseline.teams):
        rows = played[played["team_name"] == team]
        last_row = rows.loc[rows["fixture_date"].idxmax()]

        assert baseline.points[position] == last_row["cum_sum_points"]
        assert baseline.wins[position] == last_row["cum_sum_wins"]
        assert baseline.goals_for[position] == last_row["cum_sum_goals_for"]


def test_a_team_absent_from_team_params_falls_back_to_one():
    """Mirrors the adapter's .empty guard. With no parameters at all, every
    lambda must be exactly the fallback - asserting a floor instead would pass
    for almost any wrong fallback value, since the other term is a real average."""
    fixtures, remaining, team_params, _ = _setup()
    empty = team_params.iloc[0:0]

    baseline = build_baseline(fixtures, remaining, empty, 2026)

    expected = 2 * ADJUSTMENT_WEIGHT * MISSING_TEAM_AVERAGE
    assert np.array_equal(baseline.lam_home, np.full(len(baseline.lam_home), expected))
    assert np.array_equal(baseline.lam_away, np.full(len(baseline.lam_away), expected))

"""Turning one as-of date into arrays.

The whole batch design rests on one property: every remaining fixture's lambda
pair is constant across iterations, because team_params is computed from the
UNSIMULATED fixtures and never re-read inside the loop. These tests pin the
lambdas to the existing adapter's, so the two paths are provably the same model
rather than merely similar.
"""

import numpy as np
import pytest

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.batch_simulation import (
    ADJUSTMENT_WEIGHT,
    MISSING_TEAM_AVERAGE,
    BatchOutcome,
    build_baseline,
    rank_tables,
    simulate_batch,
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


def test_rank_matches_the_sql_tiebreakers():
    """p desc, then w desc, then gd desc, then gf desc.

    Teams 0 and 1 tie on points and wins, and gd and gf point opposite ways
    for them (team 0 has the bigger gf but the smaller gd), so this pins gd
    ahead of gf in the tiebreak order rather than merely pinning "some"
    combination that happens to produce the same rank either way.
    """
    points = np.array([[10.0, 10.0, 10.0, 7.0]])
    wins = np.array([[3.0, 3.0, 2.0, 9.0]])
    goals_for = np.array([[10.0, 5.0, 99.0, 0.0]])
    goals_against = np.array([[8.0, 0.0, 0.0, 0.0]])

    rank = rank_tables(points, wins, goals_for, goals_against)

    # team 0: 10pts 3w gd+2 gf10 ; team 1: 10pts 3w gd+5 gf5 -> team 1 wins the
    # tie on gd despite the lower gf, so team 1 ranks above team 0.
    # team 2: 10pts 2w      -> below both on wins
    # team 3: 7pts          -> last
    assert list(rank[0]) == [2, 1, 3, 4]


def test_rank_is_a_permutation_for_every_iteration():
    rng = np.random.default_rng(0)
    points = rng.integers(0, 40, size=(50, 20)).astype(float)
    zeros = np.zeros((50, 20))

    rank = rank_tables(points, zeros, zeros, zeros)

    for row in rank:
        assert sorted(row) == list(range(1, 21))


def test_batch_shapes_follow_the_baseline():
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    outcome = simulate_batch(baseline, iterations=7, rng=np.random.default_rng(1))

    assert isinstance(outcome, BatchOutcome)
    assert outcome.rank.shape == (7, len(baseline.teams))
    assert outcome.home_goals.shape == (7, len(baseline.lam_home))


@pytest.mark.slow  # 1500 iterations per side just to get a stable champion share
def test_both_strategies_agree_on_the_distribution():
    """The fixture loop and the fully vectorised draw are the same model; over
    enough iterations their champion distributions must agree.

    1500 iterations per side is a cost/power trade-off: both paths are fast
    (no DataFrame or SQL round trip), so this still runs in a couple of
    seconds. A 0.06 tolerance is roughly the 2-sigma band on a proportion at
    this sample size for the more common champions; do not tighten it without
    re-deriving that band, and do not loosen it further - a coarse divergence
    (e.g. swapping which array feeds home vs away goals) must still trip it.
    """
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    looped = simulate_batch(baseline, 1500, np.random.default_rng(2), vectorise_fixtures=False)
    vector = simulate_batch(baseline, 1500, np.random.default_rng(3), vectorise_fixtures=True)

    looped_share = (looped.rank == 1).mean(axis=0)
    vector_share = (vector.rank == 1).mean(axis=0)

    assert np.abs(looped_share - vector_share).max() < 0.06


def test_rank_tables_reproduces_standings_sql_exactly():
    """The strongest check available: same inputs, same ordering, row for row.

    This is exact rather than statistical - it removes ranking as a possible
    source of difference between the two paths, leaving only the random draws.
    """
    fixtures, remaining, _, adapter = _setup()
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    sql_table = adapter.get_brasileirao_standings(simulated)

    teams = list(sql_table["team_name"])
    ours = rank_tables(
        sql_table["p"].to_numpy(dtype=float)[None, :],
        sql_table["w"].to_numpy(dtype=float)[None, :],
        sql_table["gf"].to_numpy(dtype=float)[None, :],
        sql_table["ga"].to_numpy(dtype=float)[None, :],
    )[0]

    by_our_rank = [team for _, team in sorted(zip(ours, teams))]
    assert by_our_rank == teams, "vectorised ranking disagrees with standings.sql"


def test_simulated_seasons_are_complete():
    """Every team must end on 38 games: baseline played + simulated remaining."""
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)
    played_per_team = np.bincount(
        np.concatenate([baseline.home_team, baseline.away_team]),
        minlength=len(baseline.teams),
    )
    season_rows = fixtures[(fixtures["season"] == 2026) & fixtures["goals_for"].notnull()]
    already = season_rows.groupby("team_name").size().reindex(baseline.teams).to_numpy()

    assert (played_per_team + already == 38).all()

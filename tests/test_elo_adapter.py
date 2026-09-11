"""elo-adapter gates: `TeamStrength.lambdas_for`, `build_baseline`'s optional
`team_strength` (both the untouched-by-default path and the Elo-driven
override), and `EloAdapter` itself.
"""

import duckdb
import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.elo_adapter import EloAdapter
from brasileirao_simulator.domain.batch_simulation import SeasonBaseline, build_baseline
from brasileirao_simulator.domain.elo import EloParams, replay
from brasileirao_simulator.domain.elo_lambda import (
    DifferenceMap,
    EloLambdaParams,
    TeamStrength,
    fit_difference_map,
    lambdas,
    team_strength_as_of,
)
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.simulators import SIMULATORS

from test_equivalence_gates import _baseline_lambdas_by_fixture

SEASON = 2025
AS_OF = "2025-08-01"


# ---------------------------------------------------------------------------
# TeamStrength.lambdas_for - synthetic frame.
# ---------------------------------------------------------------------------


def _synthetic_frame() -> pd.DataFrame:
    """Three clubs: 1 and 2 have a real total, 3's total is NaN (no
    total-goals-window match, `team_strength_with_totals`'s own contract)."""
    return pd.DataFrame(
        {
            "team_id": [1, 2, 3],
            "as_of_date": ["2025-01-01"] * 3,
            "elo": [1600.0, 1500.0, 1400.0],
            "total": [2.5, 2.2, np.nan],
            "matches_used": [10, 8, 5],
            "competitions_used": ["71", "71", "71"],
        }
    )


def _synthetic_team_strength() -> TeamStrength:
    difference_map = DifferenceMap(slope=0.01, intercept=0.1, fitted_on_season=2019, n_matches=100)
    return TeamStrength(frame=_synthetic_frame(), difference_map=difference_map, params=EloLambdaParams())


def test_lambdas_for_reproduces_lambdas_for_two_known_clubs():
    strength = _synthetic_team_strength()

    got = strength.lambdas_for(1, 2)
    expected = lambdas(1600.0, 1500.0, 2.5, 2.2, strength.difference_map, strength.params, False)
    assert got == expected

    got_reversed = strength.lambdas_for(2, 1)
    expected_reversed = lambdas(1500.0, 1600.0, 2.2, 2.5, strength.difference_map, strength.params, False)
    assert got_reversed == expected_reversed


def test_lambdas_for_passes_is_neutral_through():
    strength = _synthetic_team_strength()

    got = strength.lambdas_for(1, 2, is_neutral=True)
    expected = lambdas(1600.0, 1500.0, 2.5, 2.2, strength.difference_map, strength.params, True)
    assert got == expected
    assert got != strength.lambdas_for(1, 2, is_neutral=False)


def test_lambdas_for_absent_club_returns_none():
    strength = _synthetic_team_strength()
    assert strength.lambdas_for(1, 999) is None
    assert strength.lambdas_for(999, 1) is None


def test_lambdas_for_nan_total_returns_none():
    strength = _synthetic_team_strength()
    assert strength.lambdas_for(1, 3) is None
    assert strength.lambdas_for(3, 1) is None


# ---------------------------------------------------------------------------
# build_baseline: team_strength=None is bit-identical to today's path.
# ---------------------------------------------------------------------------


def _real_fixtures_and_team_params(season: int, as_of_date: str):
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    remaining_games = tables.remaining_games(blank_from_date=as_of_date)

    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    team_params = con.sql(Queries(season).team_params_same_venue_average()).df()
    return fixtures, remaining_games, team_params


def test_build_baseline_team_strength_none_is_bit_identical_to_the_implicit_default():
    lam_home_implicit, lam_away_implicit = _baseline_lambdas_by_fixture(SEASON, AS_OF)

    fixtures, remaining_games, team_params = _real_fixtures_and_team_params(SEASON, AS_OF)
    baseline = build_baseline(fixtures, remaining_games, team_params, SEASON, team_strength=None)
    lam_home_explicit = pd.Series(baseline.lam_home, index=baseline.fixture_id).sort_index()
    lam_away_explicit = pd.Series(baseline.lam_away, index=baseline.fixture_id).sort_index()

    assert len(lam_home_implicit) > 0
    assert list(lam_home_implicit.index) == list(lam_home_explicit.index)
    assert np.array_equal(lam_home_implicit.to_numpy(), lam_home_explicit.to_numpy())
    assert np.array_equal(lam_away_implicit.to_numpy(), lam_away_explicit.to_numpy())
    assert baseline.lambda_fallbacks == 0


def test_season_baseline_construction_without_lambda_fallbacks_still_works():
    """Every existing `SeasonBaseline(...)` call site omits the new field -
    it must still construct, defaulting `lambda_fallbacks` to 0."""
    baseline = SeasonBaseline(
        teams=["A", "B"],
        points=np.zeros(2),
        wins=np.zeros(2),
        goals_for=np.zeros(2),
        goals_against=np.zeros(2),
        home_team=np.array([0]),
        away_team=np.array([1]),
        lam_home=np.array([1.0]),
        lam_away=np.array([1.0]),
        fixture_id=np.array([1]),
        round_=np.array([1]),
        home_name=["A"],
        away_name=["B"],
        played_home_name=[],
        played_away_name=[],
        played_home_goals=np.array([]),
        played_away_goals=np.array([]),
        played_round_=np.array([]),
        home_attack=np.array([1.0, 1.0]),
        home_defence=np.array([1.0, 1.0]),
        away_attack=np.array([1.0, 1.0]),
        away_defence=np.array([1.0, 1.0]),
        home_match_count=np.array([19, 19]),
        away_match_count=np.array([19, 19]),
    )
    assert baseline.lambda_fallbacks == 0


# ---------------------------------------------------------------------------
# build_baseline: a real TeamStrength actually changes the lambdas.
# ---------------------------------------------------------------------------


def _real_team_strength(season: int, as_of_date: str):
    store = MatchStore()
    history = replay(store, EloParams())
    # The adapter's default: the line fitted on the season before `season`.
    lambda_params = EloLambdaParams(burn_in_season=season - 1)
    difference_map = fit_difference_map(history, store, lambda_params)

    # Same frontier/cutoff derivation as DixonColesAllAdapter.fit_ratings /
    # EloAdapter.build_baseline: the day after the as-of date, since
    # MatchStore.before is strictly "<".
    cutoff = str((pd.Timestamp(as_of_date) + pd.Timedelta(days=1)).date())
    strength = team_strength_as_of(history, store, cutoff, difference_map, lambda_params)
    return strength, lambda_params


def test_build_baseline_with_real_team_strength_differs_and_keeps_prediction_set():
    strength, lambda_params = _real_team_strength(SEASON, AS_OF)
    fixtures, remaining_games, team_params = _real_fixtures_and_team_params(SEASON, AS_OF)

    incumbent = build_baseline(fixtures, remaining_games, team_params, SEASON)
    elo_baseline = build_baseline(
        fixtures, remaining_games, team_params, SEASON, team_strength=strength
    )

    assert np.array_equal(elo_baseline.fixture_id, incumbent.fixture_id)
    assert np.any(np.abs(elo_baseline.lam_home - incumbent.lam_home) > 1e-9) or np.any(
        np.abs(elo_baseline.lam_away - incumbent.lam_away) > 1e-9
    )
    assert np.all(elo_baseline.lam_home >= lambda_params.eps)
    assert np.all(elo_baseline.lam_away >= lambda_params.eps)

    if elo_baseline.lambda_fallbacks:
        pairs = fixtures[["team_name", "team_id"]].drop_duplicates(subset="team_name")
        id_by_name = dict(zip(pairs["team_name"], pairs["team_id"]))
        resolved = set(strength.frame.loc[strength.frame["total"].notna(), "team_id"])
        unresolved = sorted(
            name
            for name in set(elo_baseline.home_name) | set(elo_baseline.away_name)
            if id_by_name.get(name) not in resolved
        )
        pytest.fail(
            f"{elo_baseline.lambda_fallbacks} fixture(s) fell back to the incumbent lambda; "
            f"club(s) that did not resolve in TeamStrength: {unresolved}"
        )


# ---------------------------------------------------------------------------
# EloAdapter itself.
# ---------------------------------------------------------------------------


def test_elo_adapter_build_baseline_on_a_real_date():
    tables = Tables(SeasonData(SEASON))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    remaining_games = tables.remaining_games(blank_from_date=AS_OF)

    elo_baseline = EloAdapter("batch", SEASON).build_baseline(fixtures, remaining_games)

    incumbent = IterationBatchAdapter("average", SEASON)
    incumbent.con.register("new_fixtures", fixtures)
    team_params = incumbent.con.sql(incumbent.queries.team_params_same_venue_average()).df()
    incumbent_baseline = build_baseline(fixtures, remaining_games, team_params, SEASON)

    assert np.array_equal(elo_baseline.fixture_id, incumbent_baseline.fixture_id)
    assert np.any(np.abs(elo_baseline.lam_home - incumbent_baseline.lam_home) > 1e-9)

    print(
        "incumbent mean lam_home="
        f"{incumbent_baseline.lam_home.mean():.4f} lam_away={incumbent_baseline.lam_away.mean():.4f}"
    )
    print(
        "elo       mean lam_home="
        f"{elo_baseline.lam_home.mean():.4f} lam_away={elo_baseline.lam_away.mean():.4f}"
    )


def test_elo_is_registered_in_simulators():
    assert SIMULATORS["elo"] is EloAdapter


def test_adapter_fits_the_line_on_the_previous_season_by_default():
    store = MatchStore()
    adapter = EloAdapter("average", 2025, match_store=store)
    assert adapter.lambda_params.burn_in_season == 2024
    assert adapter.difference_map.fitted_on_season == 2024


def test_adapter_keeps_a_pinned_line_season():
    store = MatchStore()
    adapter = EloAdapter("average", 2025, match_store=store, lambda_params=EloLambdaParams(burn_in_season=2019))
    assert adapter.difference_map.fitted_on_season == 2019

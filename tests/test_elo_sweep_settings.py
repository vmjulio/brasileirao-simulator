"""The three settings elo-sweeps adds - per-competition weight, season
regression, totals window. Each must leave today's behaviour bit-identical at
its default and do exactly what it says away from it."""

import pandas as pd
import pytest

from brasileirao_simulator.domain.elo import EloParams, replay
from brasileirao_simulator.domain.elo_lambda import EloLambdaParams
from brasileirao_simulator.domain.elo_total_goals import total_goals_params
from brasileirao_simulator.domain.match_store import MatchStore

A, B, C = 101, 202, 303

_DTYPES = {"fixture_id": "int64", "season": "int64", "league_id": "int64", "home_id": "int64",
           "away_id": "int64", "home_goals": "int64", "away_goals": "int64", "is_neutral": "bool"}


def _row(fixture_id, date, home, away, hg, ag, season=2024, league=71):
    return {"fixture_id": fixture_id, "fixture_date": f"{date}T20:00:00+00:00", "season": season,
            "league_id": league, "round": "Regular Season - 1", "home_id": home, "away_id": away,
            "home_name": str(home), "away_name": str(away), "home_goals": hg, "away_goals": ag,
            "status": "FT", "is_neutral": False}


def _store(rows):
    frame = pd.DataFrame(rows)
    for column, dtype in _DTYPES.items():
        frame[column] = frame[column].astype(dtype)
    store = MatchStore.__new__(MatchStore)
    store._matches = frame
    return store


@pytest.fixture(autouse=True)
def _seed_by_season(monkeypatch):
    """Clubs seed at 1500 in 2024 and 1400 in 2025, so a regression target
    that depends on the NEXT season's division is observable."""
    monkeypatch.setattr(
        "brasileirao_simulator.domain.elo.seed_for",
        lambda team_id, season, store, params: 1500.0 if season == 2024 else 1400.0,
    )


def _two_seasons():
    return _store([
        _row(1, "2024-05-01", A, B, 3, 0),
        _row(2, "2024-06-01", B, C, 2, 1, league=73),
        _row(3, "2024-07-01", C, A, 0, 1),
        _row(4, "2025-05-01", A, C, 1, 1, season=2025),
        _row(5, "2025-06-01", B, A, 0, 2, season=2025, league=13),
    ])


def _rating(history, team, fixture, column="elo_after"):
    return history.ratings.query("team_id == @team and fixture_id == @fixture")[column].iloc[0]


def test_defaults_are_neutral():
    params = EloParams()
    assert dict(params.competition_weight) == {}
    assert params.season_regression == 0.0
    assert EloLambdaParams().totals_window_days == 365


def test_explicit_unit_weights_are_bit_identical_to_the_default():
    store = _two_seasons()
    default = replay(store, EloParams()).ratings
    unit = replay(store, EloParams(competition_weight={71: 1.0, 73: 1.0, 13: 1.0})).ratings
    pd.testing.assert_frame_equal(default, unit)


def test_weight_scales_only_its_own_competitions_updates():
    store = _two_seasons()
    default = replay(store, EloParams())
    halved = replay(store, EloParams(competition_weight={73: 0.5}))
    d_cup = _rating(default, B, 2) - _rating(default, B, 2, "elo_before")
    h_cup = _rating(halved, B, 2) - _rating(halved, B, 2, "elo_before")
    assert h_cup == pytest.approx(d_cup / 2)
    d_league = _rating(default, A, 1) - _rating(default, A, 1, "elo_before")
    h_league = _rating(halved, A, 1) - _rating(halved, A, 1, "elo_before")
    assert h_league == pytest.approx(d_league)


def test_weight_zero_means_the_competition_never_moves_a_rating():
    store = _two_seasons()
    frozen = replay(store, EloParams(competition_weight={73: 0.0})).ratings
    cup = frozen[frozen["league_id"] == 73]
    assert (cup["elo_after"] == cup["elo_before"]).all()


def test_zero_regression_is_bit_identical_to_the_default():
    store = _two_seasons()
    pd.testing.assert_frame_equal(
        replay(store, EloParams()).ratings, replay(store, EloParams(season_regression=0.0)).ratings
    )


def test_regression_moves_a_clubs_last_rating_of_a_season_toward_next_seasons_seed():
    store = _two_seasons()
    plain = replay(store, EloParams())
    shrunk = replay(store, EloParams(season_regression=0.25))
    # A's last 2024 match is fixture 3; its 2025 seed (the target) is 1400.
    unshrunk = _rating(plain, A, 3)
    assert _rating(shrunk, A, 3) == pytest.approx(unshrunk + 0.25 * (1400.0 - unshrunk))


def test_regression_is_what_the_next_season_starts_from():
    shrunk = replay(_two_seasons(), EloParams(season_regression=0.25))
    assert _rating(shrunk, A, 4, "elo_before") == pytest.approx(_rating(shrunk, A, 3))


def test_regression_leaves_matches_before_a_clubs_last_of_the_season_alone():
    store = _two_seasons()
    plain = replay(store, EloParams()).ratings
    shrunk = replay(store, EloParams(season_regression=0.25)).ratings
    first = lambda r: r.query("fixture_id == 1").reset_index(drop=True)
    pd.testing.assert_frame_equal(first(plain), first(shrunk))


def test_no_regression_without_a_later_season_to_regress_toward():
    one_season = _store([
        _row(1, "2024-05-01", A, B, 3, 0),
        _row(2, "2024-06-01", B, C, 2, 1),
        _row(3, "2024-07-01", C, A, 0, 1),
    ])
    pd.testing.assert_frame_equal(
        replay(one_season, EloParams()).ratings,
        replay(one_season, EloParams(season_regression=0.25)).ratings,
    )


def _totals_store():
    return _store([
        _row(1, "2025-01-10", A, B, 4, 3),   # ~140 days before the cutoff
        _row(2, "2025-05-20", A, C, 1, 0),   # ~12 days before
        _row(3, "2025-05-25", B, C, 0, 0),
    ])


def test_totals_window_default_matches_the_explicit_365():
    store = _totals_store()
    assert total_goals_params(store, "2025-06-01") == total_goals_params(store, "2025-06-01", window_days=365)


def test_a_shorter_totals_window_drops_older_matches():
    store = _totals_store()
    wide = total_goals_params(store, "2025-06-01", window_days=365)
    narrow = total_goals_params(store, "2025-06-01", window_days=30)
    # The 7-goal match is outside 30 days, so the fitted mean total falls.
    assert sum(narrow.values()) < sum(wide.values())


# --- elo-sudeste-gap ---------------------------------------------------------

from brasileirao_simulator.domain.elo_lambda import DifferenceMap, TeamStrength, lambdas  # noqa: E402

SE, NE, S_ = 1, 2, 3


def _strength(gap, regions):
    frame = pd.DataFrame({"team_id": [SE, NE, S_], "elo": [1600.0, 1600.0, 1600.0], "total": [1.3, 1.3, 1.3]})
    dmap = DifferenceMap(slope=0.004, intercept=0.1, fitted_on_season=2025, n_matches=100)
    return TeamStrength(frame=frame, difference_map=dmap, params=EloLambdaParams(sudeste_gap=gap), regions=regions)


REGIONS = {SE: "Sudeste", NE: "Nordeste", S_: "Sul"}


def test_sudeste_gap_off_is_bit_identical():
    off = _strength(0.0, REGIONS)
    plain = TeamStrength(frame=off.frame, difference_map=off.difference_map, params=EloLambdaParams())
    assert off.lambdas_for(SE, NE) == plain.lambdas_for(SE, NE)


def test_sudeste_gap_equals_that_many_extra_rating_points():
    on = _strength(50.0, REGIONS)
    expected = lambdas(1650.0, 1600.0, 1.3, 1.3, on.difference_map, EloLambdaParams(sudeste_gap=50.0))
    assert on.lambdas_for(SE, NE) == pytest.approx(expected)


def test_sudeste_gap_favours_the_sudeste_side_either_way_round():
    on, off = _strength(50.0, REGIONS), _strength(0.0, REGIONS)
    assert on.lambdas_for(SE, NE)[0] > off.lambdas_for(SE, NE)[0]   # Sudeste at home: home rate up
    assert on.lambdas_for(NE, SE)[0] < off.lambdas_for(NE, SE)[0]   # Sudeste away: home rate down


def test_sudeste_gap_leaves_other_pairings_and_unknown_regions_alone():
    on, off = _strength(50.0, REGIONS), _strength(50.0, {SE: "Sudeste"})
    plain = _strength(0.0, REGIONS)
    assert on.lambdas_for(NE, S_) == plain.lambdas_for(NE, S_)       # neither is Sudeste
    assert off.lambdas_for(SE, NE) == plain.lambdas_for(SE, NE)      # away club's region unknown

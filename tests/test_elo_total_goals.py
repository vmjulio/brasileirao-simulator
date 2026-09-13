"""Behaviour gates for the elo-total-goals ticket: `total_goals_params` and
`team_strength_with_totals` in `domain/elo_total_goals.py`. See that
module's docstring for the fixed point and the re-centring choice these
tests pin.

Stores are hand-built the same way tests/test_elo_replay.py builds them:
`MatchStore.__new__` skips `__init__` (which reads CSV shards off disk) and
we set `_matches` directly to a small DataFrame with exactly the columns
`MatchStore.matches` contracts for.
"""

import pandas as pd
import pytest

from brasileirao_simulator.domain.elo import EloHistory, EloParams
from brasileirao_simulator.domain.elo_snapshots import team_strength
from brasileirao_simulator.domain.elo_total_goals import (
    TOTAL_GOALS_WINDOW_DAYS,
    team_strength_with_totals,
    total_goals_params,
)
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.season_dates import utc_cutoff

LEAGUE_ID = 71
SEASON = 2025

_ID_DTYPES = {
    "fixture_id": "int64",
    "season": "int64",
    "league_id": "int64",
    "home_id": "int64",
    "away_id": "int64",
    "home_goals": "int64",
    "away_goals": "int64",
    "is_neutral": "bool",
}

_COLUMNS = [
    "fixture_id",
    "fixture_date",
    "season",
    "league_id",
    "round",
    "home_id",
    "away_id",
    "home_name",
    "away_name",
    "home_goals",
    "away_goals",
    "status",
    "is_neutral",
]


def _row(
    fixture_id,
    fixture_date,
    home_id,
    away_id,
    home_goals,
    away_goals,
    season=SEASON,
    league_id=LEAGUE_ID,
    status="FT",
) -> dict:
    return {
        "fixture_id": fixture_id,
        "fixture_date": fixture_date,
        "season": season,
        "league_id": league_id,
        "round": "Regular Season - 1",
        "home_id": home_id,
        "away_id": away_id,
        "home_name": "Home FC",
        "away_name": "Away FC",
        "home_goals": home_goals,
        "away_goals": away_goals,
        "status": status,
        "is_neutral": False,
    }


def _store(rows: list) -> MatchStore:
    """A `MatchStore` whose `.matches` is exactly `rows`, without touching
    disk - same helper pattern as tests/test_elo_replay.py. `columns=_COLUMNS`
    keeps the frame properly shaped even for an empty `rows` list, where
    `pd.DataFrame([])` alone would produce zero columns."""
    frame = pd.DataFrame(rows, columns=_COLUMNS)
    for column, dtype in _ID_DTYPES.items():
        frame[column] = frame[column].astype(dtype)
    store = MatchStore.__new__(MatchStore)
    store._matches = frame
    return store


# ---------------------------------------------------------------------------
# (a) round-robin, every match totals exactly 3 goals -> every club c = 1.5
# ---------------------------------------------------------------------------


def test_every_club_gets_1_5_when_every_match_totals_3_goals():
    rows = [
        _row(1, "2025-01-01T00:00:00+00:00", 1, 2, 2, 1),
        _row(2, "2025-01-02T00:00:00+00:00", 2, 3, 1, 2),
        _row(3, "2025-01-03T00:00:00+00:00", 3, 1, 3, 0),
    ]
    result = total_goals_params(_store(rows), "2025-06-01")

    assert result == pytest.approx({1: 1.5, 2: 1.5, 3: 1.5})


# ---------------------------------------------------------------------------
# (b) opponent adjustment: one high-scoring club, re-centring holds exactly
# ---------------------------------------------------------------------------

TEAM_X, TEAM_A, TEAM_B, TEAM_C, TEAM_D = 900, 901, 902, 903, 904


def _opponent_adjustment_rows():
    return [
        # TEAM_X's matches all total 5 goals.
        _row(1, "2025-01-01T00:00:00+00:00", TEAM_X, TEAM_A, 3, 2),
        _row(2, "2025-01-02T00:00:00+00:00", TEAM_X, TEAM_B, 4, 1),
        _row(3, "2025-01-03T00:00:00+00:00", TEAM_X, TEAM_C, 2, 3),
        _row(4, "2025-01-04T00:00:00+00:00", TEAM_X, TEAM_D, 5, 0),
        # A/B/C/D's other matches (never involving TEAM_X) all total 2 goals.
        _row(5, "2025-01-05T00:00:00+00:00", TEAM_A, TEAM_B, 1, 1),
        _row(6, "2025-01-06T00:00:00+00:00", TEAM_B, TEAM_C, 2, 0),
        _row(7, "2025-01-07T00:00:00+00:00", TEAM_C, TEAM_D, 0, 2),
        _row(8, "2025-01-08T00:00:00+00:00", TEAM_D, TEAM_A, 1, 1),
        _row(9, "2025-01-09T00:00:00+00:00", TEAM_A, TEAM_C, 2, 0),
        _row(10, "2025-01-10T00:00:00+00:00", TEAM_B, TEAM_D, 1, 1),
    ]


def test_high_scoring_club_gets_a_higher_contribution_than_its_opponents():
    result = total_goals_params(_store(_opponent_adjustment_rows()), "2025-06-01")

    assert result[TEAM_X] > result[TEAM_A]
    assert result[TEAM_X] > result[TEAM_B]
    assert result[TEAM_X] > result[TEAM_C]
    assert result[TEAM_X] > result[TEAM_D]


def test_recentring_holds_mean_of_fitted_matches_equals_observed_mean_exactly():
    store = _store(_opponent_adjustment_rows())
    result = total_goals_params(store, "2025-06-01")

    windowed = store.before("2025-06-01")
    observed_mean = (windowed["home_goals"] + windowed["away_goals"]).mean()
    fitted_mean = (
        windowed["home_id"].map(result) + windowed["away_id"].map(result)
    ).mean()

    assert fitted_mean == pytest.approx(observed_mean, abs=1e-9)


# ---------------------------------------------------------------------------
# (c) leakage and window exclusion
# ---------------------------------------------------------------------------


def test_matches_on_or_after_as_of_date_are_excluded():
    rows = [
        _row(1, "2025-01-01T00:00:00+00:00", 1, 2, 2, 1),
        # Played on the as-of date itself: 19:00 in Brazil, 22:00 UTC.
        _row(2, "2025-06-01T22:00:00+00:00", 1, 2, 10, 10),
    ]
    result = total_goals_params(_store(rows), "2025-06-01")

    # Only fixture 1 (total 3) is admitted -> both clubs land at 1.5, not the
    # value fixture 2's 20-goal total would pull them toward.
    assert result == pytest.approx({1: 1.5, 2: 1.5})


def test_matches_older_than_the_window_are_excluded():
    as_of_date = "2025-06-01"
    cutoff = utc_cutoff(as_of_date)
    just_inside = cutoff - pd.Timedelta(days=TOTAL_GOALS_WINDOW_DAYS - 1)
    just_outside = cutoff - pd.Timedelta(days=TOTAL_GOALS_WINDOW_DAYS + 1)

    rows = [
        _row(1, just_inside.isoformat(), 1, 2, 2, 1),
        # Old match, way outside the window, total goals 20 - if admitted it
        # would drag both clubs' contribution far above 1.5.
        _row(2, just_outside.isoformat(), 1, 2, 15, 5),
    ]
    result = total_goals_params(_store(rows), as_of_date)

    assert result == pytest.approx({1: 1.5, 2: 1.5})


# ---------------------------------------------------------------------------
# (d) a club with no match in the window is absent
# ---------------------------------------------------------------------------


def test_club_with_no_match_in_window_is_absent():
    rows = [_row(1, "2025-01-01T00:00:00+00:00", 1, 2, 2, 1)]
    result = total_goals_params(_store(rows), "2025-06-01")

    assert set(result) == {1, 2}
    assert 3 not in result


def test_empty_store_returns_empty_dict():
    result = total_goals_params(_store([]), "2025-06-01")
    assert result == {}


# ---------------------------------------------------------------------------
# (e) team_strength_with_totals
# ---------------------------------------------------------------------------

D1 = "2025-03-01"
D2 = "2025-03-10"

_HISTORY_ROWS = [
    # fixture_id, fixture_date, league_id, team_id, elo_before, elo_after, opponent_id, is_home, matches_used
    (1, D1, LEAGUE_ID, 1, 1500.0, 1516.0, 2, True, 0),
    (1, D1, LEAGUE_ID, 2, 1500.0, 1484.0, 1, False, 0),
]


def _history() -> EloHistory:
    frame = pd.DataFrame(
        _HISTORY_ROWS,
        columns=[
            "fixture_id",
            "fixture_date",
            "league_id",
            "team_id",
            "elo_before",
            "elo_after",
            "opponent_id",
            "is_home",
            "matches_used",
        ],
    )
    frame = frame.assign(season=SEASON, is_neutral=False)
    return EloHistory(frame, EloParams())


def test_team_strength_with_totals_columns_in_order():
    history = _history()
    store = _store([_row(1, D1, 1, 2, 2, 1)])

    frame = team_strength_with_totals(history, store, D2)

    assert list(frame.columns) == [
        "team_id",
        "as_of_date",
        "elo",
        "total",
        "matches_used",
        "competitions_used",
    ]
    assert frame["total"].dtype == "float64"


def test_team_strength_with_totals_nan_for_a_club_absent_from_totals():
    history = _history()
    as_of_date = D2
    # Club 1 (present in the Elo history) has a recent match against a third
    # club not in the Elo history at all, so it gets a total. Club 2 (also
    # present in the Elo history via fixture 1) has no match anywhere in the
    # totals store, so it is absent from `total_goals_params` and must come
    # back NaN rather than being dropped from the combined frame.
    store = _store([_row(1, "2025-03-05T00:00:00+00:00", 1, 5, 2, 1)])

    frame = team_strength_with_totals(history, store, as_of_date).set_index("team_id")

    assert frame.loc[1, "total"] == pytest.approx(1.5)
    assert frame.loc[2, "total"] != frame.loc[2, "total"]  # NaN


def test_team_strength_with_totals_elo_and_matches_used_match_team_strength():
    history = _history()
    store = _store([_row(1, D1, 1, 2, 2, 1)])

    plain = team_strength(history, D2).set_index("team_id")
    combined = team_strength_with_totals(history, store, D2).set_index("team_id")

    pd.testing.assert_series_equal(combined["elo"], plain["elo"])
    pd.testing.assert_series_equal(combined["matches_used"], plain["matches_used"])


# ---------------------------------------------------------------------------
# Real-store gate.
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("season", [2023, 2024, 2025])
def test_league_mean_total_matches_observed_within_2_percent(season):
    store = MatchStore()
    season_matches = store.matches[
        (store.matches["season"] == season) & (store.matches["league_id"] == LEAGUE_ID)
    ]
    # "The season's last date + 1 day" (the gate's own wording, see
    # `total_goals_params`'s original contract) rather than a fixed
    # `f"{season + 1}-01-01"` proxy: Série A's last matchday is early
    # December, so Jan 1 pulls in an extra 3-4 off-season weeks at the head
    # of the window - mostly quiet, but for 2025 specifically it drags the
    # fit just outside the 2% band (measured: 2.07% off vs 1.92% off using
    # the precise last-date+1 cutoff). Last date + 1 day is both what the
    # ticket specifies and what actually holds the gate on real data.
    last_kickoff = pd.to_datetime(
        season_matches["fixture_date"], utc=True, format="mixed"
    ).max()
    as_of_date = (last_kickoff + pd.Timedelta(days=1)).isoformat()

    fitted = total_goals_params(store, as_of_date)

    observed_mean = (season_matches["home_goals"] + season_matches["away_goals"]).mean()
    fitted_mean = (
        season_matches["home_id"].map(fitted) + season_matches["away_id"].map(fitted)
    ).mean()

    print(f"season={season} observed_mean={observed_mean} fitted_mean={fitted_mean}")

    assert fitted_mean == pytest.approx(observed_mean, rel=0.02)

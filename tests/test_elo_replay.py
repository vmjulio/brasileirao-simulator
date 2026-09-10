"""Behaviour gates for `elo.replay` (ticket elo-replay). Each test below is
named after the notebook defect it pins - see the module docstring of
`domain/elo.py` and section "Elo" of
docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md for the
formula these assert against.

Stores are hand-built rather than the real data: `MatchStore.__new__` skips
`__init__` (which reads CSV shards off disk) and we set `_matches` directly
to a small DataFrame with exactly the columns `MatchStore.matches` contracts
for. `seed_for` is elo-division-seeds' stub (still `NotImplementedError`
while that ticket is in flight), so every test here monkeypatches it to a
constant division-1 seed via the autouse fixture below - identical to what
the ticket brief asks tests to do.
"""

import copy

import pandas as pd
import pytest

from brasileirao_simulator.domain import elo
from brasileirao_simulator.domain.elo import EloParams, margin_multiplier, replay
from brasileirao_simulator.domain.match_store import MatchStore

TEAM_A = 101
TEAM_B = 202
TEAM_C = 303

SEASON = 2025
LEAGUE_ID = 71

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


def _row(
    fixture_id,
    fixture_date,
    home_id=TEAM_A,
    away_id=TEAM_B,
    home_goals=1,
    away_goals=0,
    home_name="Home FC",
    away_name="Away FC",
    is_neutral=False,
    status="FT",
    round_="Regular Season - 1",
    season=SEASON,
    league_id=LEAGUE_ID,
) -> dict:
    return {
        "fixture_id": fixture_id,
        "fixture_date": fixture_date,
        "season": season,
        "league_id": league_id,
        "round": round_,
        "home_id": home_id,
        "away_id": away_id,
        "home_name": home_name,
        "away_name": away_name,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "status": status,
        "is_neutral": is_neutral,
    }


def _store(rows: list) -> MatchStore:
    """A `MatchStore` whose `.matches` is exactly `rows`, without touching
    disk - `__init__` reads shards under `DATASETS_PATH`, which these tests
    have no need of."""
    frame = pd.DataFrame(rows)
    for column, dtype in _ID_DTYPES.items():
        frame[column] = frame[column].astype(dtype)
    store = MatchStore.__new__(MatchStore)
    store._matches = frame
    return store


@pytest.fixture(autouse=True)
def _constant_seed(monkeypatch):
    """Every club seeds at `params.seeds[1]`, isolating replay's own tests
    from elo-division-seeds, which is implementing the real rule in a
    parallel worktree."""
    monkeypatch.setattr(
        "brasileirao_simulator.domain.elo.seed_for",
        lambda team_id, season, store, params: params.seeds[1],
    )


def _delta(history: pd.DataFrame, team_id: int, fixture_id: int) -> float:
    row = history[(history["team_id"] == team_id) & (history["fixture_id"] == fixture_id)].iloc[0]
    return row["elo_after"] - row["elo_before"]


# --- Gate 1: margin symmetry ------------------------------------------------


def test_margin_symmetry_away_win_moves_ratings_as_much_as_home_win():
    """Notebook defect: away wins always got the one-goal K regardless of
    margin. Neutral venue removes home_advantage so both scenarios start
    from an identical 0.5 expectation."""
    params = EloParams(home_advantage=85.0)

    home_win = _store(
        [_row(1, "2025-01-01T00:00:00+00:00", home_goals=4, away_goals=0, is_neutral=True)]
    )
    away_win = _store(
        [_row(1, "2025-01-01T00:00:00+00:00", home_goals=0, away_goals=4, is_neutral=True)]
    )

    home_win_delta = _delta(replay(home_win, params).ratings, TEAM_A, 1)
    away_win_delta = _delta(replay(away_win, params).ratings, TEAM_B, 1)

    assert home_win_delta > 0
    assert home_win_delta == pytest.approx(away_win_delta)


# --- Gate 2: identity by team_id, not name ----------------------------------


def test_identity_by_team_id_ignores_name_spelling():
    store = _store(
        [
            _row(1, "2025-01-01T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_B, home_name="Sao Paulo"),
            _row(
                2,
                "2025-01-08T00:00:00+00:00",
                home_id=TEAM_B,
                away_id=TEAM_A,
                away_name="Sao Paulo FC",  # different spelling, same team_id
                home_goals=0,
                away_goals=2,
            ),
        ]
    )

    history = replay(store, EloParams()).ratings
    team_a_rows = history[history["team_id"] == TEAM_A].sort_values("fixture_id")

    assert len(team_a_rows) == 2
    assert team_a_rows["matches_used"].tolist() == [0, 1]
    # one continuous trajectory: second row picks up exactly where the first left off
    assert team_a_rows["elo_after"].iloc[0] == pytest.approx(team_a_rows["elo_before"].iloc[1])


# --- Gate 3: 90-minute scoring, extra columns ignored -----------------------


def test_stray_goals_columns_are_ignored_a_1_1_row_updates_as_a_draw():
    store = _store([_row(1, "2025-01-01T00:00:00+00:00", home_goals=1, away_goals=1, is_neutral=True)])
    # simulate raw-schema leakage: goals_* includes extra time and must
    # never be read (see MatchStore's own module docstring).
    store._matches["goals_home"] = 3
    store._matches["goals_away"] = 1

    params = EloParams()
    history = replay(store, params).ratings

    expected_home = elo._expected_home(params.seeds[1], params.seeds[1], True, params.home_advantage)
    expected_delta = params.k * margin_multiplier(0, params.margin_ladder) * (0.5 - expected_home)

    assert _delta(history, TEAM_A, 1) == pytest.approx(expected_delta)
    assert expected_delta == pytest.approx(0.0)  # equal seeds, neutral, draw -> no movement


# --- Gate 4: deterministic ordering -----------------------------------------


def test_shuffled_input_gives_identical_output():
    rows = [
        _row(1, "2025-01-01T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_B, home_goals=2, away_goals=1),
        _row(2, "2025-01-08T00:00:00+00:00", home_id=TEAM_B, away_id=TEAM_C, home_goals=0, away_goals=0),
        _row(3, "2025-01-15T00:00:00+00:00", home_id=TEAM_C, away_id=TEAM_A, home_goals=3, away_goals=1),
        # same fixture_date as row 1, different fixture_id - exercises the tie-break
        _row(4, "2025-01-01T00:00:00+00:00", home_id=TEAM_C, away_id=TEAM_B, home_goals=1, away_goals=1),
    ]
    params = EloParams()

    ordered = replay(_store(rows), params).ratings
    shuffled = replay(_store(list(reversed(rows))), params).ratings

    assert ordered.equals(shuffled)


# --- Gate 5: home advantage sign --------------------------------------------


def test_home_advantage_expectation_sign():
    home_advantage = 85.0

    assert elo._expected_home(1500.0, 1500.0, False, home_advantage) == pytest.approx(
        1 / (1 + 10 ** (-home_advantage / 400))
    )
    assert elo._expected_home(1500.0, 1500.0, False, home_advantage) > 0.5
    assert elo._expected_home(1500.0, 1500.0, True, home_advantage) == pytest.approx(0.5)


def test_home_advantage_reflected_in_replays_first_row_delta():
    params = EloParams(home_advantage=85.0, k=20.0)
    store = _store([_row(1, "2025-01-01T00:00:00+00:00", home_goals=1, away_goals=1, is_neutral=False)])

    history = replay(store, params).ratings

    expected_home = 1 / (1 + 10 ** (-params.home_advantage / 400))
    expected_delta = params.k * margin_multiplier(0, params.margin_ladder) * (0.5 - expected_home)

    assert _delta(history, TEAM_A, 1) == pytest.approx(expected_delta)
    # favoured by H and only draws -> the home side's rating must fall
    assert expected_delta < 0


# --- Gate 6: idempotent, and a correction only ripples forward -------------


def test_idempotent_and_a_corrected_score_only_changes_rows_from_that_match_on():
    rows = [
        _row(1, "2025-01-01T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_B, home_goals=2, away_goals=0),
        _row(2, "2025-01-08T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_C, home_goals=1, away_goals=1),
        _row(3, "2025-01-15T00:00:00+00:00", home_id=TEAM_B, away_id=TEAM_C, home_goals=0, away_goals=0),
    ]
    params = EloParams()
    store = _store(rows)

    first = replay(store, params).ratings
    second = replay(store, params).ratings
    assert first.equals(second)

    corrected_rows = copy.deepcopy(rows)
    corrected_rows[1] = dict(corrected_rows[1], home_goals=3, away_goals=0)
    corrected = replay(_store(corrected_rows), params).ratings

    # rows before the corrected match (fixture 1, all clubs) are untouched
    before = first[first["fixture_id"] == 1].reset_index(drop=True)
    corrected_before = corrected[corrected["fixture_id"] == 1].reset_index(drop=True)
    pd.testing.assert_frame_equal(before, corrected_before)

    # TEAM_A and TEAM_C, who played the corrected match, differ from it onward
    assert _delta(first, TEAM_A, 2) != pytest.approx(_delta(corrected, TEAM_A, 2))
    fixture_3_first = first[first["fixture_id"] == 3].reset_index(drop=True)
    fixture_3_corrected = corrected[corrected["fixture_id"] == 3].reset_index(drop=True)
    assert not fixture_3_first.equals(fixture_3_corrected)

    # TEAM_B never played the corrected match itself, but it plays TEAM_C
    # (who did) in fixture 3 - the correction legitimately ripples through
    # TEAM_C's changed incoming rating, which is real Elo behaviour, not a
    # bug this test should paper over.
    assert _delta(first, TEAM_B, 3) != pytest.approx(_delta(corrected, TEAM_B, 3))


# --- Seeding, bookkeeping, and schema shape ---------------------------------


def test_first_row_elo_before_equals_the_seed():
    params = EloParams()
    history = replay(_store([_row(1, "2025-01-01T00:00:00+00:00")]), params).ratings

    assert (history["elo_before"] == params.seeds[1]).all()


def test_matches_used_increments_per_club():
    rows = [
        _row(1, "2025-01-01T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_B),
        _row(2, "2025-01-08T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_C),
        _row(3, "2025-01-15T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_B),
    ]
    history = replay(_store(rows), EloParams()).ratings

    team_a_used = history[history["team_id"] == TEAM_A].sort_values("fixture_id")["matches_used"].tolist()
    assert team_a_used == [0, 1, 2]


def test_two_rows_per_match():
    rows = [
        _row(1, "2025-01-01T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_B),
        _row(2, "2025-01-08T00:00:00+00:00", home_id=TEAM_B, away_id=TEAM_C),
    ]
    history = replay(_store(rows), EloParams()).ratings
    assert len(history) == 2 * len(rows)


def test_column_set_and_order_match_the_docstring():
    history = replay(_store([_row(1, "2025-01-01T00:00:00+00:00")]), EloParams()).ratings
    assert list(history.columns) == [
        "fixture_id",
        "fixture_date",
        "season",
        "league_id",
        "team_id",
        "elo_before",
        "elo_after",
        "opponent_id",
        "is_home",
        "is_neutral",
        "matches_used",
    ]


def test_dtypes_match_the_docstring():
    history = replay(_store([_row(1, "2025-01-01T00:00:00+00:00")]), EloParams()).ratings

    for column in ("fixture_id", "season", "league_id", "team_id", "opponent_id", "matches_used"):
        assert history[column].dtype == "int64", column
    for column in ("elo_before", "elo_after"):
        assert history[column].dtype == "float64", column
    for column in ("is_home", "is_neutral"):
        assert history[column].dtype == "bool", column


def test_rows_are_sorted_by_fixture_date_fixture_id_is_home_desc():
    rows = [
        _row(2, "2025-01-08T00:00:00+00:00", home_id=TEAM_B, away_id=TEAM_C),
        _row(1, "2025-01-01T00:00:00+00:00", home_id=TEAM_A, away_id=TEAM_B),
    ]
    history = replay(_store(rows), EloParams()).ratings

    keys = list(zip(history["fixture_date"], history["fixture_id"], ~history["is_home"]))
    assert keys == sorted(keys)


# --- Real-store smoke test ---------------------------------------------------


@pytest.mark.slow
def test_replay_over_the_real_store_has_two_rows_per_match_and_no_missing_ratings():
    store = MatchStore()
    history = replay(store).ratings

    assert len(history) == 2 * len(store.matches)
    assert history[["elo_before", "elo_after"]].notna().all().all()

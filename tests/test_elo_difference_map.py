"""Behaviour gates for `fit_difference_map` (ticket elo-difference-map). See
the contract in `domain/elo_lambda.fit_difference_map`'s (former) docstring
and `domain/elo_difference_map.py` for what these test.

Stores and histories are hand-built rather than the real data:
`MatchStore.__new__` skips `__init__` (which reads CSV shards off disk) and
we set `_matches` directly, the same pattern `tests/test_elo_replay.py`
uses. `EloHistory.ratings` is hand-built too (two rows per match, one per
side) rather than produced by `elo.replay` - this ticket only needs
`fixture_id`, `elo_before` and `is_home` from it, and hand-building lets
each test choose exactly the Elo values its regression needs.
"""

import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.domain.elo import EloHistory
from brasileirao_simulator.domain.elo_lambda import EloLambdaParams, fit_difference_map
from brasileirao_simulator.domain.match_store import MatchStore

HOME_ADVANTAGE = EloLambdaParams().home_advantage

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


def _match_row(
    fixture_id,
    season,
    home_goals,
    away_goals,
    is_neutral=False,
    home_id=1,
    away_id=2,
    league_id=71,
) -> dict:
    day = (fixture_id % 28) + 1
    month = 1 + ((fixture_id // 28) % 9)  # stays within a season, 9*28 > 60
    return {
        "fixture_id": fixture_id,
        "fixture_date": f"{season}-{month:02d}-{day:02d}T00:00:00+00:00",
        "season": season,
        "league_id": league_id,
        "round": "Regular Season - 1",
        "home_id": home_id,
        "away_id": away_id,
        "home_name": "Home FC",
        "away_name": "Away FC",
        "home_goals": home_goals,
        "away_goals": away_goals,
        "status": "FT",
        "is_neutral": is_neutral,
    }


def _store(rows: list) -> MatchStore:
    frame = pd.DataFrame(rows)
    for column, dtype in _ID_DTYPES.items():
        frame[column] = frame[column].astype(dtype)
    store = MatchStore.__new__(MatchStore)
    store._matches = frame
    return store


def _history(elo_rows: list) -> EloHistory:
    """`elo_rows`: dicts with `fixture_id`, `elo_before_home`,
    `elo_before_away`. Builds the two-rows-per-match `EloHistory.ratings`
    frame `fit_difference_map` reads (`fixture_id`, `elo_before`,
    `is_home`)."""
    from brasileirao_simulator.domain.elo import EloParams

    records = []
    for row in elo_rows:
        records.append(
            {"fixture_id": row["fixture_id"], "elo_before": row["elo_before_home"], "is_home": True}
        )
        records.append(
            {"fixture_id": row["fixture_id"], "elo_before": row["elo_before_away"], "is_home": False}
        )
    ratings = pd.DataFrame(records)
    ratings["fixture_id"] = ratings["fixture_id"].astype("int64")
    ratings["elo_before"] = ratings["elo_before"].astype("float64")
    ratings["is_home"] = ratings["is_home"].astype("bool")
    return EloHistory(ratings=ratings, params=EloParams())


def _linear_rows(
    n: int,
    slope: float,
    intercept: float,
    *,
    x_range: tuple = (-900.0, 900.0),
    noise_sigma: float = 0.05,
    seed: int = 42,
    season: int = 2019,
    is_neutral: bool = False,
    fixture_id_start: int = 1,
) -> tuple:
    """`n` synthetic matches whose Elo-difference regressor `x_i` is spread
    evenly over `x_range` and whose goal difference is
    `round(slope * x_i + intercept + noise_i)`, `noise_i` tiny Gaussian
    noise. Returns `(match_rows, elo_rows)` in the shapes `_store`/`_history`
    take. `elo_before_away` is held at a constant 1500 and `elo_before_home`
    is solved backwards so that `fit_difference_map`'s own regressor
    (`elo_before_home + home_advantage * (1 - is_neutral) - elo_before_away`)
    equals `x_i` exactly.
    """
    rng = np.random.default_rng(seed)
    raw_x = np.linspace(x_range[0], x_range[1], n)
    noise = rng.normal(0.0, noise_sigma, n)
    diff = np.round(slope * raw_x + intercept + noise).astype(int)

    ha_term = 0.0 if is_neutral else HOME_ADVANTAGE
    # Baseline away-goal count large enough that home_goals = baseline + diff
    # never goes negative, whatever slope/intercept/x_range this call used.
    away_goals_baseline = int(np.max(np.abs(diff))) + 10

    matches, elo_rows = [], []
    for i in range(n):
        fixture_id = fixture_id_start + i
        elo_away = 1500.0
        elo_home = raw_x[i] - ha_term + elo_away
        home_goals = away_goals_baseline + int(diff[i])
        assert home_goals >= 0  # sanity: the baseline must dominate the swing
        matches.append(
            _match_row(fixture_id, season, home_goals, away_goals_baseline, is_neutral=is_neutral)
        )
        elo_rows.append({"fixture_id": fixture_id, "elo_before_home": elo_home, "elo_before_away": elo_away})
    return matches, elo_rows


# --- (a) recovers slope and intercept ---------------------------------------


def test_fit_recovers_slope_and_intercept_from_a_synthetic_linear_relationship():
    matches, elo_rows = _linear_rows(181, slope=0.004, intercept=0.2)

    result = fit_difference_map(_history(elo_rows), _store(matches), EloLambdaParams())

    assert result.slope == pytest.approx(0.004, rel=0.10)
    assert result.intercept == pytest.approx(0.2, abs=0.02)
    assert result.fitted_on_season == 2019
    assert result.n_matches == 181


# --- (b) monotone on [-400, 400] --------------------------------------------


def test_fitted_map_is_monotone_increasing_on_minus_400_to_400():
    matches, elo_rows = _linear_rows(181, slope=0.004, intercept=0.2)
    result = fit_difference_map(_history(elo_rows), _store(matches), EloLambdaParams())

    values = [result(x) for x in range(-400, 401, 50)]
    for earlier, later in zip(values, values[1:]):
        assert later > earlier


# --- (c) only the burn-in season's matches are used -------------------------


def test_only_burn_in_seasons_matches_are_used_for_fitting():
    baseline_matches, baseline_elo = _linear_rows(181, slope=0.004, intercept=0.2, season=2019)
    baseline = fit_difference_map(_history(baseline_elo), _store(baseline_matches), EloLambdaParams())

    # A wildly different (steep, negative) relationship in another season -
    # if this leaked into the fit it would drag the slope negative or at
    # least move it well outside the 10% band already asserted above.
    other_matches, other_elo = _linear_rows(
        60, slope=-0.05, intercept=-10.0, season=2020, x_range=(-300.0, 300.0),
        noise_sigma=0.0, seed=7, fixture_id_start=10_000,
    )

    combined_matches = baseline_matches + other_matches
    combined_elo = baseline_elo + other_elo

    result = fit_difference_map(_history(combined_elo), _store(combined_matches), EloLambdaParams())

    assert result.slope == pytest.approx(baseline.slope)
    assert result.intercept == pytest.approx(baseline.intercept)
    assert result.n_matches == baseline.n_matches == 181


# --- (d) neutral matches drop the home advantage from x ---------------------


def test_neutral_matches_drop_home_advantage_from_the_regressor():
    non_neutral_matches, non_neutral_elo = _linear_rows(
        60, slope=0.004, intercept=0.2, is_neutral=False, seed=7, x_range=(-300.0, 300.0)
    )
    neutral_matches, neutral_elo = _linear_rows(
        60, slope=0.004, intercept=0.2, is_neutral=True, seed=7, x_range=(-300.0, 300.0)
    )

    # Both datasets are built so fit_difference_map's own regressor
    # (elo_before_home + home_advantage * (1 - is_neutral) - elo_before_away)
    # produces the identical x_i sequence for both, and the target y_i
    # sequence is identical by construction (same slope/intercept/seed) -
    # so the fits must come out identical too, exactly proving the
    # `(1 - is_neutral)` term is what `_linear_rows` compensated for.
    result_non_neutral = fit_difference_map(
        _history(non_neutral_elo), _store(non_neutral_matches), EloLambdaParams()
    )
    result_neutral = fit_difference_map(_history(neutral_elo), _store(neutral_matches), EloLambdaParams())

    assert result_non_neutral.slope == pytest.approx(result_neutral.slope)
    assert result_non_neutral.intercept == pytest.approx(result_neutral.intercept)

    # And the two elo_before_home columns actually differ by home_advantage -
    # confirming the test set up two genuinely different raw Elo inputs (not
    # two copies of the same numbers) and it is really the `(1 - is_neutral)`
    # term absorbing that difference, not some accidental cancellation.
    assert non_neutral_elo[0]["elo_before_home"] - neutral_elo[0]["elo_before_home"] == pytest.approx(
        -HOME_ADVANTAGE
    )


# --- (e) a negative-slope dataset raises -------------------------------------


def test_negative_slope_raises_value_error_naming_season_and_slope():
    matches, elo_rows = _linear_rows(
        60, slope=-0.01, intercept=0.1, noise_sigma=0.0, x_range=(-300.0, 300.0)
    )

    with pytest.raises(ValueError, match="2019") as excinfo:
        fit_difference_map(_history(elo_rows), _store(matches), EloLambdaParams())
    assert "slope" in str(excinfo.value)


# --- (f) fewer than 50 matches raises ----------------------------------------


def test_fewer_than_50_matches_raises_value_error():
    matches, elo_rows = _linear_rows(10, slope=0.004, intercept=0.2, noise_sigma=0.0)

    with pytest.raises(ValueError):
        fit_difference_map(_history(elo_rows), _store(matches), EloLambdaParams())


# --- Real-store gate ----------------------------------------------------------


@pytest.mark.slow
def test_fit_on_2019_real_store_and_2020_serie_a_correlation(capsys):
    from brasileirao_simulator.domain.elo import replay

    store = MatchStore()
    history = replay(store)
    params = EloLambdaParams()

    result = fit_difference_map(history, store, params)
    assert result.slope > 0

    home_before = history.ratings.loc[history.ratings["is_home"], ["fixture_id", "elo_before"]].rename(
        columns={"elo_before": "elo_before_home"}
    )
    away_before = history.ratings.loc[~history.ratings["is_home"], ["fixture_id", "elo_before"]].rename(
        columns={"elo_before": "elo_before_away"}
    )
    serie_a_2020 = store.matches[(store.matches["season"] == 2020) & (store.matches["league_id"] == 71)]
    joined = serie_a_2020.merge(home_before, on="fixture_id").merge(away_before, on="fixture_id")

    home_advantage_term = params.home_advantage * (1.0 - joined["is_neutral"].astype(float))
    x = joined["elo_before_home"] + home_advantage_term - joined["elo_before_away"]
    observed = joined["home_goals"] - joined["away_goals"]
    predicted = x.map(result)

    correlation = float(np.corrcoef(predicted, observed)[0, 1])

    print(
        f"\n[elo-difference-map] fitted on 2019: slope={result.slope!r} "
        f"intercept={result.intercept!r} n_matches={result.n_matches!r}"
    )
    print(f"[elo-difference-map] 2020 Serie A predicted/observed correlation={correlation!r}")

    assert correlation > 0.15

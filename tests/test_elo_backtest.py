"""Unit tests for entrypoints/elo_backtest.py's pure parts. The end-to-end
check - the fixed-2019 setting reproducing the committed four-arm Elo column -
runs as a gate inside `run_ten_seasons` itself."""

import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.domain.elo import EloParams
from brasileirao_simulator.domain.elo_lambda import EloLambdaParams
from brasileirao_simulator.entrypoints.elo_backtest import (
    CONFIRM_SEASONS,
    FIND_SEASONS,
    TEN_SEASONS,
    EloSetting,
    compare,
    fixed_2019,
    previous_season,
)


def test_line_season_policies():
    assert fixed_2019(2016) == fixed_2019(2025) == 2019
    assert previous_season(2016) == 2015
    assert previous_season(2025) == 2024


def test_previous_season_never_fits_on_the_scored_season_or_later():
    for season in TEN_SEASONS:
        assert previous_season(season) < season


def test_setting_defaults_to_the_previous_season_line():
    setting = EloSetting("x")
    assert setting.lambda_params_for(2018).burn_in_season == 2017


def test_fixed_2019_setting_matches_the_pre_switch_lambda_params():
    setting = EloSetting("x", line_season_for=fixed_2019)
    assert setting.lambda_params_for(2023) == EloLambdaParams(burn_in_season=2019)


def test_lambda_params_for_keeps_every_other_field():
    base = EloLambdaParams(home_advantage=100.0, eps=0.1)
    got = EloSetting("x", lambda_params=base).lambda_params_for(2021)
    assert (got.home_advantage, got.eps, got.burn_in_season) == (100.0, 0.1, 2020)


def test_setting_elo_params_default_is_the_shipped_default():
    assert EloSetting("x").elo_params == EloParams()


def _frame(diff_by_season: dict, n: int = 40) -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(0)
    for season, diff in diff_by_season.items():
        base = rng.uniform(0.1, 0.3, n)
        for b in base:
            rows.append({"season": season, "rps_a": b + diff, "rps_b": b})
    return pd.DataFrame(rows)


def test_compare_sign_and_season_count():
    frame = _frame({s: (-0.01 if s % 2 else 0.01) for s in TEN_SEASONS})
    got = compare(frame, "a", "b", TEN_SEASONS)
    assert got["a_better_seasons"] == 5
    assert got["matches"] == 400
    assert got["diff"] == pytest.approx(0.0, abs=1e-12)
    assert [r["season"] for r in got["per_season"]] == list(TEN_SEASONS)


def test_compare_constant_difference_has_a_degenerate_interval():
    frame = _frame({s: -0.02 for s in TEN_SEASONS})
    got = compare(frame, "a", "b", TEN_SEASONS)
    assert got["diff"] == pytest.approx(-0.02)
    assert got["ci_low"] == pytest.approx(-0.02) and got["ci_high"] == pytest.approx(-0.02)
    assert got["a_better_seasons"] == 10


def test_compare_reports_halves_only_when_the_run_covers_them():
    frame = _frame({s: -0.01 for s in TEN_SEASONS})
    full = compare(frame, "a", "b", TEN_SEASONS)
    assert set(FIND_SEASONS) | set(CONFIRM_SEASONS) == set(TEN_SEASONS)
    assert "find" in full and "confirm" in full
    short = compare(frame, "a", "b", (2020, 2021, 2022))
    assert "find" not in short and "confirm" not in short


def test_compare_ignores_seasons_outside_the_request():
    frame = _frame({2020: -0.01, 2021: 0.05})
    got = compare(frame, "a", "b", (2020,))
    assert got["matches"] == 40
    assert got["diff"] == pytest.approx(-0.01)


# --- elo-sweeps grid -------------------------------------------------------

from brasileirao_simulator.domain.elo import SEED_BY_DIVISION  # noqa: E402
from brasileirao_simulator.entrypoints.elo_backtest import DEFAULT, _seeds_for_gap, sweep_grid  # noqa: E402


def test_grid_starts_with_the_default_and_names_are_unique():
    grid = sweep_grid()
    assert grid[0][0] == DEFAULT and grid[0][2] == EloSetting(DEFAULT)
    names = [s.name for _, _, s in grid]
    assert len(names) == len(set(names)) == 25


def test_every_level_uses_the_previous_season_line():
    assert all(s.line_season_for is previous_season for _, _, s in sweep_grid())


def test_seed_gap_of_100_is_todays_seeds():
    assert _seeds_for_gap(100) == dict(SEED_BY_DIVISION)


def test_home_advantage_levels_move_both_params_together():
    for knob, _, s in sweep_grid():
        assert s.elo_params.home_advantage == s.lambda_params.home_advantage


def test_each_level_changes_exactly_one_thing():
    default = EloSetting(DEFAULT)
    for knob, level, s in sweep_grid()[1:]:
        changed_elo = [f for f in ("k", "home_advantage", "seeds", "margin_ladder", "competition_weight", "season_regression")
                       if getattr(s.elo_params, f) != getattr(default.elo_params, f)]
        changed_lambda = [f for f in ("home_advantage", "totals_window_days", "eps")
                          if getattr(s.lambda_params, f) != getattr(default.lambda_params, f)]
        if knob == "home_advantage":
            assert changed_elo == ["home_advantage"] and changed_lambda == ["home_advantage"], (knob, level)
        else:
            assert len(changed_elo) + len(changed_lambda) == 1, (knob, level, changed_elo, changed_lambda)

"""Fast sanity checks for the four-arm-backtest ticket (E5).

Deliberately single-date and synthetic-data checks, not a full-season or
multi-season run: the full 2020-2025 run this ticket produces takes minutes
(arms A and B's own cost, plus an Elo replay per season and a per-date
analytic-Poisson forecast for arm C) and is run directly via
`dixon_coles_backtest.py --four-arms`, not as a test. What belongs in the
fast suite is exactly what the ticket names: arm C's forecasts matching arm
B's match set on one real date, the layering helper's drop/count behaviour
on synthetic data (no pickles, no MatchStore needed), and the export-row
builders' shape.
"""

import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.dixon_coles_backtest import (
    ONE_HOT,
    OUTCOMES,
    _four_arm_export_row,
    _four_arm_pooled_row,
    _layer_arm,
    dixon_coles_all_forecasts_for_date,
    elo_forecasts_for_date,
)
from brasileirao_simulator.entrypoints.benchmark_chancedegol import rps
from brasileirao_simulator.adapters.elo_adapter import EloAdapter
from brasileirao_simulator.domain import dixon_coles


SEASON = 2025
AS_OF = "2025-03-29"


@pytest.mark.slow
def test_elo_forecasts_for_date_matches_arm_b_match_set():
    """One real date: arm C's outcome probabilities sum to 1 (up to
    `match_outcome_probs`'s own max_goals=15 truncation residual - measured
    up to ~1.3e-9 on this date's most lopsided Elo mismatches, so the
    assertion uses the same abs=1e-6 tolerance
    `test_match_sets_are_identical_across_arms_on_one_date` already uses for
    this exact kind of sum-to-1 check), one entry per remaining fixture, and
    its key set is IDENTICAL to arm B's (`dixon_coles_all_forecasts_for_date`)
    on the same date - the two must forecast the same fixtures for the
    four-arm comparison to be clean. `lambda_fallbacks` is 0 on this
    well-covered 2025 date."""
    tables = Tables(SeasonData(SEASON))
    store = MatchStore()
    adapter = EloAdapter("average", SEASON, match_store=store)

    forecasts, lambda_fallbacks = elo_forecasts_for_date(SEASON, AS_OF, tables, store, adapter)
    dixon_b, _, _, _ = dixon_coles_all_forecasts_for_date(
        SEASON, AS_OF, tables, store, dixon_coles.DEFAULT_XI, max_iterations=200
    )

    assert lambda_fallbacks == 0
    assert len(forecasts) > 0
    assert set(forecasts) == set(dixon_b)
    for probs in forecasts.values():
        assert sum(probs) == pytest.approx(1.0, abs=1e-6)


def test_layer_arm_drops_missing_and_counts():
    """A synthetic `matches` frame (no pickles, no MatchStore) with a
    forecasts dict missing one (as_of_date, match_key): the missing row is
    dropped and counted, and the surviving rows carry `p_{o}_{name}`
    columns whose values are exactly the ones handed in - so `rps()` on
    them reproduces the same number computed independently."""
    matches = pd.DataFrame(
        [
            {"as_of_date": "2025-01-01", "match_key": "A x B", "outcome": "home"},
            {"as_of_date": "2025-01-01", "match_key": "C x D", "outcome": "draw"},
            {"as_of_date": "2025-01-02", "match_key": "E x F", "outcome": "away"},
        ]
    )
    forecasts_by_date = {
        "2025-01-01": {
            "A x B": (0.5, 0.3, 0.2),
            # "C x D" deliberately missing.
        },
        "2025-01-02": {
            "E x F": (0.2, 0.3, 0.5),
        },
    }

    layered, dropped = _layer_arm(matches, forecasts_by_date, "elo")

    assert dropped == 1
    assert len(layered) == 2
    assert set(layered["match_key"]) == {"A x B", "E x F"}

    a_b_row = layered.loc[layered["match_key"] == "A x B"].iloc[0]
    assert (a_b_row["p_home_elo"], a_b_row["p_draw_elo"], a_b_row["p_away_elo"]) == (0.5, 0.3, 0.2)
    e_f_row = layered.loc[layered["match_key"] == "E x F"].iloc[0]
    assert (e_f_row["p_home_elo"], e_f_row["p_draw_elo"], e_f_row["p_away_elo"]) == (0.2, 0.3, 0.5)

    outcomes = np.array([ONE_HOT[o] for o in layered["outcome"]], dtype=float)
    probabilities = layered[[f"p_{o}_elo" for o in OUTCOMES]].to_numpy()
    expected = rps(probabilities, outcomes)

    manual_probabilities = np.array(
        [forecasts_by_date[row.as_of_date][row.match_key] for row in layered.itertuples(index=False)]
    )
    manual_outcomes = np.array([ONE_HOT[o] for o in layered["outcome"]], dtype=float)
    assert (rps(manual_probabilities, manual_outcomes) == expected).all()


def test_layer_arm_keeps_everything_when_nothing_is_missing():
    matches = pd.DataFrame(
        [
            {"as_of_date": "2025-01-01", "match_key": "A x B", "outcome": "home"},
            {"as_of_date": "2025-01-01", "match_key": "C x D", "outcome": "draw"},
        ]
    )
    forecasts_by_date = {
        "2025-01-01": {
            "A x B": (0.5, 0.3, 0.2),
            "C x D": (0.3, 0.3, 0.4),
        },
    }
    layered, dropped = _layer_arm(matches, forecasts_by_date, "elo")
    assert dropped == 0
    assert len(layered) == 2


def _hand_built_summary(season: int, c_better: bool, c_better_current: bool) -> dict:
    """A `score_four_arms`-shaped summary with none of the expensive fields
    real scoring would fill in, just enough for `_four_arm_export_row` to
    build one row from."""
    return {
        "season": season,
        "matches_abc": 100,
        "matches_four_arms": 100,
        "dropped_elo": 0,
        "lambda_fallbacks_total": 0,
        "coverage": {"competitions": {72: 380}, "clubs": ["A", "B", "C"]},
        "models": {
            "dixon_coles": {"rps": 0.21, "brier": 0.63, "log_loss": 1.05},
            "dixon_coles_all": {"rps": 0.20, "brier": 0.62, "log_loss": 1.03},
            "current": {"rps": 0.22, "brier": 0.64, "log_loss": 1.06},
            "elo": {"rps": 0.19 if c_better else 0.23, "brier": 0.61, "log_loss": 1.01},
        },
        "c_vs_b": {
            "diff": -0.01 if c_better else 0.01,
            "ci_low": -0.02,
            "ci_high": 0.0 if c_better else 0.02,
            "c_wins": 55,
            "c_better": c_better,
        },
        "c_vs_a": {"diff": -0.02, "ci_low": -0.03, "ci_high": -0.01, "c_wins": 60, "c_better": True},
        "c_vs_current": {
            "diff": -0.03 if c_better_current else 0.03,
            "ci_low": -0.04,
            "ci_high": -0.02 if c_better_current else 0.04,
            "c_wins": 65,
            "c_better": c_better_current,
        },
        "b_vs_a": {"diff": -0.005, "ci_low": -0.01, "ci_high": 0.0, "b_wins": 50, "b_better": True},
        "b_vs_current": {"diff": -0.006, "ci_low": -0.012, "ci_high": 0.0, "b_wins": 52},
    }


DOCUMENTED_EXPORT_ROW_COLUMNS = [
    "season",
    "partial",
    "matches",
    "rps_dixon_coles",
    "rps_dixon_coles_all",
    "rps_current",
    "rps_elo",
    "brier_dixon_coles",
    "brier_dixon_coles_all",
    "brier_current",
    "brier_elo",
    "log_loss_dixon_coles",
    "log_loss_dixon_coles_all",
    "log_loss_current",
    "log_loss_elo",
    "diff_c_vs_b",
    "ci_low_c_vs_b",
    "ci_high_c_vs_b",
    "c_wins_vs_b",
    "c_better_than_b",
    "diff_c_vs_a",
    "ci_low_c_vs_a",
    "ci_high_c_vs_a",
    "diff_c_vs_current",
    "ci_low_c_vs_current",
    "ci_high_c_vs_current",
    "c_wins_vs_current",
    "diff_b_vs_a",
    "ci_low_b_vs_a",
    "ci_high_b_vs_a",
    "diff_b_vs_current",
    "ci_low_b_vs_current",
    "ci_high_b_vs_current",
    "dropped_elo",
    "lambda_fallbacks_total",
    "coverage_competitions",
    "coverage_clubs",
]


def test_four_arm_export_row_has_the_documented_columns():
    summary = _hand_built_summary(2024, c_better=True, c_better_current=True)
    row = _four_arm_export_row(summary, partial_seasons=(2026,))

    for column in DOCUMENTED_EXPORT_ROW_COLUMNS:
        assert column in row, f"missing documented column {column!r}"

    assert row["season"] == 2024
    assert row["partial"] is False
    assert row["matches"] == summary["matches_abc"]
    assert row["matches_abc"] == 100
    assert row["matches_four_arms"] == 100
    assert row["c_better_than_b"] is True
    assert row["coverage_clubs"] == 3


def test_four_arm_export_row_partial_flag():
    summary = _hand_built_summary(2026, c_better=False, c_better_current=False)
    row = _four_arm_export_row(summary, partial_seasons=(2026,))
    assert row["partial"] is True


def test_four_arm_pooled_row_has_the_documented_fields():
    seasons = (2024, 2025)
    season_df = pd.DataFrame(
        [
            _four_arm_export_row(_hand_built_summary(2024, c_better=True, c_better_current=True), (2026,)),
            _four_arm_export_row(_hand_built_summary(2025, c_better=False, c_better_current=True), (2026,)),
        ]
    )

    n = 20
    pooled_matches = pd.DataFrame(
        {
            "rps_dixon_coles": np.linspace(0.20, 0.22, n),
            "rps_dixon_coles_all": np.linspace(0.19, 0.21, n),
            "rps_current": np.linspace(0.21, 0.23, n),
            "rps_elo": np.linspace(0.18, 0.20, n),
            "brier_dixon_coles": np.linspace(0.60, 0.62, n),
            "brier_dixon_coles_all": np.linspace(0.59, 0.61, n),
            "brier_current": np.linspace(0.61, 0.63, n),
            "brier_elo": np.linspace(0.58, 0.60, n),
            "log_loss_dixon_coles": np.linspace(1.00, 1.02, n),
            "log_loss_dixon_coles_all": np.linspace(0.99, 1.01, n),
            "log_loss_current": np.linspace(1.01, 1.03, n),
            "log_loss_elo": np.linspace(0.98, 1.00, n),
        }
    )

    pooled_row = _four_arm_pooled_row(pooled_matches, season_df, seasons, seed=7)

    documented_pooled_columns = [
        "matches",
        "seasons",
        "rps_dixon_coles",
        "rps_dixon_coles_all",
        "rps_current",
        "rps_elo",
        "brier_dixon_coles",
        "brier_dixon_coles_all",
        "brier_current",
        "brier_elo",
        "log_loss_dixon_coles",
        "log_loss_dixon_coles_all",
        "log_loss_current",
        "log_loss_elo",
        "diff_c_vs_b",
        "ci_low_c_vs_b",
        "ci_high_c_vs_b",
        "diff_c_vs_a",
        "ci_low_c_vs_a",
        "ci_high_c_vs_a",
        "diff_c_vs_current",
        "ci_low_c_vs_current",
        "ci_high_c_vs_current",
        "diff_b_vs_a",
        "ci_low_b_vs_a",
        "ci_high_b_vs_a",
        "diff_b_vs_current",
        "ci_low_b_vs_current",
        "ci_high_b_vs_current",
        "c_better_in_n_of_m_seasons",
        "c_better_than_current_in_n_of_m",
    ]
    for column in documented_pooled_columns:
        assert column in pooled_row, f"missing documented pooled column {column!r}"

    assert pooled_row["matches"] == n
    assert pooled_row["seasons"] == "2024,2025"
    assert pooled_row["c_better_in_n_of_m_seasons"] == "1/2"
    assert pooled_row["c_better_than_current_in_n_of_m"] == "2/2"


def test_four_arms_cli_flag_parses():
    """`--four-arms` parses alongside the existing `--out`/`--pooled-out`/
    `--report-out`/`--max-iterations`/`--seed` flags, without requiring
    `--season` - exercised against the actual CLI parser
    (`_build_arg_parser`), not a reimplementation of it."""
    from brasileirao_simulator.entrypoints.dixon_coles_backtest import _build_arg_parser

    parser = _build_arg_parser()

    args = parser.parse_args(
        [
            "--four-arms",
            "--out",
            "/tmp/four_arms.csv",
            "--pooled-out",
            "/tmp/four_arms_pooled.csv",
            "--report-out",
            "/tmp/report.md",
            "--max-iterations",
            "500",
            "--seed",
            "3",
        ]
    )
    assert args.four_arms is True
    assert args.data_vs_league is False
    assert args.season is None
    assert args.out == "/tmp/four_arms.csv"
    assert args.pooled_out == "/tmp/four_arms_pooled.csv"
    assert args.report_out == "/tmp/report.md"
    assert args.max_iterations == 500
    assert args.seed == 3


def test_data_vs_league_cli_flag_still_parses_with_defaults():
    """The pre-existing `--data-vs-league` flag still parses, and
    `--pooled-out`/`--report-out` still default to `None` (each branch in
    `__main__` fills in its own mode-specific default) - a regression guard
    on the shared-parser refactor `--four-arms` required."""
    from brasileirao_simulator.entrypoints.dixon_coles_backtest import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["--data-vs-league"])

    assert args.data_vs_league is True
    assert args.four_arms is False
    assert args.pooled_out is None
    assert args.report_out is None
    assert args.out is None

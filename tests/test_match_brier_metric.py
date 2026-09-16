"""The match-level Brier metric, tested against synthetic forecasts with known
answers - the same discipline test_calibration.py already applies to the
title-level brier_score/calibration_curve (a metric that has never been
checked against ground truth cannot be trusted to grade the model).

match_brier is the multiclass (three-outcome) generalisation of that same
idea: instead of one probability scored against a 0/1 outcome, a forecast is
three probabilities (home/draw/away) scored against a one-hot outcome. It is
the sum of the three squared errors, so its range is [0, 2] rather than
brier_score's [0, 1].
"""

import numpy as np
import pytest

from brasileirao_simulator.entrypoints.calibration_backtest import brier_score
from brasileirao_simulator.entrypoints.match_brier_backtest import (
    OUTCOMES,
    base_rate_probs,
    match_brier,
    one_hot_outcomes,
    paired_bootstrap_ci,
    skill_score,
)


# ---------------------------------------------------------------------------
# one_hot_outcomes
# ---------------------------------------------------------------------------


def test_one_hot_outcomes_encodes_in_home_draw_away_order():
    encoded = one_hot_outcomes(["home", "draw", "away"])
    assert np.array_equal(encoded, [[1, 0, 0], [0, 1, 0], [0, 0, 1]])


def test_one_hot_outcomes_rejects_unknown_label():
    with pytest.raises(ValueError, match="unknown outcome"):
        one_hot_outcomes(["home", "extra_time"])


# ---------------------------------------------------------------------------
# match_brier
# ---------------------------------------------------------------------------


def test_match_brier_is_zero_for_a_perfect_certain_correct_forecast():
    scores = match_brier([[1.0, 0.0, 0.0]], one_hot_outcomes(["home"]))
    assert scores == pytest.approx([0.0])


def test_match_brier_is_two_for_a_maximally_wrong_forecast():
    """All the mass placed on the one outcome that COULD NOT happen, given a
    certain wrong call for a binary sub-case (home certain, away happened) -
    the ceiling the spec names explicitly."""
    scores = match_brier([[1.0, 0.0, 0.0]], one_hot_outcomes(["away"]))
    assert scores == pytest.approx([2.0])


def test_match_brier_hand_computed_uniform_forecast():
    """A uniform 1/3-1/3-1/3 forecast against any single outcome: two terms
    of (1/3)^2 and one term of (2/3)^2, summing to 6/9 = 2/3 - independent of
    which of the three outcomes actually happened, by symmetry."""
    forecast = [[1 / 3, 1 / 3, 1 / 3]]
    for outcome in OUTCOMES:
        scores = match_brier(forecast, one_hot_outcomes([outcome]))
        assert scores == pytest.approx([2 / 3])


def test_match_brier_is_vectorised_over_many_matches():
    forecasts = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [1 / 3, 1 / 3, 1 / 3]]
    outcomes = one_hot_outcomes(["home", "away", "draw"])
    scores = match_brier(forecasts, outcomes)
    assert scores == pytest.approx([0.0, 0.0, 2 / 3])


def test_match_brier_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="same shape"):
        match_brier([[1.0, 0.0, 0.0]], one_hot_outcomes(["home", "away"]))


def test_match_brier_rejects_wrong_column_count():
    with pytest.raises(ValueError, match="3 columns"):
        match_brier([[1.0, 0.0]], [[1.0, 0.0]])


def test_match_brier_matches_the_familiar_binary_form_on_a_degenerate_two_outcome_case():
    """The multiclass sum-of-squared-errors form, restricted to a case where
    only two of the three outcomes are ever forecast or observed (draw always
    0), is the well-known K=2 special case of Brier's original multi-category
    score: it is exactly TWICE this codebase's own single-probability
    brier_score (calibration_backtest.brier_score), because both the home
    term and the away term contribute the identical squared error
    (p_home - o_home) == -(p_away - o_away), so each is counted once and
    they are equal. This is the standard identity, not a coincidence of this
    test's numbers - see Brier (1950); most "the Brier score" usages for a
    binary event report the halved, single-probability form, which is what
    brier_score already implements.
    """
    rng = np.random.default_rng(0)
    p_home = rng.uniform(0.05, 0.95, size=25)
    home_wins = rng.integers(0, 2, size=25)

    forecasts = np.column_stack([p_home, np.zeros(25), 1 - p_home])
    outcomes = np.column_stack([home_wins, np.zeros(25), 1 - home_wins])

    multiclass = match_brier(forecasts, outcomes)
    binary = (p_home - home_wins) ** 2  # brier_score's per-observation term

    assert multiclass == pytest.approx(2 * binary)
    # And the aggregate mean carries the same factor of two.
    assert multiclass.mean() == pytest.approx(2 * brier_score(p_home, home_wins))


# ---------------------------------------------------------------------------
# base_rate_probs
# ---------------------------------------------------------------------------


def test_base_rate_probs_matches_a_hand_count():
    outcomes = ["home"] * 5 + ["draw"] * 3 + ["away"] * 2
    probs = base_rate_probs(outcomes)
    assert probs == pytest.approx((0.5, 0.3, 0.2))


def test_base_rate_probs_sums_to_one():
    outcomes = ["home", "draw", "away", "home", "home", "away"]
    assert sum(base_rate_probs(outcomes)) == pytest.approx(1.0)


def test_base_rate_probs_rejects_empty_input():
    with pytest.raises(ValueError):
        base_rate_probs([])


# ---------------------------------------------------------------------------
# skill_score
# ---------------------------------------------------------------------------


def test_skill_score_is_zero_when_model_equals_reference():
    assert skill_score(0.5, 0.5) == pytest.approx(0.0)


def test_skill_score_is_positive_when_model_beats_reference():
    assert skill_score(0.4, 0.5) > 0


def test_skill_score_is_negative_when_model_is_worse_than_reference():
    assert skill_score(0.6, 0.5) < 0


def test_skill_score_is_one_for_a_perfect_model():
    assert skill_score(0.0, 0.5) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# paired_bootstrap_ci
# ---------------------------------------------------------------------------


def test_paired_bootstrap_ci_is_a_point_mass_when_every_diff_is_identical():
    mean, lo, hi = paired_bootstrap_ci([0.1, 0.1, 0.1, 0.1], n_boot=500, seed=0)
    assert mean == pytest.approx(0.1)
    assert lo == pytest.approx(0.1)
    assert hi == pytest.approx(0.1)

def test_paired_bootstrap_ci_brackets_the_sample_mean():
    rng = np.random.default_rng(1)
    diffs = rng.normal(loc=0.02, scale=0.1, size=500)
    mean, lo, hi = paired_bootstrap_ci(diffs, n_boot=2000, seed=1)
    assert lo < mean < hi
    assert mean == pytest.approx(diffs.mean())


def test_paired_bootstrap_ci_widens_with_more_variance():
    rng = np.random.default_rng(2)
    tight = rng.normal(loc=0.0, scale=0.01, size=500)
    wide = rng.normal(loc=0.0, scale=1.0, size=500)

    _, tight_lo, tight_hi = paired_bootstrap_ci(tight, n_boot=2000, seed=2)
    _, wide_lo, wide_hi = paired_bootstrap_ci(wide, n_boot=2000, seed=2)

    assert (wide_hi - wide_lo) > (tight_hi - tight_lo)


def test_paired_bootstrap_ci_rejects_empty_input():
    with pytest.raises(ValueError):
        paired_bootstrap_ci([], n_boot=100)

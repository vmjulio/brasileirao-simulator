"""The calibration metrics, tested against synthetic forecasts with known
answers - independent of the simulator, per the C2 backtest's own requirement
(a metric that has never been checked against ground truth cannot be trusted
to grade the model).

calibration_curve buckets forecasts and compares each bucket's mean forecast
to how often the event actually happened; brier_score is the mean squared
error between forecast and outcome. Every synthetic case here is built so the
observed frequency in each bucket is an EXACT, hand-picked fraction (not a
random draw), so both the curve and the Brier score have an exact expected
answer rather than "roughly right".
"""

import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.entrypoints.calibration_backtest import (
    brier_score,
    calibration_curve,
)


def _bucket(forecast: float, hits: int, n: int) -> tuple[list[float], list[float]]:
    """n forecasts of exactly `forecast`, with exactly `hits` of them correct.

    Deterministic by construction: the bucket's observed frequency is exactly
    hits/n, not an average that happens to land near it.
    """
    outcomes = [1.0] * hits + [0.0] * (n - hits)
    return [forecast] * n, outcomes


def _perfectly_calibrated():
    """Five buckets, each one's observed frequency exactly equal to its
    forecast: 10% of the p=0.1 forecasts hit, 30% of the p=0.3 ones, etc."""
    forecasts, outcomes = [], []
    for p, n in [(0.1, 10), (0.3, 10), (0.5, 10), (0.7, 10), (0.9, 10)]:
        f, o = _bucket(p, hits=round(p * n), n=n)
        forecasts += f
        outcomes += o
    return np.array(forecasts), np.array(outcomes)


def _systematically_overconfident():
    """Every forecast claims 90% or 95%, but the true rate is only 50%."""
    forecasts, outcomes = [], []
    for p in (0.9, 0.95):
        f, o = _bucket(p, hits=5, n=10)
        forecasts += f
        outcomes += o
    return np.array(forecasts), np.array(outcomes)


# ---------------------------------------------------------------------------
# brier_score
# ---------------------------------------------------------------------------


def test_brier_score_matches_a_hand_computed_value():
    """Four forecasts, hand-computed: (0.9-1)^2 + (0.1-0)^2 + (0.5-1)^2 +
    (0.5-0)^2, divided by 4."""
    forecasts = [0.9, 0.1, 0.5, 0.5]
    outcomes = [1, 0, 1, 0]
    expected = (0.01 + 0.01 + 0.25 + 0.25) / 4

    assert brier_score(forecasts, outcomes) == pytest.approx(expected)


def test_brier_score_is_zero_for_perfect_certainty():
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)


def test_brier_score_is_one_for_confidently_wrong_forecasts():
    assert brier_score([1.0, 0.0], [0, 1]) == pytest.approx(1.0)


def test_a_well_calibrated_set_scores_better_than_an_overconfident_one():
    """Same true event rate (50%) in both cases; only the confidence differs.
    Overconfidence must cost Brier score, or the metric is not doing its job."""
    calibrated_forecasts, calibrated_outcomes = _bucket(0.5, hits=5, n=10)
    overconfident_forecasts, overconfident_outcomes = _bucket(0.9, hits=5, n=10)

    calibrated = brier_score(calibrated_forecasts, calibrated_outcomes)
    overconfident = brier_score(overconfident_forecasts, overconfident_outcomes)

    assert overconfident > calibrated


def test_brier_score_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        brier_score([0.5, 0.5], [1])


# ---------------------------------------------------------------------------
# calibration_curve
# ---------------------------------------------------------------------------


def test_a_perfectly_calibrated_set_lands_on_the_diagonal():
    forecasts, outcomes = _perfectly_calibrated()
    curve = calibration_curve(forecasts, outcomes, bins=10)

    populated = curve[curve["count"] > 0]
    assert len(populated) == 5  # one per synthetic bucket
    assert np.allclose(
        populated["forecast_mean"], populated["observed_frequency"], atol=1e-9
    )


def test_an_overconfident_set_shows_up_off_diagonal():
    forecasts, outcomes = _systematically_overconfident()
    curve = calibration_curve(forecasts, outcomes, bins=10)

    populated = curve[curve["count"] > 0]
    gap = (populated["forecast_mean"] - populated["observed_frequency"]).abs()
    assert (gap > 0.3).all()


def test_calibration_curve_has_the_requested_number_of_bins():
    forecasts, outcomes = _perfectly_calibrated()
    assert len(calibration_curve(forecasts, outcomes, bins=4)) == 4
    assert len(calibration_curve(forecasts, outcomes, bins=20)) == 20


def test_calibration_curve_bins_span_zero_to_one_with_no_gaps():
    curve = calibration_curve([0.5], [1], bins=5)

    assert curve["bin_low"].iloc[0] == pytest.approx(0.0)
    assert curve["bin_high"].iloc[-1] == pytest.approx(1.0)
    assert np.allclose(curve["bin_low"].iloc[1:].to_numpy(), curve["bin_high"].iloc[:-1].to_numpy())


def test_a_forecast_of_exactly_one_lands_in_the_last_bin_not_off_the_end():
    curve = calibration_curve([1.0], [1], bins=10)

    assert curve["count"].sum() == 1
    assert curve.iloc[-1]["count"] == 1


def test_empty_bins_report_zero_count_and_no_fabricated_average():
    curve = calibration_curve([0.05], [0], bins=10)

    empty = curve[curve["bin_low"] > 0.1]
    assert (empty["count"] == 0).all()
    assert empty["forecast_mean"].isna().all()
    assert empty["observed_frequency"].isna().all()


def test_calibration_curve_accepts_pandas_series():
    forecasts, outcomes = _perfectly_calibrated()
    curve = calibration_curve(pd.Series(forecasts), pd.Series(outcomes), bins=10)
    assert isinstance(curve, pd.DataFrame)


def test_calibration_curve_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        calibration_curve([0.5, 0.5], [1])

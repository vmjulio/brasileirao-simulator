"""match_outcome_probs: the closed-form replacement for a Monte Carlo draw
at match level. These tests are the whole justification for trusting an
analytic quantity in place of a sampled one - see the module docstring in
domain/match_outcome_probs.py and the task report for how this is used.

REAL_DATA_MAX_LAMBDA was found by running build_baseline across every
as-of date of every season 2016-2026, every adjustment_weight in
{0.3, 0.4, 0.5, 0.6, 0.7} (the blend sweep's full range) at lookback=19 (its
held-fixed default): the single largest lam_home/lam_away this project's
real data and parameter sweep ever produces is ~3.147 (2020-08-08, w=0.7).
Hardcoded here rather than re-derived on every test run, which would need a
duckdb connection and every season's fixtures just to pin one float.
"""

import numpy as np
import pytest

from brasileirao_simulator.domain.match_outcome_probs import (
    match_outcome_probs,
    truncation_residual,
)


REAL_DATA_MAX_LAMBDA = 3.1466667175292966

# Poisson tails shrink steeply but not THAT steeply: measured (against a
# direct math.exp/factorial computation, not just this module's own
# recurrence - see the task report) residual at lam=3.0 is ~1.24e-7, at
# lam=REAL_DATA_MAX_LAMBDA (~3.15) ~2.32e-7, at lam=4.0 ~4.89e-6, at lam=6.0
# ~5.09e-4. "Negligible" here means: many orders of magnitude below the
# Brier-score differences (~1e-3 to 1e-2) this whole sweep is trying to
# resolve, not below machine epsilon.


# ---------------------------------------------------------------------------
# Truncation residual - how much probability mass max_goals=15 leaves behind.
# ---------------------------------------------------------------------------


def test_truncation_residual_at_the_largest_real_data_lambda_is_negligible():
    """The number this task asks to be reported: at the single largest
    lambda this project's data and blend sweep ever produce, how much mass
    does max_goals=15 truncate away for one team's goal distribution?
    ~2.3e-7 measured - about six orders of magnitude below the Brier-score
    differences this sweep resolves."""
    residual = truncation_residual(np.array([REAL_DATA_MAX_LAMBDA]), max_goals=15)[0]

    assert residual < 1e-6, f"truncation residual {residual:.3e} at lam={REAL_DATA_MAX_LAMBDA} is not negligible"


def test_truncation_residual_stays_negligible_well_beyond_real_data():
    """A safety margin beyond REAL_DATA_MAX_LAMBDA - lam=4.5 is already
    generous headroom over the real data's ~3.15 ceiling, and still leaves
    the residual four orders of magnitude below Brier-score resolution."""
    residual = truncation_residual(np.array([4.5]), max_goals=15)[0]
    assert residual < 1e-4


# ---------------------------------------------------------------------------
# Rows sum to 1.
# ---------------------------------------------------------------------------


def test_rows_sum_to_one_within_floating_point_tolerance():
    """Tolerance is 1e-4, not machine epsilon: with max_goals=15 the sum
    falls short of 1.0 by the truncation residual (see
    test_truncation_residual_* above), which grows with lambda - up to
    ~5e-6 at lam=4.0, the top of this test's sampled range. 1e-4 comfortably
    covers that while still catching a real bug (a wrong mask, a swapped
    axis) that would be off by orders of magnitude more."""
    rng = np.random.default_rng(0)
    lam_home = rng.uniform(0.1, 4.0, size=200)
    lam_away = rng.uniform(0.1, 4.0, size=200)

    probs = match_outcome_probs(lam_home, lam_away)

    assert probs.shape == (200, 3)
    np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-4)


def test_probabilities_are_nonnegative():
    rng = np.random.default_rng(1)
    lam_home = rng.uniform(0.1, 4.0, size=200)
    lam_away = rng.uniform(0.1, 4.0, size=200)

    probs = match_outcome_probs(lam_home, lam_away)

    assert (probs >= 0).all()


# ---------------------------------------------------------------------------
# Symmetry: equal lambdas must give an exactly symmetric home/away split.
# ---------------------------------------------------------------------------


def test_equal_lambdas_give_home_and_away_win_probability_exactly():
    lam = np.array([0.1, 0.5, 1.0, 2.0, 3.14, 5.0])

    probs = match_outcome_probs(lam, lam)

    assert np.array_equal(probs[:, 0], probs[:, 2]), "P(home win) must equal P(away win) exactly when lam_home == lam_away"


# ---------------------------------------------------------------------------
# Monotonicity: a stronger home side (relative to a fixed away side) must
# never make a home win less likely.
# ---------------------------------------------------------------------------


def test_p_home_win_increases_monotonically_with_lam_home():
    lam_home = np.array([0.2, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    lam_away = np.full_like(lam_home, 1.5)

    probs = match_outcome_probs(lam_home, lam_away)

    assert np.all(np.diff(probs[:, 0]) > 0), "P(home win) did not increase monotonically with lam_home"


def test_p_away_win_decreases_monotonically_with_lam_home():
    lam_home = np.array([0.2, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    lam_away = np.full_like(lam_home, 1.5)

    probs = match_outcome_probs(lam_home, lam_away)

    assert np.all(np.diff(probs[:, 2]) < 0), "P(away win) did not decrease monotonically with lam_home"


# ---------------------------------------------------------------------------
# Agreement with Monte Carlo - the test that actually proves the analytic
# replacement is sound, not merely internally consistent.
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(
    "lam_home, lam_away",
    [
        (1.5, 1.2),   # roughly typical - a middling home vs middling away side
        (0.6, 0.6),   # equal, weak sides - draw-heavy
        (3.0, 0.8),   # a strong favourite
        (0.3, 2.5),   # a strong away favourite
        (REAL_DATA_MAX_LAMBDA, REAL_DATA_MAX_LAMBDA),  # the real data's extreme
    ],
)
def test_analytic_probabilities_match_monte_carlo_frequencies(lam_home, lam_away):
    """Draws 200,000 (home, away) goal pairs and compares the empirical
    home/draw/away frequency to the closed form. This is the load-bearing
    test: everything else above checks the analytic function is internally
    consistent, this checks it agrees with the sampled quantity it replaces.

    Tolerance: 5 standard errors on a binomial proportion at n=200,000 - the
    largest single-outcome SE here is roughly sqrt(0.5*0.5/200000) ~= 0.0011,
    so 5 SE ~= 0.0056. Generous enough to make this test's own Monte Carlo
    noise a non-issue, tight enough that a real bug (e.g. a swapped mask or
    an off-by-one in the goal grid) still trips it easily.
    """
    n = 200_000
    rng = np.random.default_rng(42)
    home_goals = rng.poisson(lam_home, size=n)
    away_goals = rng.poisson(lam_away, size=n)

    empirical_home = float(np.mean(home_goals > away_goals))
    empirical_draw = float(np.mean(home_goals == away_goals))
    empirical_away = float(np.mean(home_goals < away_goals))

    analytic = match_outcome_probs(np.array([lam_home]), np.array([lam_away]))[0]

    se = 1.0 / (2 * np.sqrt(n))  # conservative upper bound on a binomial SE (p=0.5 worst case)
    tolerance = 5 * se

    assert abs(analytic[0] - empirical_home) < tolerance, (
        f"P(home) analytic={analytic[0]:.5f} empirical={empirical_home:.5f} "
        f"lam_home={lam_home} lam_away={lam_away}"
    )
    assert abs(analytic[1] - empirical_draw) < tolerance, (
        f"P(draw) analytic={analytic[1]:.5f} empirical={empirical_draw:.5f} "
        f"lam_home={lam_home} lam_away={lam_away}"
    )
    assert abs(analytic[2] - empirical_away) < tolerance, (
        f"P(away) analytic={analytic[2]:.5f} empirical={empirical_away:.5f} "
        f"lam_home={lam_home} lam_away={lam_away}"
    )

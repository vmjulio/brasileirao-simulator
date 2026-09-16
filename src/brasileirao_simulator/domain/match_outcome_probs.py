"""Analytic (closed-form) match outcome probabilities.

The match-Brier sweep (entrypoints/variant_sweep.py) used to score every
as-of date by running a full 5,000-iteration Monte Carlo over the WHOLE
remaining season (up to 380 fixtures) and reading off ~10 matches' worth of
simulated outcomes from it - about 97% of that computation thrown away. For
a single fixture with expected goals lam_home/lam_away, drawn as independent
Poissons (the model every simulator in this project already uses - see
domain/batch_simulation.py), the three outcome probabilities have an exact
closed form and need no sampling at all:

    P(home win) = sum_{h > a} pmf(h; lam_home) * pmf(a; lam_away)
    P(draw)     = sum_{h = a} pmf(h; lam_home) * pmf(a; lam_away)
    P(away win) = sum_{h < a} pmf(h; lam_home) * pmf(a; lam_away)

match_outcome_probs computes exactly this, truncating each team's goal
support at max_goals (default 15) and summing over the resulting grid - a
handful of microseconds per fixture, vectorised over every fixture at once,
with no Monte Carlo noise in the result. See
tests/test_match_outcome_probs.py for: the truncation-residual check that
justifies max_goals=15 against this project's real data, and the large-N
Monte Carlo cross-check that the closed form agrees with the sampled
quantity it replaces - the test that actually proves this replacement is
sound.

This module has no simulator, no fixture, and no persistence dependency -
pure functions of (lam_home, lam_away) - so it is safe for any caller,
production or sweep, to import. It does not replace simulate_batch: title
and relegation probabilities are aggregates over a whole simulated SEASON
(who wins the league, who goes down), not one match at a time, and still
need the season-level Monte Carlo in domain/batch_simulation.py.

No scipy in this project's dependencies (see requirements.txt), so the
Poisson pmf below is built with the standard stable recurrence
pmf(0) = exp(-lam), pmf(k) = pmf(k-1) * lam / k, rather than the textbook
exp(-lam) * lam**k / k! - which is numerically identical here but avoids
ever forming k! or lam**k directly.
"""

import numpy as np


def _poisson_pmf_grid(lam: np.ndarray, max_goals: int) -> np.ndarray:
    """(n, max_goals + 1) array; column k is P(X = k) for X ~ Poisson(lam[i]).
    """
    lam = np.asarray(lam, dtype=float)
    pmf = np.empty((lam.shape[0], max_goals + 1), dtype=float)
    pmf[:, 0] = np.exp(-lam)
    for k in range(1, max_goals + 1):
        pmf[:, k] = pmf[:, k - 1] * lam / k
    return pmf


def match_outcome_probs(lam_home, lam_away, max_goals: int = 15) -> np.ndarray:
    """(n_fixtures, 3) array of (P(home win), P(draw), P(away win)), exact
    under independent Poisson goals truncated at max_goals per side, in
    OUTCOMES column order (home, draw, away - see
    entrypoints/match_brier_backtest.py).

    lam_home, lam_away: (n_fixtures,) array-likes of expected goals.
    Vectorised over fixtures - the whole (fixtures x goals x goals) grid is
    built and reduced in one shot, no Python loop over matches.
    """
    lam_home = np.asarray(lam_home, dtype=float)
    lam_away = np.asarray(lam_away, dtype=float)
    if lam_home.shape != lam_away.shape:
        raise ValueError(
            f"lam_home and lam_away must be the same shape ({lam_home.shape} vs {lam_away.shape})"
        )

    pmf_home = _poisson_pmf_grid(lam_home, max_goals)  # (n, G+1)
    pmf_away = _poisson_pmf_grid(lam_away, max_goals)  # (n, G+1)

    goals = np.arange(max_goals + 1)
    more = goals[:, None] > goals[None, :]  # "first scorer's goals > second scorer's"
    equal = goals[:, None] == goals[None, :]

    # joint_ha[i, h, a] = P(home scores h) * P(away scores a); joint_ah is the
    # same product with the two teams' roles swapped. p_home and p_away are
    # each `[..., more].sum(axis=1)` applied to one of these - the identical
    # reduction, just fed pmf_home/pmf_away in opposite order. When
    # lam_home == lam_away elementwise, pmf_home and pmf_away are bit-for-bit
    # identical (both come from the same deterministic function of the same
    # values), which makes joint_ha and joint_ah bit-for-bit identical too,
    # and therefore p_home and p_away bit-for-bit identical - not merely
    # numerically close. See
    # tests/test_match_outcome_probs.py::test_equal_lambdas_give_home_and_away_win_probability_exactly.
    joint_ha = pmf_home[:, :, None] * pmf_away[:, None, :]
    joint_ah = pmf_away[:, :, None] * pmf_home[:, None, :]

    p_home = joint_ha[:, more].sum(axis=1)
    p_away = joint_ah[:, more].sum(axis=1)
    p_draw = joint_ha[:, equal].sum(axis=1)

    return np.stack([p_home, p_draw, p_away], axis=1)


def truncation_residual(lam, max_goals: int = 15) -> np.ndarray:
    """1 - sum_{k=0}^{max_goals} pmf(k; lam): the probability mass ONE
    team's goal distribution loses to truncating its support at max_goals.
    The two-sided residual match_outcome_probs carries is at most the sum of
    both teams' residuals (a union bound: P(h > max_goals or a > max_goals)
    <= P(h > max_goals) + P(a > max_goals)). Used to justify max_goals=15
    against this project's real data - see
    tests/test_match_outcome_probs.py.
    """
    pmf = _poisson_pmf_grid(np.asarray(lam, dtype=float), max_goals)
    return 1.0 - pmf.sum(axis=1)

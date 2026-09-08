"""Drawing each simulated season its own team strengths.

The fixed model reuses one estimate of every team's scoring rate across all
iterations, so it cannot express that the estimate might be wrong - which is why
a side whose parameters are partly borrowed from a newcomer prior is treated as
confidently as one backed by nineteen matches.

Goals are Poisson, and Gamma is its conjugate, so a rate's uncertainty is Gamma.
Parameterised here so its MEAN is exactly the fixed estimate: only the spread is
new, and no team becomes systematically stronger or weaker.

The four rates are drawn independently. In reality a team's attack and defence
are estimated from the same matches and are correlated, so this slightly
understates joint uncertainty. It is a deliberate simplification, flagged in
the C2 design doc rather than hidden here.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from brasileirao_simulator.domain.batch_simulation import ADJUSTMENT_WEIGHT, SeasonBaseline


@dataclass(frozen=True)
class TeamRateDraws:
    """One drawn strength per team per iteration, shape (iterations, n_teams).

    Constant across a team's matches within an iteration: this models "we may be
    underrating them", not "they got hot".
    """

    home_attack: np.ndarray
    home_defence: np.ndarray
    away_attack: np.ndarray
    away_defence: np.ndarray


def draw_team_rates(
    baseline: SeasonBaseline,
    iterations: int,
    rng: np.random.Generator,
    n_eff_scale: float = 1.0,
) -> TeamRateDraws:
    """Sample each team's four rates once per iteration.

    n_eff_scale multiplies the evidence count, so a large value collapses every
    draw onto the fixed estimate - which is how C2 is shown to contain C1.
    """
    return TeamRateDraws(
        home_attack=_draw(baseline.home_attack, baseline.home_match_count, iterations, rng, n_eff_scale),
        home_defence=_draw(baseline.home_defence, baseline.home_match_count, iterations, rng, n_eff_scale),
        away_attack=_draw(baseline.away_attack, baseline.away_match_count, iterations, rng, n_eff_scale),
        away_defence=_draw(baseline.away_defence, baseline.away_match_count, iterations, rng, n_eff_scale),
    )


def _draw(
    rates: np.ndarray,
    match_count: np.ndarray,
    iterations: int,
    rng: np.random.Generator,
    n_eff_scale: float,
) -> np.ndarray:
    """Gamma(shape=n_eff, scale=rate/n_eff): mean `rate`, variance rate^2/n_eff.

    A rate with no evidence behind it, or a rate of zero, has no distribution to
    draw from and is repeated unchanged.
    """
    n_eff = match_count * n_eff_scale
    drawable = (n_eff > 0) & (rates > 0)

    drawn = np.tile(rates, (iterations, 1))
    if drawable.any():
        shape = n_eff[drawable]
        scale = rates[drawable] / shape
        drawn[:, drawable] = rng.gamma(shape, scale, size=(iterations, drawable.sum()))

    return drawn


def fixture_lambdas(
    baseline: SeasonBaseline, draws: TeamRateDraws
) -> Tuple[np.ndarray, np.ndarray]:
    """Per-iteration expected goals per fixture, shape (iterations, n_games).

    The same 50/50 attack-and-defence blend the fixed path uses; only the inputs
    now vary by iteration.
    """
    lam_home = (
        ADJUSTMENT_WEIGHT * draws.home_attack[:, baseline.home_team]
        + ADJUSTMENT_WEIGHT * draws.away_defence[:, baseline.away_team]
    )
    lam_away = (
        ADJUSTMENT_WEIGHT * draws.away_attack[:, baseline.away_team]
        + ADJUSTMENT_WEIGHT * draws.home_defence[:, baseline.home_team]
    )
    return lam_home, lam_away

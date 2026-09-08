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
from typing import Optional, Tuple

import numpy as np

from brasileirao_simulator.domain.batch_simulation import ADJUSTMENT_WEIGHT, SeasonBaseline


# A team with zero real matches at a venue still has a rate - either
# MISSING_TEAM_AVERAGE or a newcomer-prior blend - but that number is the LEAST
# trustworthy one in the model. Treating it as a point mass (the old `n_eff > 0`
# guard) inverts the design's intent: no evidence should mean the WIDEST draw,
# not infinite confidence. This floor is a modelling choice, not a mathematical
# necessity - 3 matches' worth of equivalent evidence is a starting point
# calibrated by "the newcomer prior is worth something, but nowhere near a full
# 19-match window", not a derived constant. It applies before n_eff_scale and
# before any n_eff_override, so both still operate on top of it.
PRIOR_EQUIVALENT_MATCHES = 3


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
    n_eff_override: Optional[float] = None,
) -> TeamRateDraws:
    """Sample each team's four rates once per iteration.

    n_eff_scale multiplies the evidence count, so a large value collapses every
    draw onto the fixed estimate - which is how C2 is shown to contain C1.

    n_eff_override, when given, replaces `match_count * n_eff_scale` outright:
    every drawable team is assigned that n_eff directly, regardless of its real
    match count. This is what lets a caller genuinely test "n_eff = 19 for
    everyone" instead of a per-team scale that only some teams ever reach.
    """
    return TeamRateDraws(
        home_attack=_draw(
            baseline.home_attack, baseline.home_match_count, iterations, rng, n_eff_scale, n_eff_override
        ),
        home_defence=_draw(
            baseline.home_defence, baseline.home_match_count, iterations, rng, n_eff_scale, n_eff_override
        ),
        away_attack=_draw(
            baseline.away_attack, baseline.away_match_count, iterations, rng, n_eff_scale, n_eff_override
        ),
        away_defence=_draw(
            baseline.away_defence, baseline.away_match_count, iterations, rng, n_eff_scale, n_eff_override
        ),
    )


def _draw(
    rates: np.ndarray,
    match_count: np.ndarray,
    iterations: int,
    rng: np.random.Generator,
    n_eff_scale: float,
    n_eff_override: Optional[float] = None,
) -> np.ndarray:
    """Gamma(shape=n_eff, scale=rate/n_eff): mean `rate`, variance rate^2/n_eff.

    A team with no real matches is not point-mass certain - it is the LEAST
    certain case in the model, so its evidence is floored at
    PRIOR_EQUIVALENT_MATCHES rather than treated as zero. `rates > 0` is the
    only true degeneracy guard left: a rate of zero has no Gamma to draw from
    (mean zero is undefined) and is repeated unchanged regardless of evidence.
    """
    if n_eff_override is not None:
        n_eff = np.full(match_count.shape, float(n_eff_override))
    else:
        n_eff = np.maximum(match_count, PRIOR_EQUIVALENT_MATCHES) * n_eff_scale
    drawable = rates > 0

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

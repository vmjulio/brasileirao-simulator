"""Dixon-Coles club ratings: attack and defence solved for JOINTLY.

WHY THIS EXISTS. The model this project ships with computes each fixture's
lambda as 0.5 * (the attacker's own goals-for average at that venue) + 0.5 *
(the defender's goals-against average at that venue), read straight out of
files/queries/team_params_same_venue_average.sql. Both of those inputs are
MARGINAL averages: a club that happened to play the league's worst defences
scores more goals per game, and its goals-for average records that as
attacking quality. Opponent strength is confounded with club quality, and no
amount of reweighting the same marginal average can separate them, because
the information needed to separate them (who each goal was scored against)
has already been averaged away.

Dixon and Coles (1997) fix that by never forming a marginal average at all.
Every match is a statement about a PAIR of clubs, and all the pairs are
solved together, so that "who you played" cancels by construction:

    lambda_home = attack[home] * defence[away] * home_advantage
    lambda_away = attack[away] * defence[home]

Goals are Poisson around those means, with Dixon and Coles' correction to the
four low scorelines that independent Poissons visibly under-predict (0-0, 1-0,
0-1 and above all 1-1):

    tau(0,0) = 1 - lam_home*lam_away*rho     tau(1,0) = 1 + lam_away*rho
    tau(0,1) = 1 + lam_home*rho              tau(1,1) = 1 - rho
    tau(x,y) = 1 otherwise

A negative rho moves mass onto 0-0 and 1-1 and off 1-0/0-1 - i.e. it raises
the draw probability, which is exactly the direction this project's forecasts
need (2025's observed draw rate is ~28.5% against a model mean of ~24%). The
correction is mass-preserving: the four adjustments sum to zero identically.

WHY NO OPTIMISER LIBRARY. scipy is not a dependency here (see
requirements.txt) and this fit does not need one. Ignoring the rho correction,
setting the derivative of the weighted Poisson log-likelihood to zero gives a
closed-form fixed point in each rating:

    attack[i]  = (weighted goals i scored)   / (weighted sum of defence[opponent] * venue_factor)
    defence[i] = (weighted goals i conceded) / (weighted sum of attack[opponent] * venue_factor)
    home_advantage = (weighted home goals)   / (weighted sum of attack[home] * defence[away])

where venue_factor is home_advantage for i's home matches and 1 for its away
ones. Alternating those updates converges monotonically in practice and needs
nothing but arithmetic. rho is then a single scalar, found by scanning the
weighted log-likelihood on a grid and refining - a 1-D problem where a
gradient would be pure ceremony.

THE ONE DEGENERACY. Multiplying every attack by c and dividing every defence
by c leaves every lambda unchanged, so the likelihood alone does not pin the
scale. Rescaling to mean(attack) == 1 after each sweep resolves it, and makes
an attack rating directly readable as "this club scores N times the league
average club's rate against the same defence". Removing that one line makes
the ratings drift and the recovery test fail - see
tests/test_dixon_coles.py::test_attack_ratings_are_normalised.

xi IS UNTUNED. The time-decay half-life is Dixon and Coles' own published
value, not something fitted to Brazilian football. Sweeping it is deliberate
follow-up work, not part of the measurement this module was written for.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


# Dixon and Coles' own published decay rate: exp(-xi * days), half-life ~107
# days. UNTUNED here on purpose - see the module docstring.
DEFAULT_XI = 0.0065

# Grid the 1-D rho search starts on, before refining around its best point.
RHO_GRID = (-0.2, 0.2, 41)

# Keeps a rating away from exactly zero when a club's weighted goals in the
# window are zero (a promoted club with one 0-0, say): the fixed point would
# otherwise divide by it on the next sweep.
MIN_RATING = 1e-4


@dataclass
class Ratings:
    attack: dict     # team -> float, mean 1.0
    defence: dict    # team -> float
    home_advantage: float
    rho: float
    iterations: int
    converged: bool


def _match_rows(fixtures: pd.DataFrame, as_of_date: str, xi: float):
    """One row per MATCH (home team, away team, goals, weight).

    enriched_tidy_fixtures returns each match TWICE, once from each team's
    point of view. Filtering to `venue == 'home'` keeps exactly one row per
    match, with the home side as team_name - halving the weights instead
    would leave every match contributing from both directions and is only
    equivalent for the symmetric part of the likelihood, not for
    home_advantage. So: filter, do not halve.
    """
    played = fixtures[
        (fixtures["venue"] == "home")
        & fixtures["goals_for"].notnull()
        & fixtures["goals_against"].notnull()
    ]

    as_of = pd.Timestamp(as_of_date, tz="UTC")
    kickoff = pd.to_datetime(played["fixture_date"], utc=True, format="mixed")
    days_before = (as_of - kickoff).dt.total_seconds().to_numpy() / 86400.0
    # A fixture on the as-of date itself (or, in synthetic data, after it)
    # gets full weight rather than a weight above 1.
    days_before = np.clip(days_before, 0.0, None)

    return (
        played["team_name"].to_numpy(),
        played["opponent_name"].to_numpy(),
        played["goals_for"].to_numpy(dtype=float),
        played["goals_against"].to_numpy(dtype=float),
        np.exp(-xi * days_before),
    )


def _weighted_log_likelihood(goals_home, goals_away, lam_home, lam_away, weights, rho):
    """The Dixon-Coles weighted log-likelihood, up to constants that do not
    depend on the parameters (the log factorials)."""
    poisson = (
        goals_home * np.log(lam_home) - lam_home + goals_away * np.log(lam_away) - lam_away
    )
    tau = _tau(goals_home, goals_away, lam_home, lam_away, rho)
    if np.any(tau <= 0):
        return -np.inf
    return float(np.sum(weights * (poisson + np.log(tau))))


def _tau(goals_home, goals_away, lam_home, lam_away, rho):
    """Dixon and Coles' low-score correction factor, elementwise."""
    tau = np.ones_like(np.asarray(lam_home, dtype=float))
    home0 = goals_home == 0
    home1 = goals_home == 1
    away0 = goals_away == 0
    away1 = goals_away == 1
    tau = np.where(home0 & away0, 1.0 - lam_home * lam_away * rho, tau)
    tau = np.where(home1 & away0, 1.0 + lam_away * rho, tau)
    tau = np.where(home0 & away1, 1.0 + lam_home * rho, tau)
    tau = np.where(home1 & away1, 1.0 - rho, tau)
    return tau


def _fit_rho(goals_home, goals_away, lam_home, lam_away, weights) -> float:
    """rho by grid scan plus one refinement pass - a 1-D likelihood with no
    need for a gradient. The scan naturally skips any rho that drives a tau
    non-positive, because _weighted_log_likelihood returns -inf there."""

    def best_on(grid):
        scores = [
            _weighted_log_likelihood(goals_home, goals_away, lam_home, lam_away, weights, r)
            for r in grid
        ]
        return float(grid[int(np.argmax(scores))])

    low, high, points = RHO_GRID
    coarse = best_on(np.linspace(low, high, points))
    step = (high - low) / (points - 1)
    return best_on(np.linspace(coarse - step, coarse + step, 41))


def fit(
    fixtures: pd.DataFrame,
    as_of_date: str,
    xi: float = DEFAULT_XI,
    max_iterations: int = 200,
    tolerance: float = 1e-9,
) -> Ratings:
    """Fit attack, defence, home_advantage and rho from every match visible
    in `fixtures` as of `as_of_date`.

    `fixtures` is the frame Tables.enriched_tidy_fixtures returns - two rows
    per match, one per team's perspective; see _match_rows for how that is
    collapsed. Matches with no result (a blanked, not-yet-played fixture)
    are ignored.
    """
    home, away, goals_home, goals_away, weights = _match_rows(fixtures, as_of_date, xi)
    teams = sorted(set(home) | set(away))
    if not teams:
        raise ValueError("cannot fit Dixon-Coles ratings from zero played matches")

    index = {team: i for i, team in enumerate(teams)}
    home_idx = np.array([index[t] for t in home])
    away_idx = np.array([index[t] for t in away])
    n = len(teams)

    # Weighted goals each club scored and conceded - the numerators of the
    # fixed point, constant across sweeps.
    scored = np.bincount(home_idx, weights=weights * goals_home, minlength=n) + np.bincount(
        away_idx, weights=weights * goals_away, minlength=n
    )
    conceded = np.bincount(home_idx, weights=weights * goals_away, minlength=n) + np.bincount(
        away_idx, weights=weights * goals_home, minlength=n
    )

    attack = np.ones(n)
    defence = np.ones(n)
    home_advantage = 1.0

    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        previous = np.concatenate([attack, defence, [home_advantage]])

        # attack[i]: its own goals over the defensive resistance it faced.
        expected = np.bincount(
            home_idx, weights=weights * defence[away_idx] * home_advantage, minlength=n
        ) + np.bincount(away_idx, weights=weights * defence[home_idx], minlength=n)
        attack = np.maximum(scored / np.maximum(expected, MIN_RATING), MIN_RATING)

        # defence[i]: goals conceded over the attacking pressure it faced.
        expected = np.bincount(
            home_idx, weights=weights * attack[away_idx], minlength=n
        ) + np.bincount(
            away_idx, weights=weights * attack[home_idx] * home_advantage, minlength=n
        )
        defence = np.maximum(conceded / np.maximum(expected, MIN_RATING), MIN_RATING)

        home_advantage = float(
            np.sum(weights * goals_home)
            / max(np.sum(weights * attack[home_idx] * defence[away_idx]), MIN_RATING)
        )

        # The one degeneracy: attack * c and defence / c leave every lambda
        # untouched. Pin the scale at mean(attack) == 1, moving the same
        # factor onto defence so no lambda changes.
        scale = float(attack.mean())
        attack = attack / scale
        defence = defence * scale

        current = np.concatenate([attack, defence, [home_advantage]])
        if np.max(np.abs(current - previous)) < tolerance:
            converged = True
            break

    lam_home = attack[home_idx] * defence[away_idx] * home_advantage
    lam_away = attack[away_idx] * defence[home_idx]
    rho = _fit_rho(goals_home, goals_away, lam_home, lam_away, weights)

    return Ratings(
        attack={team: float(attack[i]) for team, i in index.items()},
        defence={team: float(defence[i]) for team, i in index.items()},
        home_advantage=home_advantage,
        rho=rho,
        iterations=iterations,
        converged=converged,
    )


def lambdas(ratings: Ratings, home_team: str, away_team: str) -> tuple:
    """(lambda_home, lambda_away) for one fixture.

    A club with no played matches behind it (a newly promoted side on the
    season's first as-of date) is absent from the fitted dicts entirely; it
    gets the league-average club's ratings rather than an error, which is the
    same "assume average until there is evidence" stance
    batch_simulation.MISSING_TEAM_AVERAGE takes.
    """
    mean_defence = float(np.mean(list(ratings.defence.values()))) if ratings.defence else 1.0
    attack_home = ratings.attack.get(home_team, 1.0)
    attack_away = ratings.attack.get(away_team, 1.0)
    defence_home = ratings.defence.get(home_team, mean_defence)
    defence_away = ratings.defence.get(away_team, mean_defence)

    return (
        attack_home * defence_away * ratings.home_advantage,
        attack_away * defence_home,
    )


def outcome_probs(lam_home: float, lam_away: float, rho: float, max_goals: int = 15) -> tuple:
    """(P(home win), P(draw), P(away win)) for one fixture, exact under the
    Dixon-Coles corrected joint Poisson truncated at `max_goals` goals per
    side - the same truncation domain/match_outcome_probs.py already uses,
    whose residual there is ~1e-7 on this project's real lambdas.

    Column order matches OUTCOMES in entrypoints/match_brier_backtest.py.
    """
    goals = np.arange(max_goals + 1)
    pmf_home = _poisson_pmf(lam_home, max_goals)
    pmf_away = _poisson_pmf(lam_away, max_goals)

    joint = pmf_home[:, None] * pmf_away[None, :]
    grid_home = np.repeat(goals[:, None], max_goals + 1, axis=1)
    grid_away = np.repeat(goals[None, :], max_goals + 1, axis=0)
    joint = joint * _tau(
        grid_home, grid_away, np.full_like(joint, lam_home), np.full_like(joint, lam_away), rho
    )

    more = grid_home > grid_away
    equal = grid_home == grid_away
    return (float(joint[more].sum()), float(joint[equal].sum()), float(joint[~(more | equal)].sum()))


def _poisson_pmf(lam: float, max_goals: int) -> np.ndarray:
    """P(X = k) for k = 0..max_goals, by the stable recurrence
    pmf(0) = exp(-lam), pmf(k) = pmf(k-1) * lam / k - no scipy, and no k! or
    lam**k ever formed (same construction as match_outcome_probs)."""
    pmf = np.empty(max_goals + 1)
    pmf[0] = np.exp(-lam)
    for k in range(1, max_goals + 1):
        pmf[k] = pmf[k - 1] * lam / k
    return pmf

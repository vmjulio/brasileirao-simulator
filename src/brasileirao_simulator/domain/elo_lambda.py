"""Turning an `EloHistory` into the two Poisson lambdas a simulated match
needs.

WHY A DECOMPOSITION. Elo gives a win probability, not goals - the logistic
expectation in `elo.py` says how much more often the home side should win,
but says nothing about how many goals either side should score. chancedegol's
decomposition (see
docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md,
section "From Elo to two lambdas") separates the two things a naive
50/50-of-something blend would conflate:

  - "how much better" one side is than the other - carried by the Elo
    difference, mapped through `DifferenceMap` to an expected 90-minute goal
    difference.
  - "how many goals" a match between the two tends to produce in total -
    carried by each club's opponent-adjusted contribution to total goals,
    the `total` half of `team_strength`.

`lambdas` (below) recombines the two exactly as the spec's pseudocode does:
`(expected_total +/- expected_difference) / 2`, floored at `params.eps` so
neither side is ever handed a non-positive Poisson rate.

FILE OWNERSHIP (parallel tickets - read before editing this file):
  - elo-difference-map implements `fit_difference_map` in
    `domain/elo_difference_map.py`; this module re-exports it.
  - elo-total-goals implements `total_goals_params` and
    `team_strength_with_totals` in `domain/elo_total_goals.py`; this module
    re-exports them.
  - elo-adapter owns `adapters/elo_adapter.py` and the optional
    `team_strength` input to `domain/batch_simulation.build_baseline`, and
    edits nothing in this file.
Each ticket edits only its own file; do not touch another ticket's stub body
or its dedicated module.
"""

from dataclasses import dataclass

import pandas as pd

from brasileirao_simulator.domain.elo import EloHistory
from brasileirao_simulator.domain.match_store import MatchStore


@dataclass(frozen=True)
class EloLambdaParams:
    """Parameters for the Elo -> lambda decomposition.

    home_advantage: rating points added to the home side before the
        difference map is evaluated - must match `EloParams.home_advantage`
        (both default to 85.0) so the same advantage that shaped the Elo
        replay also shapes the expected goal difference derived from it. A
        caller sweeping `EloParams.home_advantage` should pass the same value
        here.
    eps: the floor a lambda is clamped to in `lambdas` so neither side is
        ever handed a non-positive Poisson rate.
    burn_in_season: the season `fit_difference_map` regresses on. 2019 is the
        first season in `MatchStore` (see `docs/superpowers/plans/
        2026-09-10-multi-competition-kanban.md`, E5).
    """

    home_advantage: float = 85.0
    eps: float = 0.05
    burn_in_season: int = 2019


@dataclass(frozen=True)
class DifferenceMap:
    """A fitted, linear Elo-difference -> expected-goal-difference map.

    Deliberately linear (`intercept + slope * elo_difference`), not cubic or
    otherwise unbounded: a bigger Elo gap must never predict an ever-steeper
    goal difference. Monotone by construction whenever `slope > 0`, which
    `fit_difference_map` enforces by raising rather than returning a
    non-positive slope - see that function's docstring.

    Fitted once, on `fitted_on_season` only, and stored together with that
    season so a caller (or a later re-fit) always knows what data produced
    it; it is never fitted online or refreshed by later seasons.

    slope: goal-difference points per Elo point. Must be positive.
    intercept: expected goal difference at `elo_difference == 0`.
    fitted_on_season: the season `fit_difference_map` regressed on.
    n_matches: how many matches went into the regression.
    """

    slope: float
    intercept: float
    fitted_on_season: int
    n_matches: int

    def __call__(self, elo_difference: float) -> float:
        """The expected 90-minute goal difference for `elo_difference`
        rating points (home side's Elo, home advantage already added, minus
        the away side's Elo). Linear, so monotone in `elo_difference`
        whenever `self.slope > 0`."""
        return self.intercept + self.slope * elo_difference


def fit_difference_map(
    history: EloHistory, store: MatchStore, params: EloLambdaParams = EloLambdaParams()
) -> DifferenceMap:
    """Regress observed 90-minute goal difference on Elo difference over
    `params.burn_in_season` only, and return the fitted `DifferenceMap`.

    Implemented by elo-difference-map (`domain/elo_difference_map.py`).
    Contract:

      - Scope to the matches of `store.matches` whose `season ==
        params.burn_in_season`.
      - For each such match, look up both sides' `elo_before` from
        `history.ratings` (one row per side, per match; join on
        `fixture_id`) and compute the regressor
        `elo_before_home + params.home_advantage * (1 - is_neutral) -
        elo_before_away`, where `is_neutral` is the match's own flag - zero
        home advantage on a neutral pitch, matching how `elo.py`'s replay
        itself excludes it from the expectation.
      - The target is `home_goals - away_goals` (already the 90-minute
        result, per `MatchStore`'s contract).
      - Fit by ordinary least squares (regressor -> target), one match one
        row - not one row per side.
      - Return a `DifferenceMap` with that slope and intercept,
        `fitted_on_season=params.burn_in_season` and `n_matches` the number
        of matches regressed on.
      - Raise `ValueError` if the fitted slope is not strictly positive - a
        non-positive slope on this burn-in season is a data or bug signal
        that must stop the pipeline, not a value quietly passed downstream
        (see `DifferenceMap`'s docstring).
    """
    raise NotImplementedError("elo-difference-map")


def total_goals_params(store: MatchStore, as_of_date: str) -> dict[int, float]:
    """`{team_id: contribution}` - each club's opponent-adjusted
    contribution to the expected total goals of a match it plays in, fitted
    on `store.before(as_of_date)`.

    Implemented by elo-total-goals (`domain/elo_total_goals.py`). Contract:

      - Fit only on `store.before(as_of_date)` - matches strictly before the
        cutoff, the same no-leakage rule `elo_snapshots.py` documents.
      - A club's contribution starts from its average total goals (goals it
        scored plus goals it conceded) across its matches in that window,
        then is corrected for the average contribution of the opponents it
        faced - the same opponent-adjustment idea Dixon-Coles applies to
        attack/defence, applied here to one combined total-goals parameter
        per club instead of separate attack and defence rates.
      - Gate (see the ticket): summing `total_home + total_away` for every
        match of a season and taking the league mean must land within 2% of
        that season's observed mean total goals.
      - A club with no admitted match in the fit window is absent from the
        returned dict - never defaulted to a league-average contribution.
    """
    raise NotImplementedError("elo-total-goals")


def lambdas(
    elo_home: float,
    elo_away: float,
    total_home: float,
    total_away: float,
    difference_map: DifferenceMap,
    params: EloLambdaParams = EloLambdaParams(),
    is_neutral: bool = False,
) -> tuple[float, float]:
    """`(lambda_home, lambda_away)` from the Elo decomposition: `total_home +
    total_away` split by the expected goal difference the Elo gap implies,
    each side floored at `params.eps`.

    `is_neutral` drops `params.home_advantage` from the Elo gap fed to
    `difference_map`, matching how `elo.py`'s own replay excludes home
    advantage from the expectation on a neutral pitch.
    """
    expected_difference = difference_map(
        elo_home + params.home_advantage * (0 if is_neutral else 1) - elo_away
    )
    expected_total = total_home + total_away
    lam_home = max(params.eps, (expected_total + expected_difference) / 2)
    lam_away = max(params.eps, (expected_total - expected_difference) / 2)
    return lam_home, lam_away


def team_strength_with_totals(history: EloHistory, store: MatchStore, as_of_date: str) -> pd.DataFrame:
    """`elo_snapshots.team_strength(history, as_of_date)` with one added
    `total` column (`float64`) from `total_goals_params(store, as_of_date)`.
    Same row order as `team_strength`; a club absent from
    `total_goals_params`'s result gets `NaN` rather than being dropped. This
    is the spec's full `team_strength(team_id, as_of_date, elo, total,
    matches_used, competitions_used)` table.

    Implemented by elo-total-goals (`domain/elo_total_goals.py`).
    """
    raise NotImplementedError("elo-total-goals")

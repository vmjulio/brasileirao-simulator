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
from typing import Optional

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
    burn_in_season: the season `fit_difference_map` regresses on. `None`
        (the default) means the season before the one being forecast, which
        `EloAdapter` resolves; an int pins the line to that season whatever
        is forecast. Pinning to 2019 reproduces every Elo export made before
        elo-line-previous-season. The previous season never includes the
        forecast season or later, so no forecast ever sees its own results
        in the line (see `entrypoints/elo_backtest.py`).
    totals_window_days: how far back, in days, `total_goals_params` looks
        when fitting each club's total-goals contribution.
    """

    home_advantage: float = 85.0
    eps: float = 0.05
    burn_in_season: Optional[int] = None
    totals_window_days: int = 365


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


@dataclass(frozen=True)
class TeamStrength:
    """`team_strength_with_totals(...)` as of one date, plus what turns two
    rows of it into a lambda pair.

    frame: the `team_strength_with_totals` output - one row per club with
        (among other columns) `team_id`, `elo` and `total` (`NaN` for a club
        with no total-goals-window match, per that function's contract).
    difference_map: the fitted Elo-difference -> expected-goal-difference
        map `lambdas_for` feeds into `lambdas`.
    params: the `EloLambdaParams` `lambdas_for` uses - its `home_advantage`
        must match the `EloParams.home_advantage` that replayed `frame`'s
        Elo ratings.

    `__post_init__` builds a `{team_id: (elo, total)}` dict once (via
    `object.__setattr__`, since the dataclass is frozen) so `lambdas_for` is
    an O(1) lookup rather than a per-call DataFrame scan.
    """

    frame: pd.DataFrame
    difference_map: DifferenceMap
    params: EloLambdaParams

    def __post_init__(self) -> None:
        by_team = {
            int(row.team_id): (float(row.elo), float(row.total))
            for row in self.frame.itertuples(index=False)
        }
        object.__setattr__(self, "_by_team", by_team)

    def lambdas_for(
        self, home_id: int, away_id: int, is_neutral: bool = False
    ) -> Optional[tuple[float, float]]:
        """`lambdas(...)` for these two clubs' rows in `frame`, or `None`
        when either club is absent from `frame` or has a `NaN` `total` - the
        caller decides the fallback (see
        `domain/batch_simulation.build_baseline`)."""
        home = self._by_team.get(int(home_id))
        away = self._by_team.get(int(away_id))
        if home is None or away is None:
            return None
        elo_home, total_home = home
        elo_away, total_away = away
        if pd.isna(total_home) or pd.isna(total_away):
            return None
        return lambdas(
            elo_home, elo_away, total_home, total_away, self.difference_map, self.params, is_neutral
        )


def team_strength_as_of(
    history: EloHistory,
    store: MatchStore,
    as_of_date: str,
    difference_map: DifferenceMap,
    params: EloLambdaParams = EloLambdaParams(),
) -> TeamStrength:
    """Builds a `TeamStrength` from `team_strength_with_totals(history,
    store, as_of_date)`, `difference_map`, and `params`."""
    frame = team_strength_with_totals(history, store, as_of_date, params.totals_window_days)
    return TeamStrength(frame=frame, difference_map=difference_map, params=params)


# Re-exports of the two parallel tickets' implementations.
#
# `elo_total_goals` imports nothing from this module, so its re-export is a
# plain bottom-of-module import. `elo_difference_map` DOES import
# `DifferenceMap`/`EloLambdaParams` from here, so a plain import in either
# direction is a cycle that breaks whenever `elo_difference_map` is the first
# of the two imported in a process. A module-level `__getattr__` resolves the
# name on first access instead, after both modules are fully initialised, so
# `from elo_lambda import fit_difference_map` works in any import order.
from brasileirao_simulator.domain.elo_total_goals import (  # noqa: E402
    team_strength_with_totals,
    total_goals_params,
)


def __getattr__(name: str):
    if name == "fit_difference_map":
        from brasileirao_simulator.domain.elo_difference_map import fit_difference_map

        return fit_difference_map
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

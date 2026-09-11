"""DixonColesAllAdapter's structure, with the two lambdas built from Elo
instead of Dixon-Coles: `team_strength_as_of(...).lambdas_for(home_id,
away_id)` in place of `dixon_coles.lambdas(ratings, ...)` - see
domain/elo_lambda.py's module docstring for the "From Elo to two lambdas"
decomposition this reads from.

CACHED ONCE PER ADAPTER INSTANCE, NOT PER DATE. `replay` walks the whole
`MatchStore` in chronological order; `fit_difference_map` regresses once on
`lambda_params.burn_in_season` - by default the season before `season`, resolved
in `__init__`. Both are pure functions of `store`
and their params - nothing about either depends on the as-of date a
particular `build_baseline` call is for - so `__init__` runs them once and
every `build_baseline` call reuses `self.history`/`self.difference_map`.
Replaying the FULL store (matches after any given as-of date included) is
still safe for a snapshot at that date: `ratings_as_of`/`team_strength_with_
totals` (domain/elo_snapshots.py, domain/elo_total_goals.py) only ever read
rows strictly before their own cutoff, and `EloHistory.ratings` is built
sequentially in fixture order, so a club's `elo_after` at any row is a pure
function of matches strictly before that row - no future match's outcome
can move a past snapshot. Re-fitting per date would be wasted, identical
work.

WHAT STAYS THE SAME AS DixonColesAllAdapter. Same port, same
`vectorise_fixtures = False`, same `__init__` shape (`match_store=None`
constructs the real one), same reuse of the shipped `build_baseline` for
everything except the lambdas, same frontier/cutoff derivation in
`build_baseline` (copied from `DixonColesAllAdapter.fit_ratings`'s comment -
see that adapter's module docstring for why the cutoff is the frontier date
plus one day), same MEASUREMENT CANDIDATE status - `batch` remains the
default and nothing here changes that.
"""

import logging
from dataclasses import replace
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.domain.batch_simulation import (
    BatchOutcome,
    SeasonBaseline,
    build_baseline,
    simulate_batch,
)
from brasileirao_simulator.domain.elo import EloHistory, EloParams, replay
from brasileirao_simulator.domain.elo_lambda import (
    EloLambdaParams,
    fit_difference_map,
    team_strength_as_of,
)
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort

logger = logging.getLogger(__name__)


class EloAdapter(BatchSimulatorPort):
    """DixonColesAllAdapter's structure; the lambdas come from the Elo ->
    two-lambdas decomposition instead of a Dixon-Coles fit. See the module
    docstring for the once-per-instance caching and its safety argument.
    """

    vectorise_fixtures = False

    def __init__(
        self,
        strategy: Optional[str],
        season: int,
        rng: Optional[np.random.Generator] = None,
        match_store: Optional[MatchStore] = None,
        elo_params: EloParams = EloParams(),
        lambda_params: EloLambdaParams = EloLambdaParams(),
    ) -> None:
        self.con = duckdb.connect()
        self.strategy: Optional[str] = strategy
        self.season: int = season
        self.rng: Optional[np.random.Generator] = rng
        self.queries: Queries = Queries(season)
        self.match_store: MatchStore = match_store if match_store is not None else MatchStore()
        self.elo_params: EloParams = elo_params
        # An unpinned line is fitted on the season before the one forecast.
        if lambda_params.burn_in_season is None:
            lambda_params = replace(lambda_params, burn_in_season=season - 1)
        self.lambda_params: EloLambdaParams = lambda_params

        # See the module docstring: both are pure functions of `self.
        # match_store` and their own params, fitted once here rather than
        # per `build_baseline` call.
        self.history: EloHistory = replay(self.match_store, self.elo_params)
        self.difference_map = fit_difference_map(
            self.history, self.match_store, self.lambda_params
        )
        # One date's simulation arrives as many `simulate_batch` calls on the
        # same as-of state (200 batches of 100 at 20,000 iterations). The team
        # strengths depend only on the cutoff - history, store, line and
        # params are fixed per instance - so they are built once per cutoff,
        # not once per batch. Rebuilding them was ~70% of Elo's run time.
        self._strength_by_cutoff: dict = {}

    def build_baseline(
        self, fixtures: pd.DataFrame, remaining_games: pd.DataFrame
    ) -> SeasonBaseline:
        """The shipped baseline with `team_strength` supplied - see
        DixonColesAllAdapter.build_baseline, which this mirrors exactly
        except for how the lambdas are produced.
        """
        self.con.register("new_fixtures", fixtures)
        team_params = self.con.sql(self.queries.team_params_same_venue_average()).df()

        played = fixtures[fixtures["goals_for"].notnull()]
        as_of_date = str(pd.to_datetime(played["fixture_date"], format="mixed").max().date())

        # `MatchStore.before` is strictly "<" its cutoff; the frontier date's
        # own matches must still count (DixonColesAdapter's fixtures frame
        # keeps them - blank_from_date only blanks strictly AFTER 23:59:59 on
        # the as-of date), so the cutoff passed to MatchStore is the next
        # calendar day.
        cutoff = str((pd.Timestamp(as_of_date) + pd.Timedelta(days=1)).date())

        strength = self._strength_by_cutoff.get(cutoff)
        if strength is None:
            strength = team_strength_as_of(
                self.history, self.match_store, cutoff, self.difference_map, self.lambda_params
            )
            self._strength_by_cutoff[cutoff] = strength

        baseline = build_baseline(
            fixtures, remaining_games, team_params, self.season, team_strength=strength
        )
        if baseline.lambda_fallbacks:
            logger.debug(
                "EloAdapter.build_baseline: %d fixture(s) fell back to the "
                "same-venue-average lambda pair (as_of_date=%s, cutoff=%s)",
                baseline.lambda_fallbacks,
                as_of_date,
                cutoff,
            )
        return baseline

    def simulate_batch(
        self,
        fixtures: pd.DataFrame,
        remaining_games: pd.DataFrame,
        iterations: int,
    ) -> BatchOutcome:
        baseline = self.build_baseline(fixtures, remaining_games)
        return simulate_batch(
            baseline,
            iterations,
            self.rng if self.rng is not None else np.random.default_rng(),
            vectorise_fixtures=self.vectorise_fixtures,
        )

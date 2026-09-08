"""Batch simulators built on the same-venue-average team parameters.

Both adapters run the identical model; they differ only in whether the Poisson
draw loops over fixtures. The looping one is the default because collapsing
that loop forecloses ever letting a lambda change as a simulated season
unfolds. The collapsed one is kept so the trade-off can be measured.
"""

from typing import Optional

import duckdb
import pandas as pd

from brasileirao_simulator.domain.batch_simulation import (
    BatchOutcome,
    build_baseline,
    simulate_batch,
)
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort
import numpy as np


class IterationBatchAdapter(BatchSimulatorPort):
    """Draws every iteration of one fixture at a time."""

    vectorise_fixtures = False

    def __init__(
        self,
        strategy: Optional[str],
        season: int,
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        # Production runs are unseeded, so the default (None) draws a fresh
        # np.random.default_rng() per simulate_batch call below. The parameter
        # exists so a caller - a test, or a user wanting a reproducible run -
        # can pin the stream instead. Note this is a Generator, not the legacy
        # global RandomState: np.random.seed() has no effect on it, and vice
        # versa - the two are independent streams.
        self.con = duckdb.connect()
        self.strategy: Optional[str] = strategy
        self.season: int = season
        self.rng: Optional[np.random.Generator] = rng
        self.queries: Queries = Queries(season)

    def simulate_batch(
        self,
        fixtures: pd.DataFrame,
        remaining_games: pd.DataFrame,
        iterations: int,
    ) -> BatchOutcome:
        # Team parameters read the unsimulated fixtures, so this runs once for
        # the whole batch rather than once per simulated season.
        self.con.register("new_fixtures", fixtures)
        team_params = self.con.sql(self.queries.team_params_same_venue_average()).df()

        baseline = build_baseline(fixtures, remaining_games, team_params, self.season)
        return simulate_batch(
            baseline,
            iterations,
            self.rng if self.rng is not None else np.random.default_rng(),
            vectorise_fixtures=self.vectorise_fixtures,
        )


class FullVectorAdapter(IterationBatchAdapter):
    """Draws the entire batch in one call, collapsing the per-fixture loop.

    Measured no faster than IterationBatchAdapter (see README) - once the
    Python loop over seasons is gone, collapsing the remaining per-fixture
    loop buys nothing further. Kept so that comparison stays reproducible,
    and because it fixes every lambda for the whole simulated season, which
    IterationBatchAdapter deliberately avoids doing.
    """

    vectorise_fixtures = True

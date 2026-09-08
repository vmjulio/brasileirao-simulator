"""Batch simulator that draws each simulated season its own team strengths.

Models parameter uncertainty: how much a team's home_attack/home_defence/
away_attack/away_defence estimate should be trusted, given how many real
matches back it (fewer for a newly promoted side still filling its lookback
window). Each simulated season draws its own set of the four rates from a
Gamma centred on IterationBatchAdapter's fixed estimate (see
parameter_uncertainty.draw_team_rates), so C1's model is recovered exactly as
n_eff_scale grows - this adapter changes how confident the estimate is, never
what the estimate itself is.

It does NOT model form drift: a team's drawn rates are constant across every
remaining fixture within one simulated season (TeamRateDraws' own contract),
so "we may be underrating this team all along" is expressible but "they just
went on a hot streak" is not.
"""

from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.domain.batch_simulation import (
    BatchOutcome,
    build_baseline,
    simulate_batch,
)
from brasileirao_simulator.domain.parameter_uncertainty import draw_team_rates, fixture_lambdas
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort


class UncertainParamsAdapter(BatchSimulatorPort):
    """Draws each iteration's team strengths instead of reusing one fixed set."""

    def __init__(
        self,
        strategy: Optional[str],
        season: int,
        rng: Optional[np.random.Generator] = None,
        n_eff_scale: float = 1.0,
    ) -> None:
        # See IterationBatchAdapter's __init__ for why rng defaults to None
        # rather than eagerly building a generator: production runs are
        # unseeded, and a test or a reproducible run can still pin the stream.
        self.con = duckdb.connect()
        self.strategy: Optional[str] = strategy
        self.season: int = season
        self.rng: Optional[np.random.Generator] = rng
        self.n_eff_scale: float = n_eff_scale
        self.queries: Queries = Queries(season)

    def simulate_batch(
        self,
        fixtures: pd.DataFrame,
        remaining_games: pd.DataFrame,
        iterations: int,
    ) -> BatchOutcome:
        # Both queries read the unsimulated fixtures, so - like
        # IterationBatchAdapter - this runs once for the whole batch rather
        # than once per simulated season.
        self.con.register("new_fixtures", fixtures)
        team_params = self.con.sql(self.queries.team_params_same_venue_average()).df()
        match_counts = self.con.sql(self.queries.team_match_counts()).df()

        baseline = build_baseline(
            fixtures, remaining_games, team_params, self.season, match_counts=match_counts
        )

        rng = self.rng if self.rng is not None else np.random.default_rng()
        draws = draw_team_rates(baseline, iterations, rng, n_eff_scale=self.n_eff_scale)
        lam_home, lam_away = fixture_lambdas(baseline, draws)

        return simulate_batch(baseline, iterations, rng, lam_home=lam_home, lam_away=lam_away)

"""A batch simulator whose lambdas come from jointly fitted Dixon-Coles
ratings instead of per-venue marginal averages.

Everything about a simulated season except the two expected-goals numbers per
fixture is unchanged: the standings, the played results, the fixture order and
the Poisson draw all come from domain/batch_simulation.py exactly as
IterationBatchAdapter leaves them. Only lam_home and lam_away are replaced -
which is precisely the claim under test, since that pair is the entire
difference between the two models.

MEASUREMENT CANDIDATE, NOT A DEFAULT. This exists so Dixon-Coles can be scored
against the shipped model on real matches (see
entrypoints/dixon_coles_backtest.py). `batch` remains the default and nothing
here changes that.

WHAT THE SEASON-LEVEL PATH DOES NOT USE: rho. Dixon and Coles' low-score
correction is a statement about the JOINT distribution of the two scorelines,
and simulate_batch draws the two teams' goals as independent Poissons - there
is no place to apply it there without changing the shared simulation code this
module is forbidden to touch. So rho is fitted, carried on the Ratings object,
and used by the analytic outcome probabilities in the backtest (where it is
exact), while a season-level Monte Carlo through this adapter would see the
Dixon-Coles lambdas with rho = 0. That difference matters for draw
probabilities and nothing else.
"""

from dataclasses import replace
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.domain import dixon_coles
from brasileirao_simulator.domain.batch_simulation import (
    BatchOutcome,
    SeasonBaseline,
    build_baseline,
    simulate_batch,
)
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort


class DixonColesAdapter(BatchSimulatorPort):
    """IterationBatchAdapter's structure, with Dixon-Coles lambdas."""

    vectorise_fixtures = False

    def __init__(
        self,
        strategy: Optional[str],
        season: int,
        rng: Optional[np.random.Generator] = None,
        xi: float = dixon_coles.DEFAULT_XI,
    ) -> None:
        self.con = duckdb.connect()
        self.strategy: Optional[str] = strategy
        self.season: int = season
        self.rng: Optional[np.random.Generator] = rng
        self.xi: float = xi
        self.queries: Queries = Queries(season)
        self.ratings: Optional[dixon_coles.Ratings] = None

    def fit_ratings(self, fixtures: pd.DataFrame) -> dixon_coles.Ratings:
        """Fit on every played match visible in `fixtures` - both this season
        and the previous one, which is the same history window the shipped
        model's lookback reaches into.

        The as-of date is taken as the latest played fixture in the frame:
        `fixtures` has already been blanked from the as-of date onwards by
        Tables.enriched_tidy_fixtures, so that IS the frontier of what was
        known, and it keeps the port's signature untouched (simulate_batch is
        never told a date).
        """
        played = fixtures[fixtures["goals_for"].notnull()]
        as_of_date = str(pd.to_datetime(played["fixture_date"], format="mixed").max().date())
        self.ratings = dixon_coles.fit(fixtures, as_of_date, xi=self.xi)
        return self.ratings

    def build_baseline(
        self, fixtures: pd.DataFrame, remaining_games: pd.DataFrame
    ) -> SeasonBaseline:
        """The shipped baseline with its two lambda arrays swapped out.

        build_baseline still runs on the shipped team_params because the rest
        of SeasonBaseline (standings, played matches, fixture ids, the
        per-team rate arrays the loggers read) is genuinely shared; rebuilding
        it here would be duplication, and reusing it keeps any difference the
        backtest measures attributable to the lambdas alone.
        """
        self.con.register("new_fixtures", fixtures)
        team_params = self.con.sql(self.queries.team_params_same_venue_average()).df()
        baseline = build_baseline(fixtures, remaining_games, team_params, self.season)

        ratings = self.fit_ratings(fixtures)
        pairs = [dixon_coles.lambdas(ratings, home, away)
                 for home, away in zip(baseline.home_name, baseline.away_name)]
        lam_home = np.array([p[0] for p in pairs], dtype=float)
        lam_away = np.array([p[1] for p in pairs], dtype=float)

        return replace(baseline, lam_home=lam_home, lam_away=lam_away)

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

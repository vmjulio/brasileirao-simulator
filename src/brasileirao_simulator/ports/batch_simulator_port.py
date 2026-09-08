from abc import ABC, abstractmethod

import pandas as pd

from brasileirao_simulator.domain.batch_simulation import BatchOutcome


class BatchSimulatorPort(ABC):
    """A simulator whose unit of work is a batch of seasons.

    FixtureSimulatorPort's unit is a single season, which cannot express drawing
    many at once; rather than distort that port, this one sits alongside it.
    """

    season: int

    @abstractmethod
    def simulate_batch(
        self,
        fixtures: pd.DataFrame,
        remaining_games: pd.DataFrame,
        iterations: int,
    ) -> BatchOutcome:
        pass

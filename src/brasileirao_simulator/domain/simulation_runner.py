from typing import Any
import pandas as pd


class SimulationRunner:
    def __init__(
            self,
            fixtures: pd.DataFrame,
            remaining_games: pd.DataFrame,
            simulator: Any,
            logger: Any,
            persistence: Any,
            params: Any
    ) -> None:
        self.fixtures: pd.DataFrame = fixtures
        self.remaining_games: pd.DataFrame = remaining_games
        self.simulator: Any = simulator
        self.logger: Any = logger

        self.strategy: str = params.strategy
        self.iterations: int = params.iterations
        self.batch_size: int = params.max_batch_size
        self.load_results: bool = params.load_results

        self.persistence: Any = persistence

        if self.load_results:
            self.logger.load_results(self.persistence.load_results(self.strategy))

    def run(self) -> None:
        remaining_iterations = self.iterations

        while remaining_iterations > 0:
            current_batch_size = min(remaining_iterations, self.batch_size)
            print(f"Running batch of size: {current_batch_size}; Remaining iterations: {remaining_iterations}")

            for _ in range(current_batch_size):
                simulated_fixtures = self.simulator.simulate_fixtures(self.fixtures, self.remaining_games)
                bras_standings = self.simulator.get_brasileirao_standings(simulated_fixtures)
                bolao_standings = self.simulator.get_bolao_standings(simulated_fixtures)

                self.logger.log_brasileirao_results(bras_standings)
                self.logger.log_bolao_results(bolao_sta

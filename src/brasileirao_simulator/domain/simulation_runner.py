from typing import Any
import pandas as pd

from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort


class SimulationRunner:
    def __init__(self,
                 fixtures: Any,
                 remaining_games: Any,
                 simulator: Any,
                 logger: Any,
                 persistence: Any,
                 params: Any,
                 file_suffix: str = "") -> None:
        self.fixtures = fixtures
        self.remaining_games = remaining_games
        self.simulator = simulator
        self.logger = logger

        self.strategy: str = params.strategy
        self.iterations: int = params.iterations
        self.batch_size: int = params.max_batch_size
        self.load_results: bool = params.load_results

        self.persistence = persistence
        self.file_suffix = file_suffix

        if self.load_results:
            self.logger.load_results(self.persistence.load_results(strategy=self.strategy, suffix=self.file_suffix))

    def run(self) -> None:
        remaining_iterations: int = self.iterations

        while remaining_iterations > 0:
            current_batch_size: int = min(remaining_iterations, self.batch_size)
            print(f"Running batch of size: {current_batch_size}; Remaining iterations: {remaining_iterations}")

            if isinstance(self.simulator, BatchSimulatorPort):
                self._run_batch(current_batch_size)
            else:
                self._run_one_at_a_time(current_batch_size)

            self.persistence.save_results(results=self.logger.get_results(), strategy=self.strategy, suffix=self.file_suffix)
            remaining_iterations -= current_batch_size

    def _run_batch(self, batch_size: int) -> None:
        outcome = self.simulator.simulate_batch(self.fixtures, self.remaining_games, batch_size)
        teams = sorted(self.fixtures[self.fixtures["season"] == self.simulator.season]["team_name"].unique())
        self.logger.log_batch(outcome, teams)

    def _run_one_at_a_time(self, batch_size: int) -> None:
        for _ in range(batch_size):
            simulated_fixtures = self.simulator.simulate_fixtures(self.fixtures, self.remaining_games)
            bras_standings = self.simulator.get_brasileirao_standings(simulated_fixtures)
            match_results = self.simulator.get_match_results(simulated_fixtures)

            self.logger.log_brasileirao_results(bras_standings)
            self.logger.log_brasileirao_relegation_points(bras_standings)
            self.logger.log_brasileirao_positions(bras_standings)
            self.logger.log_match_results(match_results)

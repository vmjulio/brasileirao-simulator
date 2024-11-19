from typing import Any


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

            for _ in range(current_batch_size):
                simulated_fixtures = self.simulator.simulate_fixtures(self.fixtures, self.remaining_games)
                bras_standings = self.simulator.get_brasileirao_standings(simulated_fixtures)
                bolao_standings = self.simulator.get_bolao_standings(simulated_fixtures)
                match_results = self.simulator.get_match_results(simulated_fixtures)

                self.logger.log_brasileirao_results(bras_standings)
                self.logger.log_bolao_results(bolao_standings)
                self.logger.log_match_results(match_results)

            self.persistence.save_results(results=self.logger.get_results(), strategy=self.strategy, suffix=self.file_suffix)
            remaining_iterations -= current_batch_size

        self.logger.print_results()
        self.logger.print_matches()

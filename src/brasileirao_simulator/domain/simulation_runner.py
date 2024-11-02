from typing import Any


class SimulationRunner:
    def __init__(self,
                 fixtures: Any,
                 remaining_games: Any,
                 simulator: Any,
                 logger: Any,
                 persistence: Any,
                 params: Any) -> None:
        self.fixtures = fixtures
        self.remaining_games = remaining_games
        self.simulator = simulator
        self.logger = logger

        self.strategy: str = params.strategy
        self.iterations: int = params.iterations
        self.batch_size: int = params.max_batch_size
        self.load_results: bool = params.load_results

        self.persistence = persistence

        if self.load_results:
            self.logger.load_results(self.persistence.load_results(self.strategy))

    def run(self) -> None:
        remaining_iterations: int = self.iterations

        while remaining_iterations > 0:
            current_batch_size: int = min(remaining_iterations, self.batch_size)
            print(f"Running batch of size: {current_batch_size}; Remaining iterations: {remaining_iterations}")

            for _ in range(current_batch_size):
                simulated_fixtures = self.simulator.simulate_fixtures(self.fixtures, self.remaining_games)
                bras_standings = self.simulator.get_brasileirao_standings(simulated_fixtures)
                bolao_standings = self.simulator.get_bolao_standings(simulated_fixtures)

                self.logger.log_brasileirao_results(bras_standings)
                self.logger.log_bolao_results(bolao_standings)

            self.persistence.save_results(self.logger.get_results(), self.strategy)
            remaining_iterations -= current_batch_size

        self.logger.print_results()
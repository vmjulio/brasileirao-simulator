from brasileirao_simulator.ports.persistence_port import PersistencePort
from brasileirao_simulator.ports.fixture_simulator_port import FixtureSimulatorPort
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.domain.simulation_runner import SimulationRunner
from brasileirao_simulator.domain.result_logger import ResultLogger
from brasileirao_simulator.domain.season_data import SeasonData


class SimulationService:
    def __init__(
        self,
        persistence_adapter: PersistencePort,
        simulator_adapter: FixtureSimulatorPort,
        params: SimulationParams
    ) -> None:
        self.persistence_adapter: PersistencePort = persistence_adapter
        self.simulator_adapter: FixtureSimulatorPort = simulator_adapter
        self.params: SimulationParams = params

    def run_simulation(self, print_results: bool = False) -> None:
        tables = Tables(SeasonData(self.params.season))

        simulation_runner = SimulationRunner(
            fixtures=tables.enriched_tidy_fixtures(blank_from_date=self.params.ignore_results_after),
            remaining_games=tables.remaining_games(blank_from_date=self.params.ignore_results_after),
            params=self.params,
            simulator=self.simulator_adapter,
            logger=ResultLogger(),
            persistence=self.persistence_adapter,
            file_suffix=self.params.ignore_results_after
        )

        simulation_runner.run()

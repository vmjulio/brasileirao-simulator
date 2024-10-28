from brasileirao_simulator.ports.persistence_port import PersistencePort
from brasileirao_simulator.ports.fixture_simulator_port import FixtureSimulatorPort
from brasileirao_simulator.domain.simulation import Simulation
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.domain.simulation_runner import SimulationRunner
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.domain.result_logger import ResultLogger


class SimulationService:
    def __init__(self, persistence_adapter: PersistencePort, simulator_adapter: FixtureSimulatorPort, params: SimulationParams):
        self.persistence_adapter = persistence_adapter
        self.simulator_adapter = simulator_adapter
        self.params = params

    def run_simulation(self):
        tables = Tables()

        # Create the simulation runner
        simulation_runner = SimulationRunner(
            fixtures=tables.enriched_tidy_fixtures(),
            remaining_games=tables.remaining_games(),
            params=self.params,
            simulator=self.simulator_adapter,
            logger=ResultLogger(),
            persistence=self.persistence_adapter,
        )

        # Run the simulation
        simulation_runner.run()

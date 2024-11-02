from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.fixtures_simulator_adapter import FixtureSimulatorAdapter


def main() -> None:
    persistence_adapter: PickleAdapter = PickleAdapter(RESULTS_DIRECTORY)
    params: SimulationParams = SimulationParams(iterations=10000, max_batch_size=1000, load_results=True)
    simulator_adapter: FixtureSimulatorAdapter = FixtureSimulatorAdapter(params.strategy)

    simulation_service = SimulationService(persistence_adapter=persistence_adapter,
                                           simulator_adapter=simulator_adapter,
                                           params=params)

    simulation_service.run_simulation()


if __name__ == "__main__":
    main()
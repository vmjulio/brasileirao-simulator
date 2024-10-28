from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.fixtures_simulator_adapter import FixtureSimulatorAdapter


def main():
    persistence_adapter = PickleAdapter(RESULTS_DIRECTORY)
    params = SimulationParams(iterations=100, load_results=False)
    simulator_adapter = FixtureSimulatorAdapter(params.strategy)

    simulation_service = SimulationService(persistence_adapter=persistence_adapter,
                                           simulator_adapter=simulator_adapter,
                                           params=params)

    simulation_service.run_simulation()


if __name__ == "__main__":
    main()

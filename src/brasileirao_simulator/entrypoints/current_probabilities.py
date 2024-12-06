from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import PoissonSameVenueAverageAdapter
from brasileirao_simulator.ports.fixture_simulator_port import FixtureSimulatorPort


def current_probabilities(date: str = None) -> None:
    persistence_adapter: PickleAdapter = PickleAdapter(RESULTS_DIRECTORY)
    params: SimulationParams = SimulationParams(iterations=400,
                                                max_batch_size=25,
                                                load_results=False)
    simulator_adapter: FixtureSimulatorPort = PoissonSameVenueAverageAdapter(params.strategy)

    simulation_service = SimulationService(persistence_adapter=persistence_adapter,
                                           simulator_adapter=simulator_adapter,
                                           params=params)

    simulation_service.run_simulation()


if __name__ == "__main__":
    current_probabilities()

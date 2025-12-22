from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY, BACKFILL_DATES
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import PoissonSameVenueAverageAdapter


def backfill(date: str = None) -> None:
    persistence_adapter: PickleAdapter = PickleAdapter(RESULTS_DIRECTORY)
    params: SimulationParams = SimulationParams(iterations=200,
                                                max_batch_size=100,
                                                ignore_results_after=date,
                                                load_results=True)
    simulator_adapter: FixtureSimulatorPort = PoissonSameVenueAverageAdapter(params.strategy)

    simulation_service = SimulationService(persistence_adapter=persistence_adapter,
                                           simulator_adapter=simulator_adapter,
                                           params=params)

    simulation_service.run_simulation(print_results=False)


if __name__ == "__main__":
    for date in BACKFILL_DATES:
        backfill(date)

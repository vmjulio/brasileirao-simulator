import argparse

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.entrypoints.simulators import simulator_for
from brasileirao_simulator.service_layer.simulation_service import SimulationService


def current_probabilities(
    season: int, iterations: int = 100, date: str = None, simulator: str = "loop"
) -> None:
    params = SimulationParams(
        season=season,
        iterations=iterations,
        max_batch_size=25,
        ignore_results_after=date,
        load_results=False,
    )
    simulation_service = SimulationService(
        persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season),
        simulator_adapter=simulator_for(simulator, params.strategy, season),
        params=params,
    )
    simulation_service.run_simulation()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate a season's remaining fixtures.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--date", default=None, help="simulate as of this date (YYYY-MM-DD)")
    parser.add_argument(
        "--simulator",
        choices=["loop", "batch", "vector"],
        default="loop",
        help="loop is the reference implementation; batch and vector are faster.",
    )
    args = parser.parse_args()

    current_probabilities(args.season, args.iterations, args.date, args.simulator)

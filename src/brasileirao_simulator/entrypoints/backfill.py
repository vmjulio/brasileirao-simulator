"""Replay a season day by day, simulating as of each date.

Dates come from the season's own dates.json. --from-date/--to-date select a
slice, replacing the old habit of commenting entries out of a settings list.
"""

import argparse

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService


def backfill_dates(season: int, from_date: str = None, to_date: str = None) -> list[str]:
    dates = SeasonData(season).dates
    if from_date:
        dates = [d for d in dates if d >= from_date]
    if to_date:
        dates = [d for d in dates if d <= to_date]
    return dates


def backfill(season: int, date: str, iterations: int = 200) -> None:
    params = SimulationParams(
        season=season,
        iterations=iterations,
        max_batch_size=100,
        ignore_results_after=date,
        load_results=True,
    )
    simulation_service = SimulationService(
        persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season),
        simulator_adapter=PoissonSameVenueAverageAdapter(params.strategy, season),
        params=params,
    )
    simulation_service.run_simulation(print_results=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()

    for date in backfill_dates(args.season, args.from_date, args.to_date):
        print(f"backfilling {args.season} as of {date}")
        backfill(args.season, date, args.iterations)

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


def pending_dates(dates: list[str], persistence: PickleAdapter, strategy: str) -> list[str]:
    """Dates with no results yet.

    Re-running a date does not replace its results, it adds to them: backfill runs
    with load_results=True, so the runner seeds its logger from the existing pickle.
    Skipping finished dates keeps a repeated `make all` idempotent, which is what
    the old commented-out BACKFILL_DATES list did by hand.
    """
    return [d for d in dates if persistence.load_results(strategy, suffix=d) is None]


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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replay dates that already have results, adding iterations to them.",
    )
    args = parser.parse_args()

    strategy = SimulationParams(season=args.season).strategy
    dates = backfill_dates(args.season, args.from_date, args.to_date)

    if not args.force:
        persistence = PickleAdapter(RESULTS_DIRECTORY, args.season)
        pending = pending_dates(dates, persistence, strategy)
        skipped = len(dates) - len(pending)
        if skipped:
            print(f"skipping {skipped} date(s) with existing results (use --force to replay)")
        dates = pending

    for date in dates:
        print(f"backfilling {args.season} as of {date}")
        backfill(args.season, date, args.iterations)

"""Replay a season day by day, simulating as of each date.

Dates come from the season's own dates.json, and stop at its last result unless
--to-date says otherwise. --from-date/--to-date select a slice, replacing the
old habit of commenting entries out of a settings list.
"""

import argparse

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.season_dates import latest_result_date
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.entrypoints.simulators import simulator_for
from brasileirao_simulator.service_layer.simulation_service import SimulationService


def backfill_dates(season: int, from_date: str = None, to_date: str = None) -> list[str]:
    """The dates to replay, oldest first.

    Without an explicit to_date the replay stops at the season's last result:
    a mid-season fixture list runs months past today, and simulating as of a
    date that has not happened yet just repeats the latest snapshot.
    """
    season_data = SeasonData(season)
    dates = season_data.dates

    if to_date is None:
        to_date = latest_result_date(season_data.fixtures)
        if to_date is None:
            return []

    if from_date:
        dates = [d for d in dates if d >= from_date]
    return [d for d in dates if d <= to_date]


def pending_dates(dates: list[str], persistence: PickleAdapter, strategy: str) -> list[str]:
    """Dates with no results yet.

    Re-running a date does not replace its results, it adds to them: backfill runs
    with load_results=True, so the runner seeds its logger from the existing pickle.
    Skipping finished dates keeps a repeated `make all` idempotent, which is what
    the old commented-out BACKFILL_DATES list did by hand.
    """
    return [d for d in dates if persistence.load_results(strategy, suffix=d) is None]


def backfill(season: int, date: str, iterations: int = 200, simulator: str = "loop") -> None:
    params = SimulationParams(
        season=season,
        iterations=iterations,
        max_batch_size=100,
        ignore_results_after=date,
        load_results=True,
    )
    simulation_service = SimulationService(
        persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season),
        simulator_adapter=simulator_for(simulator, params.strategy, season),
        params=params,
    )
    simulation_service.run_simulation(print_results=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--from-date", default=None)
    parser.add_argument(
        "--to-date",
        default=None,
        help="Last date to replay. Defaults to the season's last result.",
    )
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replay dates that already have results, adding iterations to them.",
    )
    parser.add_argument(
        "--simulator",
        choices=["loop", "batch", "uncertain"],
        default="loop",
        help=(
            "loop is the reference implementation; batch is faster; uncertain "
            "is batch with per-iteration parameter uncertainty."
        ),
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
        backfill(args.season, date, args.iterations, args.simulator)

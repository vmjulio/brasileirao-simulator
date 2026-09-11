"""Top every date of a season up to a target iteration count, running only the shortfall.

The archive was built piecemeal: some seasons ran a flat 20,000 per date, others
were accumulated during the season at whatever budget was passed at the time, and
some dates were never simulated at all. `backfill.py --force` adds a fixed amount
to every date in range, which either overshoots the dates that are already deep or
leaves the thin ones short.

This computes each date's shortfall and runs exactly that, so a date already at or
above the target costs nothing.

One case needs a rebuild rather than a top-up: pickles written before
`brasileirao_relegation_points` and `brasileirao_positions` existed cannot be
resumed - the runner raises KeyError seeding its logger from them. Those are
deleted and re-run from zero, which also discards any `bolao` standings they hold,
since the current ResultLogger no longer writes that field. Back them up first;
--allow-rebuild has to be passed explicitly before this happens.
"""

import argparse
import json
import os
import pickle

from brasileirao_simulator.config.explorer_models import EXPLORER_MODELS, explorer_model
from brasileirao_simulator.config.settings import DATASETS_PATH, RESULTS_DIRECTORY
from brasileirao_simulator.entrypoints.backfill import backfill
from brasileirao_simulator.domain.season_dates import latest_result_date
from brasileirao_simulator.domain.season_data import SeasonData

REQUIRED_KEYS = ("brasileirao_relegation_points", "brasileirao_positions")


def inspect(path: str) -> tuple:
    """(iterations, resumable) for one pickle, or (0, True) when it does not exist."""
    if not os.path.isfile(path):
        return 0, True
    with open(path, "rb") as f:
        payload = pickle.load(f)
    resumable = all(key in payload for key in REQUIRED_KEYS)
    return sum(payload["brasileirao_title"].values()), resumable


def plan(season: int, target: int, results_directory: str = RESULTS_DIRECTORY) -> list:
    """One entry per date needing work: (date, shortfall, stale_pickle_to_remove)."""
    season_data = SeasonData(season)
    last = latest_result_date(season_data.fixtures)
    dates = [d for d in season_data.dates if last is None or d <= last]

    work = []
    for date in dates:
        path = f"{results_directory}/{season}/average_results_{date}.pkl"
        iterations, resumable = inspect(path)

        if not resumable:
            # Cannot be resumed: the whole target has to be re-run from zero.
            work.append((date, target, path))
        elif iterations < target:
            work.append((date, target - iterations, None))

    return work


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", required=True, help="comma-separated")
    parser.add_argument("--target", type=int, default=20000)
    parser.add_argument("--simulator", default="batch", choices=["loop", "batch", "uncertain"])
    parser.add_argument(
        "--allow-rebuild",
        action="store_true",
        help="delete and re-run pickles too old to resume from (destroys their bolao field)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--model",
        choices=sorted(EXPLORER_MODELS),
        default=None,
        help="an explorer model: sets the simulator and the pickle directory (overrides --simulator)",
    )
    args = parser.parse_args()
    simulator, results_directory = args.simulator, RESULTS_DIRECTORY
    if args.model:
        model = explorer_model(args.model)
        simulator, results_directory = model.simulator, model.results_directory

    for season in [int(s) for s in args.seasons.split(",")]:
        work = plan(season, args.target, results_directory)
        rebuilds = [entry for entry in work if entry[2]]

        print(f"season {season}: {len(work)} date(s) need work, {len(rebuilds)} need a rebuild")
        print(f"   iterations to run: {sum(shortfall for _, shortfall, _ in work):,}")

        if rebuilds and not args.allow_rebuild:
            print(f"   refusing: {len(rebuilds)} pickle(s) cannot be resumed; pass --allow-rebuild")
            continue
        if args.dry_run:
            continue

        for index, (date, shortfall, stale) in enumerate(work, start=1):
            if stale:
                os.remove(stale)
            print(f"   [{index}/{len(work)}] {date} +{shortfall}", flush=True)
            backfill(season, date, shortfall, simulator, results_directory)

        print(f"season {season}: done")

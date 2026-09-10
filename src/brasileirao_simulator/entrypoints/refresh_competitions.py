"""Refresh the current season for every live-pulled competition, in one command.

    python3 brasileirao_simulator/entrypoints/refresh_competitions.py [--pull]

Three steps, always in this order:

  1. (only with --pull) run lean-pype's live extraction for `LIVE_LEAGUES` /
     --season, via docker-compose. This is a paid API-Football request, so
     the default is --no-pull: the entrypoint never spends money unless a
     caller explicitly opts in.
  2. shard each league's `processed_fixtures_{season}_{league}.csv` export
     (`shard_competitions.shard`, unmodified) into
     `competitions/{league}/{season}.csv`, overwriting whatever was there -
     the shard is the source of truth for that (league, season), so an
     overwrite is not a hazard to guard against.
  3. load a `MatchStore` off the refreshed tree and print `coverage(season)`,
     the manifest T2.1 already uses to say what a store actually contains.

IDEMPOTENCE. `shard()` writes bytes deterministically from the source file's
own row order, and `MatchStore` dedupes on `fixture_id`. Re-running this
entrypoint against an unchanged source therefore reproduces the same shard
bytes and the same store: the ticket's gate ("running it twice in a row is a
no-op on the second run") falls out of those two behaviours rather than
needing new dedupe logic here.

LEAGUE 71 IS NOT IN `LIVE_LEAGUES`. `shard_competitions.py`'s module
docstring is explicit: Série A's current season comes from the
`datasets/{season}/fixtures.csv` pipeline, not "the live pull" - "the live
pull" is what T7.1 added LEAGUES/SEASONS for, and it means the rest of
`COMPETITIONS` (72, 73, 13, 11). This entrypoint refreshes exactly that set;
71's current season is a different ticket's problem.

WHY --dry-run PROVES THE PULL COMPOSES WITHOUT SPENDING MONEY. `pull()` is
the only function that can reach the API-Football key, and it does so by
shelling out to `docker-compose run ... extraction` in the lean-pype repo -
never imported or called here. `--dry-run` renders the exact command
`--pull` would run (LEAGUES, SEASONS, the docker-compose invocation) and the
per-league shard plan, then returns before `pull()` or `shard()` is ever
called - the same proof the extract-leagues-param gate used, without needing
lean-pype's own dependencies importable from this repo's test environment.
"""

import argparse
import datetime
import os
import subprocess

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.competitions import COMPETITIONS
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.entrypoints.shard_competitions import shard

# Every id in COMPETITIONS except 71 - see "LEAGUE 71 IS NOT IN LIVE_LEAGUES"
# above. Order matches the LEAGUES=72,73,13,11 the pull was planned against.
LIVE_LEAGUES = tuple(league_id for league_id in COMPETITIONS if league_id != 71)

LEAN_PYPE_DIR = os.path.expanduser("~/Documents/GitHub/lean-pype")
DEFAULT_SOURCE_DIR = f"{LEAN_PYPE_DIR}/app/files"
DEFAULT_OUT_ROOT = f"{DATASETS_PATH}/competitions"


def format_pull_command(season: int, leagues: tuple, lean_pype_dir: str) -> str:
    """The exact shell invocation `pull()` runs, as a string - shared by the
    real run's announcement and `--dry-run`'s preview so the two can never
    drift apart."""
    league_list = ",".join(str(league_id) for league_id in leagues)
    return (
        f"LEAGUES={league_list} SEASONS={season} docker-compose run --rm "
        f'-e LEAGUES -e SEASONS -v "{lean_pype_dir}/app:/app" extraction'
    )


def pull(season: int, leagues: tuple = LIVE_LEAGUES, lean_pype_dir: str = LEAN_PYPE_DIR) -> None:
    """Spend the paid API-Football quota: run lean-pype's live extraction for
    `leagues`/`season`. Reachable only through `refresh(..., do_pull=True)`,
    which itself is reachable only through the CLI's --pull - the default
    invocation of this entrypoint never calls this function."""
    env = dict(os.environ, LEAGUES=",".join(str(league_id) for league_id in leagues), SEASONS=str(season))
    subprocess.run(
        [
            "docker-compose", "run", "--rm",
            "-e", "LEAGUES", "-e", "SEASONS",
            "-v", f"{lean_pype_dir}/app:/app",
            "extraction",
        ],
        cwd=lean_pype_dir,
        env=env,
        check=True,
    )


def shard_plan(season: int, leagues: tuple, source_dir: str, out_root: str) -> list:
    """`(league_id, source_path, dest_path)` for every league this run would
    shard - the plan `--dry-run` prints and `shard_all` executes."""
    return [
        (
            league_id,
            os.path.join(source_dir, f"processed_fixtures_{season}_{league_id}.csv"),
            os.path.join(out_root, str(league_id), f"{season}.csv"),
        )
        for league_id in leagues
    ]


def shard_all(season: int, leagues: tuple, source_dir: str, out_root: str) -> dict:
    """Shard every league's export via `shard_competitions.shard`, one
    league at a time so one league's export never sees another league's
    fixture_ids - `shard`'s own duplicate check is scoped to a single call.
    Returns `{league_id: rows written}`."""
    row_counts = {}
    for league_id, source_path, _ in shard_plan(season, leagues, source_dir, out_root):
        counts = shard(source_path, (league_id,), season + 1, out_root)
        row_counts[league_id] = sum(counts[str(league_id)].values())
    return row_counts


def refresh(
    season: int,
    source_dir: str = DEFAULT_SOURCE_DIR,
    out_root: str = DEFAULT_OUT_ROOT,
    leagues: tuple = LIVE_LEAGUES,
    lean_pype_dir: str = LEAN_PYPE_DIR,
    do_pull: bool = False,
    dry_run: bool = False,
):
    """Run the three-step refresh described in the module docstring.

    Returns `MatchStore(root=out_root).coverage(season)`, or `None` under
    `--dry-run` (nothing was loaded, because nothing was written).
    """
    if dry_run:
        status = "would run" if do_pull else "skipped - pass --pull to run it"
        print(f"[dry-run] pull ({status}): {format_pull_command(season, leagues, lean_pype_dir)}")
        for league_id, source_path, dest_path in shard_plan(season, leagues, source_dir, out_root):
            print(f"[dry-run] shard: {source_path} -> {dest_path}")
        return None

    if do_pull:
        print(f"pulling: {format_pull_command(season, leagues, lean_pype_dir)}")
        pull(season, leagues, lean_pype_dir)

    row_counts = shard_all(season, leagues, source_dir, out_root)
    for league_id, _, dest_path in shard_plan(season, leagues, source_dir, out_root):
        print(f"league {league_id}: {row_counts[league_id]} row(s) -> {dest_path}")

    store = MatchStore(root=out_root)
    coverage = store.coverage(season)
    print(f"coverage[{season}]: {coverage}")
    return coverage


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=datetime.date.today().year)
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR, help="lean-pype's app/files, by default")
    parser.add_argument(
        "--pull",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Actually run the paid live extraction before sharding. Default --no-pull.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the pull command and the per-league shard plan; write nothing.",
    )
    args = parser.parse_args(argv)

    refresh(season=args.season, source_dir=args.source_dir, do_pull=args.pull, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

"""Refresh the cup and Série B seasons from the extractor's exports.

    python3 brasileirao_simulator/entrypoints/refresh_competitions.py

Two steps:

  1. shard each league's `processed_fixtures_{season}_{league}.csv` export
     (`shard_competitions.shard`, unmodified) into
     `competitions/{league}/{season}.csv`, overwriting whatever was there -
     the shard is the source of truth for that (league, season), so an
     overwrite is not a hazard to guard against.
  1b. mirror Série A: shard `datasets/{season}/fixtures.csv` into
     `competitions/71/{season}.csv` the same way (see "LEAGUE 71" below).
  2. load a `MatchStore` off the refreshed tree and print `coverage(season)`,
     the manifest T2.1 already uses to say what a store actually contains.

FETCHING IS NOT THIS REPO'S JOB ANY MORE. This used to carry a `--pull` that
shelled into a sibling checkout to spend the API-Football quota. That repo is
now `data-brasileirao-extractor`, runs on GitHub Actions, and publishes to S3 -
so the pull was pointing at a directory that no longer exists. Série A arrives
through `ingest_fixtures`, which reads the published manifest. The cups and
Série B still shard from a local directory and have no equivalent yet.

IDEMPOTENCE. `shard()` writes bytes deterministically from the source file's
own row order, and `MatchStore` dedupes on `fixture_id`. Re-running this
entrypoint against an unchanged source therefore reproduces the same shard
bytes and the same store: the ticket's gate ("running it twice in a row is a
no-op on the second run") falls out of those two behaviours rather than
needing new dedupe logic here.

LEAGUE 71 IS NOT IN `LIVE_LEAGUES` - IT IS MIRRORED. `shard_competitions.py`'s
module docstring is explicit: Série A's current season comes from the
`datasets/{season}/fixtures.csv` pipeline, not from these exports. But
`MatchStore` reads only the `competitions/` tree, and the Elo replay reads only
`MatchStore`, so without a `competitions/71/{season}.csv` the ratings never see
the current Série A season at all. Step 1b therefore shards the season file
into `competitions/71/{season}.csv` - the same `shard()` call, the same source
the Série A forecasts already run on. The season file and the committed 71
shards were checked identical in every scored column for 2024 and 2025 (they
differ only in integer-vs-float spelling of blank scores), so the mirror is the
same data by construction, not a second source.
"""

import argparse
import datetime
import os

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.competitions import COMPETITIONS
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.entrypoints.shard_competitions import shard

SERIE_A = 71

# Every id in COMPETITIONS except 71 - see "LEAGUE 71 IS NOT IN LIVE_LEAGUES"
# above. Order matches the LEAGUES=72,73,13,11 the pull was planned against.
LIVE_LEAGUES = tuple(league_id for league_id in COMPETITIONS if league_id != SERIE_A)

# Where the extractor's per-league CSVs are read from. Série A no longer comes
# from here at all - `ingest_fixtures` takes it from S3 - but the cups and
# Série B are still shard-from-a-directory until they get the same treatment.
DEFAULT_SOURCE_DIR = os.environ.get(
    "COMPETITION_EXPORTS", os.path.expanduser("~/Documents/GitHub/data-brasileirao-extractor/app/files")
)
DEFAULT_OUT_ROOT = f"{DATASETS_PATH}/competitions"


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


def serie_a_mirror_plan(season: int, datasets_root: str, out_root: str) -> tuple:
    """`(source_path, dest_path)` for step 2b: the season file Série A
    forecasts already run on, and the 71 shard `MatchStore` reads."""
    return (
        os.path.join(datasets_root, str(season), "fixtures.csv"),
        os.path.join(out_root, str(SERIE_A), f"{season}.csv"),
    )


def mirror_serie_a(season: int, datasets_root: str, out_root: str) -> int:
    """Step 2b. Shard `datasets/{season}/fixtures.csv` into
    `competitions/71/{season}.csv` with the same `shard()` the live leagues
    use, so the current Série A season reaches `MatchStore` (and through it
    the Elo replay) without an API call. Returns rows written."""
    source_path, _ = serie_a_mirror_plan(season, datasets_root, out_root)
    counts = shard(source_path, (SERIE_A,), season + 1, out_root)
    return sum(counts[str(SERIE_A)].values())


def refresh(
    season: int,
    source_dir: str = DEFAULT_SOURCE_DIR,
    out_root: str = DEFAULT_OUT_ROOT,
    leagues: tuple = LIVE_LEAGUES,
    dry_run: bool = False,
    datasets_root: str = DATASETS_PATH,
):
    """Run the refresh described in the module docstring.

    Returns `MatchStore(root=out_root).coverage(season)`, or `None` under
    `--dry-run` (nothing was loaded, because nothing was written).
    """
    mirror_source, mirror_dest = serie_a_mirror_plan(season, datasets_root, out_root)
    if dry_run:
        for league_id, source_path, dest_path in shard_plan(season, leagues, source_dir, out_root):
            print(f"[dry-run] shard: {source_path} -> {dest_path}")
        print(f"[dry-run] mirror Série A: {mirror_source} -> {mirror_dest}")
        return None

    row_counts = shard_all(season, leagues, source_dir, out_root)
    for league_id, _, dest_path in shard_plan(season, leagues, source_dir, out_root):
        print(f"league {league_id}: {row_counts[league_id]} row(s) -> {dest_path}")

    mirrored = mirror_serie_a(season, datasets_root, out_root)
    print(f"league {SERIE_A} (mirrored): {mirrored} row(s) -> {mirror_dest}")

    store = MatchStore(root=out_root)
    coverage = store.coverage(season)
    print(f"coverage[{season}]: {coverage}")
    return coverage


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=datetime.date.today().year)
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR,
                        help="the extractor's app/files, or $COMPETITION_EXPORTS")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the per-league shard plan; write nothing.")
    args = parser.parse_args(argv)

    refresh(season=args.season, source_dir=args.source_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

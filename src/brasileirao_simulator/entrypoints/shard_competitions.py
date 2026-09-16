"""Shard a multi-league API-Football export into one file per league per season.

    files/datasets/competitions/{league_id}/{season}.csv

Why this layout: MatchStore loads every league it knows about from one place,
and a new round of any competition becomes a file drop under its league id.
Sharding by league rather than by season keeps a competition's history
contiguous, which is what Elo's chronological replay reads.

The rows are copied verbatim - same 41 columns as the per-season fixtures.csv -
so nothing about a match is decided here. Which statuses count, which rounds
are admitted, and which score to read are MatchStore's decisions, made once,
in code, where they can be tested.

Seasons at or beyond --max-season are never written from this source. The
current season comes from the pipeline (datasets/{season}/fixtures.csv for
league 71, and the live pull for the rest), not from a static export.

Stdlib only, deliberately: the source export lives outside the repo, on the
host, so this has to run without the Docker image's pandas.
"""

import argparse
import collections
import csv
import os

from brasileirao_simulator.config.settings import DATASETS_PATH

DEFAULT_LEAGUES = (71, 72, 73, 13, 11)
DEFAULT_MAX_SEASON = 2026  # exclusive: 2026 rows are never taken from the export


def shard(source: str, leagues: tuple, max_season: int, out_root: str) -> dict:
    """Write the shards and return per-league, per-season row counts."""
    wanted = {str(league) for league in leagues}
    counts = collections.defaultdict(collections.Counter)
    seen_ids = {}
    writers, handles = {}, {}

    with open(source, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames

        for row in reader:
            league = row["league_id"]
            season = int(row["league_season"])
            if league not in wanted or season >= max_season:
                continue

            fixture_id = row["fixture_id"]
            if fixture_id in seen_ids:
                raise ValueError(
                    f"fixture_id {fixture_id} appears twice "
                    f"(leagues {seen_ids[fixture_id]} and {league})"
                )
            seen_ids[fixture_id] = league

            key = (league, season)
            if key not in writers:
                directory = f"{out_root}/{league}"
                os.makedirs(directory, exist_ok=True)
                handle = open(f"{directory}/{season}.csv", "w", newline="", encoding="utf-8")
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                handles[key], writers[key] = handle, writer

            writers[key].writerow(row)
            counts[league][season] += 1

    for handle in handles.values():
        handle.close()
    return counts


def verify(counts: dict, expected_totals: dict, max_season: int) -> None:
    """The ticket's gate, as assertions rather than a checklist."""
    for league, total in expected_totals.items():
        actual = sum(counts[str(league)].values())
        assert actual == total, f"league {league}: expected {total} rows, wrote {actual}"
    for league, by_season in counts.items():
        assert max(by_season) < max_season, f"league {league} has a season >= {max_season}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="multi-league API-Football export")
    parser.add_argument("--leagues", default=",".join(map(str, DEFAULT_LEAGUES)))
    parser.add_argument("--max-season", type=int, default=DEFAULT_MAX_SEASON)
    parser.add_argument("--out", default=f"{DATASETS_PATH}/competitions")
    parser.add_argument(
        "--expect",
        default="72=4940,73=1215,13=1085,11=950",
        help="league=rows pairs the run must reproduce; the profile the shards were planned against",
    )
    args = parser.parse_args()

    leagues = tuple(int(s) for s in args.leagues.split(","))
    expected = {int(k): int(v) for k, v in (pair.split("=") for pair in args.expect.split(",") if pair)}

    counts = shard(args.source, leagues, args.max_season, args.out)
    verify(counts, expected, args.max_season)

    print(f"{'league':>7}{'seasons':>10}{'rows':>8}   per season")
    for league in sorted(counts, key=int):
        by_season = counts[league]
        span = f"{min(by_season)}-{max(by_season)}"
        print(f"{league:>7}{span:>10}{sum(by_season.values()):>8}   {dict(sorted(by_season.items()))}")
    print(f"\n{sum(sum(c.values()) for c in counts.values()):,} rows, all fixture_ids unique, "
          f"no season >= {args.max_season}. Gate passed.")

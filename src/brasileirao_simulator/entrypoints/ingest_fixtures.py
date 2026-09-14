"""Bring the extractor's published fixtures into the simulator, if there are any.

    python3 -m brasileirao_simulator.entrypoints.ingest_fixtures            # do it
    python3 -m brasileirao_simulator.entrypoints.ingest_fixtures --dry-run  # say what it would do

WHY THIS EXISTS. The join between the two repos used to be a file copied by
hand between two checkouts on one laptop. `data-brasileirao-extractor` now runs
on GitHub Actions and publishes to S3 at stable keys, so the copy is obsolete
and the laptop is no longer part of the contract.

THE MANIFEST IS THE CONTRACT. The extractor writes `manifest.json` last, after
every other file has landed, so a new manifest means everything else is in
place. It carries `last_result_date` per league; comparing that against what
the local fixtures already hold is what makes this safe to run on a schedule:
when no football has been played, it does nothing and says so.

STDLIB ONLY, AND HOST-RUNNABLE. The S3 objects are private, the container has
no boto3, and the AWS CLI lives on the host - so the download shells out to
`aws s3 cp` and the sharding calls `shard_competitions.shard`, which is
deliberately stdlib-only. Nothing here imports pandas, so this runs outside
Docker.
"""

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from brasileirao_simulator.entrypoints.shard_competitions import shard

# `settings.DATASETS_PATH` is relative to /src, where everything else runs.
# This entrypoint runs on the host, so it resolves the same directory from its
# own location instead - otherwise it finds no fixtures, concludes the season
# is a fresh install, and happily overwrites a season that was already there.
DATASETS_PATH = str(Path(__file__).resolve().parents[2] / "files" / "datasets")

SERIE_A = 71
BUCKET = "vmj-lake"
PREFIX = "app/football"
# Kickoffs are stored UTC; a date in this project always means the Brazilian
# one. See domain/season_dates.py, which owns this shift for everything else.
BRAZIL_UTC_OFFSET_HOURS = 3


class NothingToDo(Exception):
    """Not an error: the published data matches what is already here."""


def local_last_result_date(fixtures_path: str) -> str | None:
    """The last Brazilian date with a result in the fixtures we already have,
    or None when the file is absent or nothing has been played."""
    if not os.path.isfile(fixtures_path):
        return None
    dates = []
    with open(fixtures_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("goals_home") not in (None, "", "NULL"):
                kick = dt.datetime.fromisoformat(row["fixture_date"])
                dates.append((kick - dt.timedelta(hours=BRAZIL_UTC_OFFSET_HOURS)).date())
    return max(dates).isoformat() if dates else None


def league_state(manifest: dict, league: int) -> dict:
    """One league's entry, with the schema version checked.

    An unknown schema is refused rather than guessed at: a forecast built from
    a misread manifest would look perfectly normal.
    """
    version = manifest.get("schema_version")
    if version != 1:
        raise ValueError(f"manifest schema_version {version!r} is not one this understands")
    leagues = manifest.get("leagues", {})
    state = leagues.get(str(league))
    if state is None:
        raise ValueError(f"manifest has no league {league}; it has {sorted(leagues)}")
    return state


def decide(manifest: dict, local_date: str | None, league: int = SERIE_A) -> tuple[str, str]:
    """`(published_date, reason)`, or raise NothingToDo.

    Deliberately refuses to go backwards: if the published data is older than
    what is here, something is wrong upstream and overwriting would quietly
    destroy results already simulated on.
    """
    state = league_state(manifest, league)
    published = state["last_result_date"]
    if local_date is None:
        return published, f"no local fixtures yet; publishing {published}"
    if published == local_date:
        raise NothingToDo(f"no new football: both at {published}")
    if published < local_date:
        raise ValueError(
            f"published data ({published}) is older than local ({local_date}) - "
            "refusing to overwrite; check the extractor"
        )
    return published, f"{local_date} -> {published}"


def s3_text(key: str, profile: str | None) -> str:
    return _aws(["s3", "cp", f"s3://{BUCKET}/{key}", "-"], profile)


def s3_download(key: str, destination: str, profile: str | None) -> None:
    _aws(["s3", "cp", f"s3://{BUCKET}/{key}", destination], profile)


def _aws(args: list, profile: str | None) -> str:
    cmd = ["aws", *args]
    if profile:
        cmd += ["--profile", profile]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"aws {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--league", type=int, default=SERIE_A)
    parser.add_argument("--profile", default=os.environ.get("INGEST_AWS_PROFILE"),
                        help="AWS profile that can read the lake (default: $INGEST_AWS_PROFILE)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--datasets", default=DATASETS_PATH)
    args = parser.parse_args()

    fixtures_path = f"{args.datasets}/{args.season}/fixtures.csv"
    manifest = json.loads(s3_text(f"{PREFIX}/manifest.json", args.profile))
    local = local_last_result_date(fixtures_path)

    try:
        published, reason = decide(manifest, local, args.league)
    except NothingToDo as quiet:
        print(f"nothing to do - {quiet}")
        return 0

    state = league_state(manifest, args.league)
    print(f"new results: {reason}")
    print(f"  manifest run {manifest.get('run_id')}, {state['played']} of {state['rows']} played")

    if args.dry_run:
        print("dry run - nothing written")
        return 0

    key = f"{PREFIX}/fixtures_{args.season}_{args.league}.csv"
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        staged = tmp.name
    s3_download(key, staged, args.profile)

    # Check what arrived before letting it replace anything: a truncated or
    # half-written object must not become the season.
    arrived = local_last_result_date(staged)
    if arrived != published:
        os.unlink(staged)
        raise SystemExit(f"downloaded file says {arrived}, manifest said {published} - refusing")

    os.makedirs(os.path.dirname(fixtures_path), exist_ok=True)
    os.replace(staged, fixtures_path)
    print(f"  wrote {fixtures_path}")

    rows = shard(fixtures_path, (args.league,), args.season + 1, f"{args.datasets}/competitions")
    written = sum(rows[str(args.league)].values())
    print(f"  sharded {written} rows into competitions/{args.league}/{args.season}.csv")
    print(f"ready to simulate up to {published}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

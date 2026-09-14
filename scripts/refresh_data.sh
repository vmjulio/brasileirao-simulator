#!/usr/bin/env bash
# Bring the forecasts up to date: ingest what the extractor published, simulate
# the dates that are short, export the dataset the site is built from.
#
#   scripts/refresh_data.sh              # ingest, simulate, export
#   DRY_RUN=1 scripts/refresh_data.sh    # say what would change, touch nothing
#
# The first of three steps; the others are scripts/build_site.sh (the HTML) and
# scripts/deploy_s3.sh (build + publish). Fetching is not here: the extractor
# runs on GitHub Actions and publishes to s3://vmj-lake/app/football/.
#
# Running this without DRY_RUN simulates. The standing rule is to ask before
# that, so an agent runs it with DRY_RUN=1 until told to go - see docs/DEPLOY.md.
#
# Every step is safe to repeat, which is why none of them is skipped:
#   - ingest does nothing when the lake has no newer result than local;
#   - the top-up runs each date's shortfall to the target, so dates already
#     simulated cost nothing - and a run that died mid-way resumes here;
#   - the export is a full rewrite of one JSON file.
#
# The lake profile comes from the environment or from .env.pipeline beside this
# repo (gitignored). It is kept apart from .env.site on purpose: the deploy key
# can reach only the website, and reading the lake must not widen it.
#
#   INGEST_AWS_PROFILE=<a profile that can read vmj-lake>
set -euo pipefail

cd "$(dirname "$0")/.."
# set -a: ingest runs as a child process and sees only exported variables.
if [[ -f .env.pipeline ]]; then set -a; source .env.pipeline; set +a; fi

SEASON=2026
TARGET=20000
MODEL=elo
# Every season the page's archive summary reads, not only the current one.
EXPORT_SEASONS=2016,2017,2018,2019,2020,2021,2022,2023,2024,2025,2026

if [[ -z "${INGEST_AWS_PROFILE:-}" ]]; then
  echo "INGEST_AWS_PROFILE is not set - ingest will use the default AWS profile"
fi

DRY_ARG=""
if [[ -n "${DRY_RUN:-}" ]]; then
  DRY_ARG="--dry-run"
  echo "DRY RUN - nothing will be written"
fi

echo
echo "== 1/3 ingest the published fixtures"
# On the host, not in the container: the lake is private and the AWS CLI is here.
PYTHONPATH=src python3 -m brasileirao_simulator.entrypoints.ingest_fixtures \
  --season "$SEASON" $DRY_ARG

echo
echo "== 2/3 top up every played date to ${TARGET} iterations"
# A dry run here reads the fixtures still on disk, so after a dry ingest it
# does not yet count the dates the new results would add.
docker-compose run --rm --entrypoint "" app \
  python brasileirao_simulator/entrypoints/topup_iterations.py \
  --seasons "$SEASON" --model "$MODEL" --target "$TARGET" $DRY_ARG

if [[ -n "$DRY_ARG" ]]; then
  echo
  echo "== 3/3 export - skipped in a dry run"
  exit 0
fi

echo
echo "== 3/3 export the forecast dataset"
docker-compose run --rm --entrypoint "" app \
  python brasileirao_simulator/entrypoints/export_forecast_dataset.py \
  --seasons "$EXPORT_SEASONS" --model "$MODEL"

echo
echo "data refreshed - scripts/build_site.sh for a local look, scripts/deploy_s3.sh to publish"

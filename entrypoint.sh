#!/bin/bash
set -euo pipefail

SEASON="${SEASON:-2026}"
SIMULATOR="${SIMULATOR:-loop}"

python3 brasileirao_simulator/entrypoints/backfill.py --season "$SEASON" --simulator "$SIMULATOR";
python3 brasileirao_simulator/entrypoints/results_history_dataset.py --season "$SEASON";
python3 brasileirao_simulator/entrypoints/matches_history_dataset.py --season "$SEASON";

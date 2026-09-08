"""Import API-Football season exports into the per-season dataset folders.

Copies `processed_fixtures_{season}_71.csv` from the lean-pype pipeline into
`files/datasets/{season}/fixtures.csv`, generates that season's `dates.json`,
and canonicalises team names.

Why names need canonicalising: team_params_same_venue_average.sql groups by
team_name, but a club's *name* can change between seasons while its id stays
put - Bragantino became RB Bragantino in 2021, and Chapecoense is spelled
"Chapecoense-SC" in 2019 and "Chapecoense-sc" everywhere else. The lookback
window reaches across the season boundary, so an unfixed rename makes a club
that played a full previous season look newly promoted: its window comes up
short, it gets shrunk toward the newcomer prior, and nothing errors.

Team ids are stable across every season, so one name per id - the most recent -
is applied to every row. The deeper fix would be to group by team_id in the SQL;
this keeps the reference queries untouched.
"""

import argparse
import csv
import json
import os
import shutil

import pandas as pd

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.season_dates import dates_from_fixtures


NAME_COLUMNS = ("teams_home_name", "teams_away_name")
ID_COLUMNS = ("teams_home_id", "teams_away_id")


def canonical_names(frames: dict) -> dict:
    """One name per team id, taken from the most recent season it appears in."""
    canonical = {}
    for season in sorted(frames):
        for id_col, name_col in zip(ID_COLUMNS, NAME_COLUMNS):
            for team_id, name in zip(frames[season][id_col], frames[season][name_col]):
                canonical[str(team_id)] = name
    return canonical


def apply_canonical_names(frame: pd.DataFrame, canonical: dict) -> int:
    """Rewrite names in place, returning how many cells changed."""
    changed = 0
    for id_col, name_col in zip(ID_COLUMNS, NAME_COLUMNS):
        wanted = frame[id_col].astype(str).map(canonical)
        changed += int((wanted != frame[name_col]).sum())
        frame[name_col] = wanted
    return changed


def import_seasons(source_dir: str, seasons: list, datasets_path: str = DATASETS_PATH) -> dict:
    frames = {
        season: pd.read_csv(f"{source_dir}/processed_fixtures_{season}_71.csv")
        for season in seasons
    }
    canonical = canonical_names(frames)

    report = {}
    for season, frame in frames.items():
        renamed = apply_canonical_names(frame, canonical)
        season_dir = f"{datasets_path}/{season}"
        os.makedirs(season_dir, exist_ok=True)
        frame.to_csv(f"{season_dir}/fixtures.csv", index=False)

        dates = dates_from_fixtures(frame)
        with open(f"{season_dir}/dates.json", "w") as f:
            json.dump({"dates": dates}, f, indent=2)

        played = int(frame["goals_home"].notnull().sum())
        report[season] = {
            "matches": len(frame),
            "played": played,
            "unplayed": len(frame) - played,
            "renamed_cells": renamed,
            "dates": len(dates),
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="directory holding processed_fixtures_{season}_71.csv")
    parser.add_argument("--seasons", required=True, help="comma-separated, e.g. 2015,2016,2017")
    args = parser.parse_args()

    seasons = [int(s) for s in args.seasons.split(",")]
    report = import_seasons(args.source, seasons)

    print(f"{'season':>7}{'matches':>9}{'played':>8}{'unplayed':>10}{'renamed':>9}{'dates':>7}")
    for season in sorted(report):
        r = report[season]
        print(f"{season:>7}{r['matches']:>9}{r['played']:>8}{r['unplayed']:>10}{r['renamed_cells']:>9}{r['dates']:>7}")

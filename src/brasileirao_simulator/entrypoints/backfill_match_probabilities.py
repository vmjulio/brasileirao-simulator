"""Persist the analytic match-outcome probabilities for every as-of date of a season.

Why this exists: the ten-season sweeps computed match probabilities on the fly
and kept only the aggregate Brier numbers, so the forecasts themselves survive
nowhere for 2016-2023. The Monte Carlo pickles hold `match_results`, but they
exist only for 2024 (partial), 2025 and 2026.

This writes one row per (as-of date, remaining fixture) using the same closed-form
Poisson outcome probabilities the sweeps score against - no simulation, so a whole
season costs seconds rather than the pickle route's minutes. Title, relegation and
position probabilities are NOT produced here: those need the ranked season tables
only Monte Carlo gives.

The probabilities are the model's exact values at the sweep's defaults, so a
downstream re-score needs no recomputation.
"""

import argparse
import os
import time

import pandas as pd

from brasileirao_simulator.config.settings import EXPORTS_PATH
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.variant_sweep import analytic_forecasts_for_date


def season_match_probabilities(season: int) -> pd.DataFrame:
    """One row per (as_of_date, remaining fixture) for every date in the season."""
    season_data = SeasonData(season)
    tables = Tables(season_data)

    rows = []
    for as_of_date in season_data.dates:
        forecasts = analytic_forecasts_for_date(season, as_of_date, tables=tables)
        for match_key, (p_home, p_draw, p_away) in forecasts.items():
            home, away = match_key.split(" x ", 1)
            rows.append(
                {
                    "season": season,
                    "as_of_date": as_of_date,
                    "match_key": match_key,
                    "home_team": home,
                    "away_team": away,
                    "p_home": p_home,
                    "p_draw": p_draw,
                    "p_away": p_away,
                }
            )

    return pd.DataFrame(
        rows,
        columns=[
            "season",
            "as_of_date",
            "match_key",
            "home_team",
            "away_team",
            "p_home",
            "p_draw",
            "p_away",
        ],
    )


def backfill(seasons: list, exports_path: str = EXPORTS_PATH) -> dict:
    report = {}
    for season in seasons:
        started = time.perf_counter()
        frame = season_match_probabilities(season)
        elapsed = time.perf_counter() - started

        season_dir = f"{exports_path}/{season}"
        os.makedirs(season_dir, exist_ok=True)
        # Gzipped: these are ~20k highly repetitive rows per season, which
        # compress about six-fold, and they are committed.
        path = f"{season_dir}/match_probabilities.csv.gz"
        frame.to_csv(path, index=False, compression="gzip")

        report[season] = {
            "dates": frame["as_of_date"].nunique(),
            "rows": len(frame),
            "seconds": elapsed,
            "megabytes": os.path.getsize(path) / 1e6,
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", required=True, help="comma-separated, e.g. 2016,2017")
    args = parser.parse_args()

    seasons = [int(s) for s in args.seasons.split(",")]
    report = backfill(seasons)

    print(f"{'season':>7}{'dates':>7}{'rows':>9}{'seconds':>9}{'MB':>7}")
    for season in sorted(report):
        r = report[season]
        print(f"{season:>7}{r['dates']:>7}{r['rows']:>9}{r['seconds']:>9.1f}{r['megabytes']:>7.1f}")
    total = sum(r["seconds"] for r in report.values())
    print(f"total: {total:.1f}s across {len(report)} seasons")

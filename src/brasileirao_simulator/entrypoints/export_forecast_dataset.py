"""Flatten the Monte Carlo pickles into one compact JSON for the web report.

Each season contributes its as-of dates, and per team the title and relegation
probability at each of those dates, plus that team's actual final position. The
calibration block bins every (date, team) relegation forecast against what
actually happened.

Why distinct clubs are counted per bin and not only forecasts: a club sitting in
the same probability band for two months contributes sixty forecasts and one
outcome. Reading the forecast count as a sample size is what made an earlier
"relegation overconfidence" finding look real when it was a handful of clubs -
so the bin carries both numbers and the report shows both.
"""

import argparse
import json
import os
import pickle

import pandas as pd

from brasileirao_simulator.config.settings import DATASETS_PATH, EXPORTS_PATH, RESULTS_DIRECTORY

RELEGATION_PLACES = 4
BINS = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def final_table(season: int) -> pd.DataFrame:
    """Final standings from played matches: points, wins, goal difference, goals for."""
    fixtures = pd.read_csv(f"{DATASETS_PATH}/{season}/fixtures.csv")
    played = fixtures[fixtures["goals_home"].notnull()]

    rows = []
    for side, own, against in (("home", "goals_home", "goals_away"), ("away", "goals_away", "goals_home")):
        frame = played.rename(columns={f"teams_{side}_name": "team"})
        rows.append(
            pd.DataFrame(
                {
                    "team": frame["team"],
                    "gf": played[own].values,
                    "ga": played[against].values,
                    "points": (played[own].values > played[against].values) * 3
                    + (played[own].values == played[against].values) * 1,
                    "wins": (played[own].values > played[against].values).astype(int),
                }
            )
        )

    table = pd.concat(rows).groupby("team", as_index=False).sum()
    table["gd"] = table["gf"] - table["ga"]
    table = table.sort_values(["points", "wins", "gd", "gf"], ascending=False).reset_index(drop=True)
    table["position"] = table.index + 1
    return table


def season_series(season: int, results_directory: str = RESULTS_DIRECTORY) -> dict:
    """Per-date title and relegation probabilities for every team in one season."""
    season_dir = f"{results_directory}/{season}"
    if not os.path.isdir(season_dir):
        return {}

    files = sorted(f for f in os.listdir(season_dir) if f.startswith("average_results_"))
    dates = [f.replace("average_results_", "").replace(".pkl", "") for f in files]

    table = final_table(season)
    positions = dict(zip(table["team"], table["position"]))

    teams = {team: {"title": [], "releg": []} for team in positions}
    iterations = None

    for file_name in files:
        with open(f"{season_dir}/{file_name}", "rb") as f:
            payload = pickle.load(f)
        title = payload["brasileirao_title"]
        releg = payload["brasileirao_relegation"]
        total = sum(title.values()) or 1
        iterations = total

        for team in teams:
            teams[team]["title"].append(round(100 * title.get(team, 0) / total, 1))
            teams[team]["releg"].append(round(100 * releg.get(team, 0) / total, 1))

    return {
        "dates": dates,
        "iterations": iterations,
        "teams": {
            team: {
                "title": series["title"],
                "releg": series["releg"],
                "position": int(positions[team]),
            }
            for team, series in teams.items()
        },
    }


def calibration(seasons: dict) -> list:
    """Bin every relegation forecast against whether the club was actually relegated.

    Each bin reports forecasts, distinct clubs, and the observed relegation rate
    computed per club rather than per forecast - one club, one outcome, however
    many days it spent in the band.
    """
    buckets = {i: {"forecasts": 0, "clubs": {}} for i in range(len(BINS) - 1)}

    for season, payload in seasons.items():
        if not payload:
            continue
        for team, series in payload["teams"].items():
            relegated = series["position"] > (len(payload["teams"]) - RELEGATION_PLACES)
            for probability in series["releg"]:
                index = min(
                    next(i for i in range(len(BINS) - 1) if probability < BINS[i + 1] or i == len(BINS) - 2),
                    len(BINS) - 2,
                )
                buckets[index]["forecasts"] += 1
                buckets[index]["clubs"][f"{season}:{team}"] = relegated

    rows = []
    for index, bucket in buckets.items():
        clubs = bucket["clubs"]
        if not clubs:
            continue
        rows.append(
            {
                "low": BINS[index],
                "high": BINS[index + 1],
                "midpoint": (BINS[index] + BINS[index + 1]) / 2,
                "forecasts": bucket["forecasts"],
                "clubs": len(clubs),
                "relegated": sum(clubs.values()),
                "observed": round(100 * sum(clubs.values()) / len(clubs), 1),
            }
        )
    return rows


def build(season_list: list) -> dict:
    seasons = {str(season): season_series(season) for season in season_list}
    seasons = {season: payload for season, payload in seasons.items() if payload}
    return {
        "seasons": seasons,
        "calibration": calibration(seasons),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", required=True, help="comma-separated")
    parser.add_argument("--out", default=f"{EXPORTS_PATH}/forecast_dataset.json")
    args = parser.parse_args()

    dataset = build([int(s) for s in args.seasons.split(",")])
    with open(args.out, "w") as f:
        json.dump(dataset, f, separators=(",", ":"))

    print(f"{'season':>7}{'dates':>7}{'teams':>7}{'iters':>8}")
    for season, payload in sorted(dataset["seasons"].items()):
        print(f"{season:>7}{len(payload['dates']):>7}{len(payload['teams']):>7}{payload['iterations']:>8}")
    print(f"\nwrote {args.out} ({os.path.getsize(args.out) / 1e6:.2f} MB)")

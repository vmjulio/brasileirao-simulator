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
from brasileirao_simulator.domain.season_data import SeasonData

RELEGATION_PLACES = 4
PENDING_STATUSES = ("NS", "PST")
BINS = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def season_is_complete(season: int) -> bool:
    """True once no fixture is still waiting to be played.

    Standings for a season still under way are the table *so far*, not where
    anyone finished, so the report must not crown a leader or outline a club's
    "actual" place on the heatmap until this is true.

    The test is the fixture's status, not whether it has a score: Chapecoense's
    last match of 2016 was cancelled after the LaMia crash and has no result,
    yet that season's table is as settled as any other. Only NS and PST mean a
    match is still owed.
    """
    fixtures = pd.read_csv(f"{DATASETS_PATH}/{season}/fixtures.csv")
    return not fixtures["fixture_status_short"].isin(PENDING_STATUSES).any()


def final_table(season: int) -> pd.DataFrame:
    """Standings from played matches: points, wins, goal difference, goals for."""
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

    # Drive off the season's own date list rather than whatever is in the
    # directory. Earlier runs left pickles for dates that are not matchdays at
    # all - 2024 carries one for 2024-12-02, a day with zero fixtures - and
    # reading the directory pulls those orphans into the archive as though they
    # were forecasts.
    season_dates = SeasonData(season).dates
    dates, files = [], []
    for date in season_dates:
        file_name = f"average_results_{date}.pkl"
        if os.path.isfile(f"{season_dir}/{file_name}"):
            dates.append(date)
            files.append(file_name)

    table = final_table(season)
    positions = dict(zip(table["team"], table["position"]))

    teams = {team: {"title": [], "releg": [], "pos": []} for team in positions}
    places = len(positions)

    # Per date, not per season: 2024 and 2025 were run incrementally during the
    # season at whatever iteration count was passed at the time, so a single
    # figure hides dates thin enough for their probabilities to be quantised
    # (2025 has dates at 10 iterations - every probability a multiple of 10%).
    iterations = []

    # One relegation-risk curve per as-of date, never pooled across dates or
    # seasons. "Does 43 points keep you up?" has no league-wide answer: the cut
    # fell on 36 in 2019 and on 43 in 2017, so pooling averages over league
    # shapes that differ four-fold. It is only meaningful conditioned on one
    # season's remaining fixtures and one date's standings, which is exactly
    # what the simulation behind each pickle already conditions on.
    points_risk = []

    for file_name in files:
        with open(f"{season_dir}/{file_name}", "rb") as f:
            payload = pickle.load(f)

        by_points = payload.get("brasileirao_relegation_points", {})
        totals = {}
        for points, by_place in by_points.items():
            bucket = totals.setdefault(int(points), [0, 0])
            for place, count in by_place.items():
                bucket[0] += count
                if int(place) > places - RELEGATION_PLACES:
                    bucket[1] += count

        if totals:
            low, high = min(totals), max(totals)
            counts = []
            for points in range(low, high + 1):
                total, relegated = totals.get(points, [0, 0])
                counts += [total, relegated]
            points_risk.append({"low": low, "counts": counts})
        else:
            # 2024's pickles predate brasileirao_relegation_points entirely.
            points_risk.append(None)
        title = payload["brasileirao_title"]
        releg = payload["brasileirao_relegation"]
        total = sum(title.values()) or 1
        iterations.append(int(total))

        by_position = payload.get("brasileirao_positions", {})

        for team in teams:
            teams[team]["title"].append(round(100 * title.get(team, 0) / total, 1))
            teams[team]["releg"].append(round(100 * releg.get(team, 0) / total, 1))

            # One row of 20 integers per date: the chance of finishing in each
            # place, in tenths of a percent. A fixed-width row of small ints is
            # both smaller and simpler to read back than a sparse map, and most
            # of a team's mass sits in a handful of adjacent places anyway.
            counts = by_position.get(team, {})
            teams[team]["pos"].append(
                [round(1000 * counts.get(place, counts.get(str(place), 0)) / total) for place in range(1, places + 1)]
            )

    return {
        "dates": dates,
        "complete": season_is_complete(season),
        "iterations": iterations,
        "points_risk": points_risk,
        "teams": {
            team: {
                "title": series["title"],
                "releg": series["releg"],
                "pos": series["pos"],
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
        # A season still being played has no relegated clubs yet - scoring its
        # forecasts against the table so far would count mid-season strugglers
        # as though their fate were settled.
        if not payload or not payload["complete"]:
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


def historical_cutoffs(season_list: list) -> list:
    """Where the relegation line actually fell in each completed season.

    Deliberately not a probability. A finished season's outcomes are settled -
    a club on 44 points either went down or did not - so presenting them as
    rates invites exactly the wrong reading. What they legitimately give is
    context: the span between the lowest survivor and the highest relegated
    club, which is the range any live season's cut is likely to land in.
    """
    rows = []
    for season in season_list:
        if not season_is_complete(season):
            continue
        table = final_table(season)
        places = len(table)
        safe = table[table["position"] == places - RELEGATION_PLACES]
        down = table[table["position"] == places - RELEGATION_PLACES + 1]
        if safe.empty or down.empty:
            continue
        rows.append(
            {
                "season": season,
                "lowest_safe": int(safe["points"].iloc[0]),
                "highest_relegated": int(down["points"].iloc[0]),
            }
        )
    return rows


def build(season_list: list) -> dict:
    seasons = {str(season): season_series(season) for season in season_list}
    seasons = {season: payload for season, payload in seasons.items() if payload}

    return {
        "seasons": seasons,
        "calibration": calibration(seasons),
        "historical_cutoffs": historical_cutoffs(season_list),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", required=True, help="comma-separated")
    parser.add_argument("--out", default=f"{EXPORTS_PATH}/forecast_dataset.json")
    args = parser.parse_args()

    dataset = build([int(s) for s in args.seasons.split(",")])
    with open(args.out, "w") as f:
        json.dump(dataset, f, separators=(",", ":"))

    print(f"{'season':>7}{'dates':>7}{'teams':>7}{'min':>8}{'median':>8}{'max':>8}{'thin':>6}")
    for season, payload in sorted(dataset["seasons"].items()):
        iterations = sorted(payload["iterations"])
        middle = iterations[len(iterations) // 2]
        thin = sum(1 for i in iterations if i < 1000)
        print(
            f"{season:>7}{len(payload['dates']):>7}{len(payload['teams']):>7}"
            f"{iterations[0]:>8}{middle:>8}{iterations[-1]:>8}{thin:>6}"
        )
    print(f"\nwrote {args.out} ({os.path.getsize(args.out) / 1e6:.2f} MB)")

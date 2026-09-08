"""One as-of date, expressed as arrays a batch simulation can consume.

Team parameters are computed from the fixtures as they stand before any
simulation and are never re-read afterwards, so every remaining fixture's
lambda pair is a constant. Precomputing them here is what lets the Monte Carlo
draw a whole batch of seasons without touching a DataFrame.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


ADJUSTMENT_WEIGHT = 0.5

# Used when a team has no rows at a venue, mirroring the .empty guard in
# PoissonSameVenueAverageAdapter._calculate_adjusted_averages.
MISSING_TEAM_AVERAGE = 1.0


@dataclass(frozen=True)
class SeasonBaseline:
    """The fixed inputs of one as-of date.

    Arrays indexed by team follow `teams`; arrays indexed by fixture follow the
    remaining games in date order, which is the order the existing adapter
    simulates them in.
    """

    teams: list[str]
    points: np.ndarray
    wins: np.ndarray
    goals_for: np.ndarray
    goals_against: np.ndarray
    home_team: np.ndarray
    away_team: np.ndarray
    lam_home: np.ndarray
    lam_away: np.ndarray
    fixture_id: np.ndarray
    round_: np.ndarray


def build_baseline(
    fixtures: pd.DataFrame,
    remaining_games: pd.DataFrame,
    team_params: pd.DataFrame,
    season: int,
) -> SeasonBaseline:
    season_rows = fixtures[fixtures["season"] == season]
    teams = sorted(season_rows["team_name"].unique())
    position_of = {team: position for position, team in enumerate(teams)}

    points, wins, goals_for, goals_against = _played_table(season_rows, teams, position_of)
    averages = _averages_by_team_and_venue(team_params)

    games = remaining_games[remaining_games["season"] == season].sort_values(
        by=["fixture_date"]
    )
    home_team, away_team, lam_home, lam_away = _fixture_arrays(games, position_of, averages)

    return SeasonBaseline(
        teams=teams,
        points=points,
        wins=wins,
        goals_for=goals_for,
        goals_against=goals_against,
        home_team=home_team,
        away_team=away_team,
        lam_home=lam_home,
        lam_away=lam_away,
        fixture_id=games["fixture_id"].to_numpy(),
        round_=games["round_"].to_numpy(),
    )


def _played_table(season_rows, teams, position_of):
    played = season_rows[season_rows["goals_for"].notnull()]
    points = np.zeros(len(teams))
    wins = np.zeros(len(teams))
    goals_for = np.zeros(len(teams))
    goals_against = np.zeros(len(teams))

    for row in played.itertuples():
        position = position_of[row.team_name]
        goals_for[position] += row.goals_for
        goals_against[position] += row.goals_against
        if row.goals_for > row.goals_against:
            points[position] += 3
            wins[position] += 1
        elif row.goals_for == row.goals_against:
            points[position] += 1

    return points, wins, goals_for, goals_against


def _averages_by_team_and_venue(team_params):
    return {
        (row.team_name, row.venue): (row.goals_for_average, row.goals_against_average)
        for row in team_params.itertuples()
    }


def _fixture_arrays(games, position_of, averages):
    fallback = (MISSING_TEAM_AVERAGE, MISSING_TEAM_AVERAGE)
    home_team, away_team, lam_home, lam_away = [], [], [], []

    for row in games.itertuples():
        home_for, home_against = averages.get((row.team_name, "home"), fallback)
        away_for, away_against = averages.get((row.opponent_name, "away"), fallback)

        home_team.append(position_of[row.team_name])
        away_team.append(position_of[row.opponent_name])
        lam_home.append(ADJUSTMENT_WEIGHT * home_for + ADJUSTMENT_WEIGHT * away_against)
        lam_away.append(ADJUSTMENT_WEIGHT * away_for + ADJUSTMENT_WEIGHT * home_against)

    return (
        np.array(home_team),
        np.array(away_team),
        np.array(lam_home),
        np.array(lam_away),
    )

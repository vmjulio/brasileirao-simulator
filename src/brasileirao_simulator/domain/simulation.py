from collections import defaultdict
from brasileirao_simulator.queries.queries import TEAM_PARAMS_WEIGHTED, TEAM_PARAMS, BOLAO_STANDINGS, STANDINGS
import numpy as np
import pandas as pd
import duckdb


ADJUSTMENT_WEIGHT = 0.5


class Simulation:
    def __init__(self, remaining_games, fixtures, strategy):
        self.remaining_games: pd.DataFrame = remaining_games
        self.fixtures: pd.DataFrame = fixtures
        self.strategy: str = strategy
        self.results = defaultdict(int)
        self.con = duckdb.connect()

    def run_simulation(self):
        if self.strategy == "average":
            return self._simulate_average()
        elif self.strategy == "index":
            return self._simulate_index()
        else:
            raise ValueError("Unknown strategy")

    def _simulate_average(self):
        new_fixtures = self.fixtures.copy()
        team_params = self._get_team_params(new_fixtures)

        for game in self.remaining_games.to_dict(orient="records"):
            home_avg, away_avg = self._calculate_adjusted_averages(game, team_params)
            home_goals, away_goals = self._calculate_goals(home_avg, away_avg)
            self._update_fixtures(new_fixtures, game, home_goals, away_goals)

        return new_fixtures

    def _get_team_params(self, new_fixtures):
        return self.con.sql(TEAM_PARAMS_WEIGHTED).df()

    def _simulate_index(self):
        # Implement simplified simulation logic based on "index" strategy
        return self.fixtures.copy()

    def _calculate_adjusted_averages(self, game, team_params):
        home_criteria = self._get_team_criteria(team_params, game["team_name"], "home")
        away_criteria = self._get_team_criteria(team_params, game["opponent_name"], "away")

        home_goals_for_avg = team_params[home_criteria]["goals_for_average"].iloc[0]
        home_goals_against_avg = team_params[home_criteria]["goals_against_average"].iloc[0]
        away_goals_for_avg = team_params[away_criteria]["goals_for_average"].iloc[0]
        away_goals_against_avg = team_params[away_criteria]["goals_against_average"].iloc[0]

        home_avg = (ADJUSTMENT_WEIGHT * home_goals_for_avg + ADJUSTMENT_WEIGHT * away_goals_against_avg)
        away_avg = (ADJUSTMENT_WEIGHT * away_goals_for_avg + ADJUSTMENT_WEIGHT * home_goals_against_avg)

        return home_avg, away_avg

    def _get_team_criteria(self, team_params, team_name, venue):
        return (team_params["team_name"] == team_name) & (team_params["venue"] == venue)

    def _calculate_goals(self, home_avg, away_avg):
        home_goals = np.random.poisson(home_avg, 1)[0]
        away_goals = np.random.poisson(away_avg, 1)[0]
        return home_goals, away_goals

    def _update_fixtures(self, fixtures, game, home_goals, away_goals):
        index_home = self._get_fixture_index(fixtures, game, "team_name")
        index_away = self._get_fixture_index(fixtures, game, "opponent_name")

        fixtures.loc[index_home, "goals_for"] = home_goals
        fixtures.loc[index_home, "goals_against"] = away_goals
        fixtures.loc[index_away, "goals_for"] = away_goals
        fixtures.loc[index_away, "goals_against"] = home_goals


    def _get_fixture_index(self, fixtures, game, team_type):
        return (fixtures["fixture_id"] == game["fixture_id"]) & (fixtures["team_name"] == game[team_type])

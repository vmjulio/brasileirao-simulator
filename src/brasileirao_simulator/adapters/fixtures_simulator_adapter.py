from brasileirao_simulator.queries.queries import TEAM_PARAMS_WEIGHTED, TEAM_PARAMS, BOLAO_STANDINGS, STANDINGS
import duckdb
import numpy as np


ADJUSTMENT_WEIGHT = 0.5


class FixtureSimulatorAdapter:
    def __init__(self, strategy):
        self.con = duckdb.connect()
        self.strategy = strategy

    def simulate_fixtures(self, fixtures, remaining_games):
        if self.strategy == "average":
            return self._simulate_average(fixtures, remaining_games)
        elif self.strategy == "index":
            return self._simulate_index(fixtures, remaining_games)
        else:
            raise ValueError(f"Strategy {self.strategy} not supported.")

    def _simulate_average(self, fixtures, remaining_games):
        new_fixtures = fixtures.copy()
        team_params = self._get_team_params(new_fixtures)

        for game in remaining_games.to_dict(orient="records"):
            home_avg, away_avg = self._calculate_adjusted_averages(game, team_params)
            home_goals, away_goals = self._calculate_goals(home_avg, away_avg)
            self._update_fixtures(new_fixtures, game, home_goals, away_goals)

        return new_fixtures

    def _get_team_params(self, new_fixtures):
        return self.con.sql(TEAM_PARAMS_WEIGHTED).df()
    
    def _get_team_criteria(self, team_params, team_name, venue):
        return (team_params["team_name"] == team_name) & (team_params["venue"] == venue)

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

    def get_brasileirao_standings(self, df):
        enriched_tidy_fixtures = df
        return self.con.sql(STANDINGS).df()

    def get_bolao_standings(self, df):
        return self.con.sql(BOLAO_STANDINGS).df()

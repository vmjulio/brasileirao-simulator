from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.datasets import punters, doubles, fixtures
import duckdb
import pandas as pd
import numpy as np


class Tables:
    def __init__(self) -> None:
        self.con = duckdb.connect()

    def enriched_tidy_fixtures(self, blank_from_date: str = None) -> pd.DataFrame:
        tidy_fixtures: pd.DataFrame = self.con.sql(Queries().tidy_fixtures()).df()
        if blank_from_date:
            condition = tidy_fixtures["fixture_date"] > (blank_from_date + " 23:59:59")
            new_values = {
                'goals_for': np.nan,
                'goals_against': np.nan,
                'points': 0.0
            }
            tidy_fixtures.loc[condition, ['goals_for', 'goals_against', 'points']] = tidy_fixtures.loc[condition, ['goals_for', 'goals_against', 'points']].assign(**new_values)
        return self.con.sql(Queries().enriched_tidy_fixtures()).df()

    def remaining_games(self, blank_from_date: str = None) -> pd.DataFrame:
        enriched_tidy_fixtures: pd.DataFrame = self.enriched_tidy_fixtures(blank_from_date)
        home_filter = enriched_tidy_fixtures["venue"] == "home"
        not_played_filter = enriched_tidy_fixtures["goals_for"].isnull()

        return enriched_tidy_fixtures[(not_played_filter) & (home_filter)]

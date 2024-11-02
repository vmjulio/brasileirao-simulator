from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.datasets import punters, doubles, fixtures
import duckdb
import pandas as pd


class Tables:
    def __init__(self) -> None:
        self.con = duckdb.connect()

    def enriched_tidy_fixtures(self) -> pd.DataFrame:
        tidy_fixtures: pd.DataFrame = self.con.sql(Queries().tidy_fixtures()).df()
        return self.con.sql(Queries().enriched_tidy_fixtures()).df()

    def remaining_games(self) -> pd.DataFrame:
        enriched_tidy_fixtures: pd.DataFrame = self.enriched_tidy_fixtures()
        home_filter = enriched_tidy_fixtures["venue"] == "home"
        not_player_filter = enriched_tidy_fixtures["goals_for"].isnull()

        return enriched_tidy_fixtures[(not_player_filter) & (home_filter)]

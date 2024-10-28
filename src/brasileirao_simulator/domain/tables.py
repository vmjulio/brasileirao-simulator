from brasileirao_simulator.queries.queries import TIDY_FIXTURES, ENRICHED_TIDY_FIXTURES
from brasileirao_simulator.domain.datasets import punters, doubles, fixtures
import duckdb


class Tables:
    def __init__(self):
        self.con = duckdb.connect()

    def enriched_tidy_fixtures(self):
        tidy_fixtures = self.con.sql(TIDY_FIXTURES).df()
        return self.con.sql(ENRICHED_TIDY_FIXTURES).df()

    def remaining_games(self):
        enriched_tidy_fixtures = self.enriched_tidy_fixtures()
        home_filter = enriched_tidy_fixtures["venue"]=="home"
        not_player_filter = enriched_tidy_fixtures["goals_for"].isnull()

        return enriched_tidy_fixtures[(not_player_filter) & (home_filter)]

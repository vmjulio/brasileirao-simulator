from brasileirao_simulator.config.settings import QUERIES_PATH


class Queries:
    def read_sql(self, file_name: str) -> str:
        with open(f"{QUERIES_PATH}/{file_name}", "r") as f:
            return f.read()

    def tidy_fixtures(self) -> str:
        return self.read_sql("tidy_fixtures.sql")

    def enriched_tidy_fixtures(self) -> str:
        return self.read_sql("enriched_tidy_fixtures.sql")

    def bolao_standings(self) -> str:
        return self.read_sql("bolao_standings.sql")

    def standings(self) -> str:
        return self.read_sql("standings.sql")
    
    def match_results(self) -> str:
        return self.read_sql("match_results.sql")

    def team_params(self) -> str:
        return self.read_sql("team_params.sql")

    def team_params_same_venue_average(self) -> str:
        return self.read_sql("team_params_same_venue_average.sql")

    def team_params_weighted_venue_average(self) -> str:
        return self.read_sql("team_params_weighted_venue_average.sql")

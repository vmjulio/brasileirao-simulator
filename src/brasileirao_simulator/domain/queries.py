class Queries:
    def read_sql(file_name: str) -> str:
        with open(f"files/queries/{file_name}", "r") as f:
            return f.read()

    def tidy_fixtures(self) -> str:
        return self.read_sql("tidy_fixtures.sql")

    def enriched_tidy_fixtures(self) -> str:
        return self.read_sql("enriched_tidy_fixtures.sql")

    def bolao_standings(self) -> str:
        return self.read_sql("bolao_standings.sql")

    def standings(self) -> str:
        return self.read_sql("standings.sql")

    def team_params(self) -> str:
        return self.read_sql("team_params.sql")

    def team_params_weighted(self) -> str:
        return self.read_sql("team_params_weighted.sql")

    TIDY_FIXTURES = read_sql("tidy_fixtures.sql")
    ENRICHED_TIDY_FIXTURES = read_sql("enriched_tidy_fixtures.sql")
    BOLAO_STANDINGS = read_sql("bolao_standings.sql")
    STANDINGS = read_sql("standings.sql")
    TEAM_PARAMS = read_sql("team_params.sql")
    TEAM_PARAMS_WEIGHTED = read_sql("team_params_weighted.sql")

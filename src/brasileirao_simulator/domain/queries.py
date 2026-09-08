from string import Template

from brasileirao_simulator.config.settings import QUERIES_PATH


class Queries:
    """SQL text with the season bound in.

    Uses string.Template rather than str.format so a literal brace in SQL can
    never be mistaken for a placeholder.
    """

    def __init__(self, season: int) -> None:
        self.season: int = season

    def read_sql(self, file_name: str) -> str:
        with open(f"{QUERIES_PATH}/{file_name}", "r") as f:
            template = Template(f.read())
        return template.safe_substitute(
            season=self.season, previous_season=self.season - 1
        )

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

    def team_match_counts(self) -> str:
        return self.read_sql("team_match_counts.sql")

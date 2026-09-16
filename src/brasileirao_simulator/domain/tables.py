import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData


class Tables:
    """The fixture tables a simulation starts from, for one season."""

    def __init__(self, season_data: SeasonData) -> None:
        self.season_data: SeasonData = season_data
        self.con = duckdb.connect()
        self.season_data.register(self.con)
        self.queries: Queries = Queries(season_data.season)

    def enriched_tidy_fixtures(self, blank_from_date: str = None) -> pd.DataFrame:
        """Fixtures with running totals, form, and rank per round.

        blank_from_date erases results after a date so the simulation can be run
        "as of" that day. The blanked frame is registered before the enriching
        query runs, which is what makes the blanking take effect downstream.
        """
        tidy_fixtures: pd.DataFrame = self.con.sql(self.queries.tidy_fixtures()).df()

        if blank_from_date:
            condition = tidy_fixtures["fixture_date"] > (blank_from_date + " 23:59:59")
            new_values = {"goals_for": np.nan, "goals_against": np.nan, "points": 0.0}
            tidy_fixtures.loc[condition, ["goals_for", "goals_against", "points"]] = (
                tidy_fixtures.loc[
                    condition, ["goals_for", "goals_against", "points"]
                ].assign(**new_values)
            )

        self.con.register("tidy_fixtures", tidy_fixtures)
        return self.con.sql(self.queries.enriched_tidy_fixtures()).df()

    def remaining_games(self, blank_from_date: str = None) -> pd.DataFrame:
        enriched_tidy_fixtures: pd.DataFrame = self.enriched_tidy_fixtures(blank_from_date)
        home_filter = enriched_tidy_fixtures["venue"] == "home"
        not_played_filter = enriched_tidy_fixtures["goals_for"].isnull()
        # The fixtures union carries the previous season too. If that season was
        # itself unfinished, its unplayed games would otherwise be simulated every
        # iteration and then discarded downstream.
        current_season_filter = enriched_tidy_fixtures["season"] == self.season_data.season

        return enriched_tidy_fixtures[(not_played_filter) & (home_filter) & (current_season_filter)]

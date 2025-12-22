from brasileirao_simulator.domain.datasets import punters, doubles, fixtures
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import PoissonSameVenueAverageAdapter
import pandas as pd


def inspect_dataset(dataset_name: str = None) -> None:
    enriched_tidy_fixtures = Tables().enriched_tidy_fixtures()
    remaining_games = Tables().remaining_games
    standings = PoissonSameVenueAverageAdapter().get_brasileirao_standings(enriched_tidy_fixtures)
    team_params = PoissonSameVenueAverageAdapter().get_team_params(enriched_tidy_fixtures)

    cols = ["venue", "home_punter", "round_", "away_punter", "team_name", "opponent_name",
            "fixture_date", "goals_for", "goals_against"]

    pd.options.display.max_columns = 20
    pd.options.display.max_rows = 50
    pd.set_option('display.width',1000)

    enriched_tidy_fixtures.to_csv("files/datasets/out_enriched_tidy_fixtures.csv", index=False)
    standings.to_csv("files/datasets/out_standings.csv", index=False)
    team_params.to_csv("files/datasets/out_team_params.csv", index=False)


if __name__ == "__main__":
    inspect_dataset()

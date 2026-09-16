"""Dump a season's intermediate tables to CSV for eyeballing."""

import argparse
import os

import pandas as pd

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.config.settings import EXPORTS_PATH
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


def inspect_dataset(season: int) -> None:
    tables = Tables(SeasonData(season))
    enriched_tidy_fixtures = tables.enriched_tidy_fixtures()

    adapter = PoissonSameVenueAverageAdapter("average", season)
    standings = adapter.get_brasileirao_standings(enriched_tidy_fixtures)
    team_params = adapter.get_team_params(enriched_tidy_fixtures)

    pd.options.display.max_columns = 20
    pd.options.display.max_rows = 50
    pd.set_option("display.width", 1000)

    export_dir = f"{EXPORTS_PATH}/{season}"
    os.makedirs(export_dir, exist_ok=True)
    enriched_tidy_fixtures.to_csv(f"{export_dir}/out_enriched_tidy_fixtures.csv", index=False)
    standings.to_csv(f"{export_dir}/out_standings.csv", index=False)
    team_params.to_csv(f"{export_dir}/out_team_params.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    inspect_dataset(args.season)

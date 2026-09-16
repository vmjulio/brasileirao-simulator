import argparse
import os

import pandas as pd

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH, RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.service_layer.data_transformation_service import (
    DataTransformationService,
)


def results_history_dataset(season: int):
    dates = SeasonData(season).dates
    service = DataTransformationService(
        strategy="average", persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season)
    )
    return (
        service.results_pkl_to_rows(dates),
        service.relegation_points_pkl_to_rows(dates),
        service.positions_pkl_to_rows(dates),
    )


def dump_rows_to_file(rows, season: int, file_name: str) -> None:
    export_dir = f"{EXPORTS_PATH}/{season}"
    os.makedirs(export_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(f"{export_dir}/{file_name}", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a season's simulation history.")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    results, relegation_points, positions = results_history_dataset(args.season)
    dump_rows_to_file(results, args.season, "results_pivot.csv")
    dump_rows_to_file(relegation_points, args.season, "relegation_points.csv")
    dump_rows_to_file(positions, args.season, "positions.csv")

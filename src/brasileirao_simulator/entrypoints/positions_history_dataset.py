import argparse
import os

import pandas as pd

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH, RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.service_layer.data_transformation_service import (
    DataTransformationService,
)


def positions_history_dataset(season: int):
    service = DataTransformationService(
        strategy="average", persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season)
    )
    return service.positions_pkl_to_rows(SeasonData(season).dates)


def dump_rows_to_file(rows, season: int) -> None:
    export_dir = f"{EXPORTS_PATH}/{season}"
    os.makedirs(export_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(f"{export_dir}/positions_pivot.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a season's simulated positions.")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    dump_rows_to_file(positions_history_dataset(args.season), args.season)

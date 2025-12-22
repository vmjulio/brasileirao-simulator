from brasileirao_simulator.service_layer.data_transformation_service import DataTransformationService
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY, DATASETS_PATH, DATES
import pandas as pd


def matches_history_dataset(date: str = None) -> None:
    persistence_adapter: PickleAdapter = PickleAdapter(RESULTS_DIRECTORY)
    data_transformation_service = DataTransformationService(strategy="average", persistence_adapter=persistence_adapter)
    return data_transformation_service.matches_pkl_to_rows(DATES)


def dump_rows_to_file(rows):
    df = pd.DataFrame(rows)
    df.to_csv(DATASETS_PATH + "/matches_results_pivot.csv", index=False)


if __name__ == "__main__":
    rows = matches_history_dataset()
    dump_rows_to_file(rows)

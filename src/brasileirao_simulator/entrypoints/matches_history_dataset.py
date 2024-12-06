from brasileirao_simulator.service_layer.data_transformation_service import DataTransformationService
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY, DATASETS_PATH
import pandas as pd


def matches_history_dataset(date: str = None) -> None:
    persistence_adapter: PickleAdapter = PickleAdapter(RESULTS_DIRECTORY)
    data_transformation_service = DataTransformationService(strategy="average", persistence_adapter=persistence_adapter)
    dates = ['2024-07-27',
             '2024-07-28',
             '2024-08-03',
             '2024-08-04',
             '2024-08-05',
             '2024-08-10',
             '2024-08-11',
             '2024-08-14',
             '2024-08-17',
             '2024-08-18',
             '2024-08-19',
             '2024-08-24',
             '2024-08-25',
             '2024-08-26',
             '2024-08-28',
             '2024-08-31',
             '2024-09-01',
             '2024-09-05',
             '2024-09-11',
             '2024-09-14',
             '2024-09-15',
             '2024-09-16',
             '2024-09-21',
             '2024-09-22',
             '2024-09-23',
             '2024-09-25',
             '2024-09-28',
             '2024-09-29',
             '2024-10-03',
             '2024-10-04',
             '2024-10-05',
             '2024-10-09',
             '2024-10-16',
             '2024-10-17',
             '2024-10-18',
             '2024-10-19',
             '2024-10-20',
             '2024-10-22',
             '2024-10-25',
             '2024-10-26',
             '2024-10-28',
             '2024-10-30',
             '2024-11-01',
             '2024-11-02',
             '2024-11-04',
             '2024-11-05',
             '2024-11-06',
             '2024-11-08',
             '2024-11-09',
             '2024-11-13',
             '2024-11-16',
             '2024-11-20',
             '2024-11-21',
             '2024-11-22',
             '2024-11-23',
             '2024-11-24',
             '2024-11-26',
             #'2024-11-30',
             #'2024-12-03',
             #'2024-12-07'
             ]
    return data_transformation_service.matches_pkl_to_rows(dates)


def dump_rows_to_file(rows):
    df = pd.DataFrame(rows)
    df.to_csv(DATASETS_PATH + "/matches_results_pivot.csv", index=False)


if __name__ == "__main__":
    rows = matches_history_dataset()
    dump_rows_to_file(rows)

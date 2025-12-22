from brasileirao_simulator.service_layer.data_transformation_service import DataTransformationService
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY, DATASETS_PATH, DATES
import pandas as pd


def results_history_dataset(date: str = None) -> None:
    persistence_adapter: PickleAdapter = PickleAdapter(RESULTS_DIRECTORY)
    data_transformation_service = DataTransformationService(strategy="average", persistence_adapter=persistence_adapter)
    results = data_transformation_service.results_pkl_to_rows(DATES)
    relegation_points = data_transformation_service.relegation_points_pkl_to_rows(DATES)
    positions = data_transformation_service.positions_pkl_to_rows(DATES)
    return results, relegation_points, positions 


def dump_rows_to_file(rows, file_name):
    df = pd.DataFrame(rows)
    df.to_csv(DATASETS_PATH + "/" + file_name, index=False)


if __name__ == "__main__":
    results, relegation_points, positions = results_history_dataset()
    dump_rows_to_file(results, "results_pivot.csv")
    dump_rows_to_file(relegation_points, "relegation_points.csv")
    dump_rows_to_file(positions, "positions.csv")

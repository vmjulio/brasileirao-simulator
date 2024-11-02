import pickle
from typing import Any
from brasileirao_simulator.ports.persistence_port import PersistencePort
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY


class PickleAdapter(PersistencePort):
    def __init__(self, directory: str) -> None:
        self.directory: str = directory

    def save_results(self, results: Any, strategy: str) -> None:
        file_path = f"{self.directory}/{strategy}_results.pkl"
        with open(file_path, "wb") as f:
            pickle.dump(results, f)

    def load_results(self, strategy: str) -> Any:
        file_path = f"{self.directory}/{strategy}_results.pkl"
        with open(file_path, "rb") as f:
            return pickle.load(f)

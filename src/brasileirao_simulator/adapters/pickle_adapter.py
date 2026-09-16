import os
import pickle
from typing import Any

from brasileirao_simulator.ports.persistence_port import PersistencePort


class PickleAdapter(PersistencePort):
    """Simulation results on disk, one directory per season."""

    def __init__(self, directory: str, season: int) -> None:
        self.directory: str = f"{directory}/{season}"
        os.makedirs(self.directory, exist_ok=True)

    def save_results(self, results: Any, strategy: str, suffix: str = None) -> None:
        with open(self._file_path(strategy, suffix), "wb") as f:
            pickle.dump(results, f)

    def load_results(self, strategy: str, suffix: str = None) -> Any:
        file_path = self._file_path(strategy, suffix)
        if os.path.isfile(file_path):
            with open(file_path, "rb") as f:
                return pickle.load(f)
        return None

    def _file_path(self, strategy: str, suffix: str = None) -> str:
        appendix = f"_{suffix}" if suffix else ""
        return f"{self.directory}/{strategy}_results{appendix}.pkl"

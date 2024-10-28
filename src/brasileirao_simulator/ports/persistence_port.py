from abc import ABC, abstractmethod


class PersistencePort(ABC):
    @abstractmethod
    def save_results(self, strategy: str, results: dict) -> None:
        pass

    @abstractmethod
    def load_results(self, strategy: str) -> dict:
        pass

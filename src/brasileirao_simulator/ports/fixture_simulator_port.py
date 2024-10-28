from abc import ABC, abstractmethod
import pandas as pd


class FixtureSimulatorPort(ABC):
    @abstractmethod
    def log(self, message: str):
        pass

    @abstractmethod
    def simulate_fixtures(self, fixtures:pd.DataFrame, remaining_games:pd.DataFrame):
        pass
    
    @abstractmethod
    def get_brasileirao_standings(self, df:pd.DataFrame):
        pass

    @abstractmethod
    def get_bolao_standings(self, df:pd.DataFrame):
        pass

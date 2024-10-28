from abc import ABC, abstractmethod


class LoggerPort(ABC):
    @abstractmethod
    def log(self, message: str):
        pass

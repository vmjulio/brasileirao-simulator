import pickle
from brasileirao_simulator.ports.persistence_port import PersistencePort


class PickleAdapter(PersistencePort):
    def __init__(self, directory):
        self.directory = directory

    def save_results(self, results, strategy):
        file_path = f"{self.directory}/{strategy}_results.pkl"
        with open(file_path, "wb") as f:
            pickle.dump(results, f)

    def load_results(self, strategy):
        file_path = f"{self.directory}/{strategy}_results.pkl"
        with open(file_path, "rb") as f:
            return pickle.load(f)

from brasileirao_simulator.ports.persistence_port import PersistencePort
from brasileirao_simulator.ports.fixture_simulator_port import FixtureSimulatorPort
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.domain.simulation_runner import SimulationRunner
from brasileirao_simulator.domain.result_logger import ResultLogger


class DataTransformationService:
    def __init__(self, strategy, persistence_adapter: PersistencePort) -> None:
        self.persistence_adapter: PersistencePort = persistence_adapter
        self.strategy: str = strategy

    def _create_rows_results(self, results, suffix: str) -> list:
        pivot_results = []
        for k, v in results.items():
            if k in ["brasileirao_title", "brasileirao_relegation", "bolao"]:
                for r in v.items():
                    pivot_results.append({"type": k, "date": suffix, "team": r[0], "n_simulations": r[1]})
        return pivot_results

    def _create_rows_matches(self, results, suffix: str):
        pivot_matches = []
        for k, v in results.items():
            if k == "match_results":
                for r in v.items():
                    pivot_matches.append({"type": k,
                                          "date": suffix,
                                          "match": r[0],
                                          "team_home": r[0].split(" x ")[0],
                                          "team_away": r[0].split(" x ")[1],
                                          "home": r[1]["home"],
                                          "draw": r[1]["draw"],
                                          "away": r[1]["away"],
                                          "matches_simulated": r[1]["draw"] + r[1]["home"] + r[1]["away"],
                                          "round": r[1]["round_"]})
        return pivot_matches

    # "results" should be a class
    def results_pkl_to_rows(self, suffix_list: list = []) -> None:
        pivot_results = []
        for suffix in suffix_list:
            results = self.persistence_adapter.load_results(self.strategy, suffix=suffix)
            row_dict = self._create_rows_results(results, suffix)
            pivot_results.extend(row_dict)
        return pivot_results
    
    def matches_pkl_to_rows(self, suffix_list: list = []) -> None:
        pivot_results = []
        for suffix in suffix_list:
            results = self.persistence_adapter.load_results(self.strategy, suffix=suffix)
            row_dict = self._create_rows_matches(results, suffix)
            pivot_results.extend(row_dict)
        return pivot_results

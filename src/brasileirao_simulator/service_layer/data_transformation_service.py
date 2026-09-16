from brasileirao_simulator.ports.persistence_port import PersistencePort


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

    def _create_rows_positions(self, results, suffix: str):
        pivot_positions = []
        for k, v in results.items():
            if k == "brasileirao_positions":
                for r in v.items():
                    for position, times in r[1].items():
                        pivot_positions.append({"type": k,
                                                "date": suffix,
                                                "team": r[0],
                                                "position": position,
                                                "times": times})
        return pivot_positions

    def _create_rows_relegation_points(self, results, suffix: str):
        pivot_positions = []
        for k, v in results.items():
            if k == "brasileirao_relegation_points":
                for r in v.items():
                    for position, times in r[1].items():
                        pivot_positions.append({"type": k,
                                                "date": suffix,
                                                "points": r[0],
                                                "position": position,
                                                "times": times})
        return pivot_positions
    
    
    def results_pkl_to_rows(self, suffix_list: list = []) -> list:
        pivot_results = []
        for suffix in suffix_list:
            results = self.persistence_adapter.load_results(self.strategy, suffix=suffix)
            if results is None:
                # A date that was never simulated is a gap, not a failure.
                continue
            pivot_results.extend(self._create_rows_results(results, suffix))
        return pivot_results

    def relegation_points_pkl_to_rows(self, suffix_list: list = []) -> list:
        pivot_results = []
        for suffix in suffix_list:
            results = self.persistence_adapter.load_results(self.strategy, suffix=suffix)
            if results is None:
                # A date that was never simulated is a gap, not a failure.
                continue
            pivot_results.extend(self._create_rows_relegation_points(results, suffix))
        return pivot_results

    def positions_pkl_to_rows(self, suffix_list: list = []) -> list:
        pivot_results = []
        for suffix in suffix_list:
            results = self.persistence_adapter.load_results(self.strategy, suffix=suffix)
            if results is None:
                # A date that was never simulated is a gap, not a failure.
                continue
            pivot_results.extend(self._create_rows_positions(results, suffix))
        return pivot_results

    def matches_pkl_to_rows(self, suffix_list: list = []) -> list:
        pivot_results = []
        for suffix in suffix_list:
            results = self.persistence_adapter.load_results(self.strategy, suffix=suffix)
            if results is None:
                # A date that was never simulated is a gap, not a failure.
                continue
            pivot_results.extend(self._create_rows_matches(results, suffix))
        return pivot_results

from collections import defaultdict
import json
from functools import partial
from typing import Dict, Any


class ResultLogger:
    def __init__(self) -> None:
        self.brasileirao_title_positions: Dict[str, int] = defaultdict(int)
        self.brasileirao_relegation_positions: Dict[str, int] = defaultdict(int)
        self.bolao_positions: Dict[str, int] = defaultdict(int)
        self.match_results = defaultdict(partial(defaultdict, int))

    def log_brasileirao_results(self, bras_standings: Any) -> None:
        for row in bras_standings.to_dict(orient="records"):
            if row["rank_"] == 1:
                self.brasileirao_title_positions[row["team_name"]] += 1
            elif row["rank_"] >= 17:
                self.brasileirao_relegation_positions[row["team_name"]] += 1

    def log_bolao_results(self, bolao_standings: Any) -> None:
        for row in bolao_standings.to_dict(orient="records"):
            if row["rank_"] == 1:
                self.bolao_positions[row["punter"]] += 1

    def log_match_results(self, match_results: Any) -> None:
        for row in match_results.to_dict(orient="records"):
            if row["home_goals"] > row["away_goals"]:
                self.match_results[row["home_team"] + " x " + row["away_team"]]["home"] += 1
            if row["home_goals"] < row["away_goals"]:
                self.match_results[row["home_team"] + " x " + row["away_team"]]["away"] += 1
            if row["home_goals"] == row["away_goals"]:
                self.match_results[row["home_team"] + " x " + row["away_team"]]["draw"] += 1
            self.match_results[row["home_team"] + " x " + row["away_team"]]["round_"] = row["round_"]

    def get_results(self) -> Dict[str, Dict[str, int]]:
        return {
            "brasileirao_title": self.brasileirao_title_positions,
            "brasileirao_relegation": self.brasileirao_relegation_positions,
            "bolao": self.bolao_positions,
            "match_results": self.match_results
        }

    def load_results(self, results: Dict[str, Dict[str, int]]) -> None:
        self.brasileirao_title_positions = results["brasileirao_title"]
        self.brasileirao_relegation_positions = results["brasileirao_relegation"]
        self.bolao_positions = results["bolao"]
        self.match_results = results["match_results"]

    def _sorted_defaultdict(self, d: Dict[str, int], correction: float = 1.0) -> str:
        total = sum(d.values())
        r = dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True))
        j = {team: f'{100 * count * correction / total:.2f}%' for team, count in r.items()}
        return json.dumps(j, indent=4)
    
    # TODO assign a type to d
    def _sorted_nested_defaultdict(self, d, correction: float = 1.0) -> str:
        return json.dumps(d, indent=4)

    def print_results(self) -> None:
        print("Brasileirao Title Positions:", self._sorted_defaultdict(self.brasileirao_title_positions))
        print("Brasileirao Relegation Positions:", self._sorted_defaultdict(self.brasileirao_relegation_positions, correction=4.0))
        print("Bolao Results:", self._sorted_defaultdict(self.bolao_positions))
        print("--")
        print("Total iterations:", sum([v for k, v in self.brasileirao_title_positions.items()]))
    
    def print_matches(self) -> None:
        print("Matches:", self._sorted_nested_defaultdict(self.match_results))

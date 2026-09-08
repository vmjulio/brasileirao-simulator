from collections import defaultdict
import json
from functools import partial
from typing import Dict, Any

from brasileirao_simulator.domain.batch_simulation import BatchOutcome


class ResultLogger:
    def __init__(self) -> None:
        self.brasileirao_title_positions: Dict[str, int] = defaultdict(int)
        self.brasileirao_relegation_points: Dict[str, int] = defaultdict(lambda: defaultdict(int))
        self.brasileirao_relegation_positions: Dict[str, int] = defaultdict(int)
        self.brasileirao_positions: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))

        self.match_results = defaultdict(partial(defaultdict, int))
        self.match_odds = defaultdict(partial(defaultdict, int))

    def log_brasileirao_results(self, bras_standings: Any) -> None:
        for row in bras_standings.to_dict(orient="records"):
            if row["rank_"] == 1:
                self.brasileirao_title_positions[row["team_name"]] += 1
            elif row["rank_"] >= 17:
                self.brasileirao_relegation_positions[row["team_name"]] += 1
    
    def log_brasileirao_relegation_points(self, bras_standings: Any) -> None:
        for row in bras_standings.to_dict(orient="records"):
            self.brasileirao_relegation_points[row["p"]][row["rank_"]] += 1
    
    def log_brasileirao_positions(self, bras_standings: Any) -> None:
        for row in bras_standings.to_dict(orient="records"):
            self.brasileirao_positions[row["team_name"]][row["rank_"]] += 1

    def log_batch(self, outcome: BatchOutcome) -> None:
        """Count a whole batch of simulated seasons.

        Increments exactly the counters the per-season log_* methods do, so the
        pickle a batch run writes is indistinguishable from one written a season
        at a time - which is what keeps existing results comparable. Reads the
        team ordering from outcome.baseline.teams - the same list rank's
        columns were built against - rather than re-deriving it, so there is
        only one place that ordering comes from.
        """
        for position, team in enumerate(outcome.baseline.teams):
            ranks = outcome.rank[:, position]
            self.brasileirao_title_positions[team] += int((ranks == 1).sum())
            self.brasileirao_relegation_positions[team] += int((ranks >= 17).sum())
            for rank in ranks:
                self.brasileirao_positions[team][int(rank)] += 1
            for points, rank in zip(outcome.points[:, position], outcome.rank[:, position]):
                self.brasileirao_relegation_points[float(points)][int(rank)] += 1

        baseline = outcome.baseline
        for fixture in range(outcome.home_goals.shape[1]):
            key = f"{baseline.home_name[fixture]} x {baseline.away_name[fixture]}"
            home = outcome.home_goals[:, fixture]
            away = outcome.away_goals[:, fixture]
            self.match_results[key]["home"] += int((home > away).sum())
            self.match_results[key]["away"] += int((away > home).sum())
            self.match_results[key]["draw"] += int((home == away).sum())
            self.match_results[key]["round_"] = int(baseline.round_[fixture])

    def log_match_results(self, match_results: Any) -> None:
        for row in match_results.to_dict(orient="records"):
            if row["home_goals"] > row["away_goals"]:
                self.match_results[row["home_team"] + " x " + row["away_team"]]["home"] += 1
            if row["home_goals"] < row["away_goals"]:
                self.match_results[row["home_team"] + " x " + row["away_team"]]["away"] += 1
            if row["home_goals"] == row["away_goals"]:
                self.match_results[row["home_team"] + " x " + row["away_team"]]["draw"] += 1
            self.match_results[row["home_team"] + " x " + row["away_team"]]["round_"] = row["round_"]
    
    def log_match_results_specific_round(self, match_results: Any, round_: int = None) -> None:
        for row in match_results.to_dict(orient="records"):
            if round_ and row["round_"] == round_:
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
            "brasileirao_relegation_points": {k: dict(v) for k, v in self.brasileirao_relegation_points.items()},
            "brasileirao_positions": {k: dict(v) for k, v in self.brasileirao_positions.items()},
            "match_results": self.match_results
        }

    def get_nested_ddict(self, d):
        def dd():
            return defaultdict(int)

        out = defaultdict(dd)

        for team, inner in d.items():
            out[team].update(inner)

        return out

    def load_results(self, results: Dict[str, Dict[str, int]]) -> None:
        if results:
            self.brasileirao_title_positions = results["brasileirao_title"]
            self.brasileirao_relegation_positions = results["brasileirao_relegation"]
            self.brasileirao_relegation_points= self.get_nested_ddict(results["brasileirao_relegation_points"])
            self.brasileirao_positions = self.get_nested_ddict(results["brasileirao_positions"])
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
        print("--")
        print("Total iterations:", sum([v for k, v in self.brasileirao_title_positions.items()]))
    
    def print_matches(self) -> None:
        print("Matches:", self._sorted_nested_defaultdict(self.match_results))

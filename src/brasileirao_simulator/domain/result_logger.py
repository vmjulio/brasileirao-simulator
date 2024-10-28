from collections import defaultdict
import json


class ResultLogger:
    def __init__(self):
        self.brasileirao_title_positions = defaultdict(int)
        self.brasileirao_relegation_positions = defaultdict(int)
        self.bolao_positions = defaultdict(int)

    def log_brasileirao_results(self, bras_standings):
        for row in bras_standings.to_dict(orient="records"):
            if row["rank_"] == 1:
                self.brasileirao_title_positions[row["team_name"]] += 1
            elif row["rank_"] >= 17:
                self.brasileirao_relegation_positions[row["team_name"]] += 1

    def log_bolao_results(self, bolao_standings):
        for row in bolao_standings.to_dict(orient="records"):
            if row["rank_"] == 1:
                self.bolao_positions[row["punter"]] += 1

    def get_results(self):
        return {
            "brasileirao_title": self.brasileirao_title_positions,
            "brasileirao_relegation": self.brasileirao_relegation_positions,
            "bolao": self.bolao_positions
        }

    def load_results(self, results):
        self.brasileirao_title_positions = results["brasileirao_title"]
        self.brasileirao_relegation_positions = results["brasileirao_relegation"]
        self.bolao_positions = results["bolao"]

    def _sorted_defaultdict(self, d, correction=1.0):
        total = sum(d.values())
        r = dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True))
        j = {team: f'{100*count*correction/total:.2f}%' for team, count in r.items()}
        return json.dumps(j, indent=4)

    def print_results(self):
        print("Brasileirao Title Positions:", self._sorted_defaultdict(self.brasileirao_title_positions))
        print("Brasileirao Relegation Positions:", self._sorted_defaultdict(self.brasileirao_relegation_positions, correction=4.0))
        print("Bolao Results:", self._sorted_defaultdict(self.bolao_positions))
        print("--")
        print("Total iterations:", sum([v for k, v in self.brasileirao_title_positions.items()]))
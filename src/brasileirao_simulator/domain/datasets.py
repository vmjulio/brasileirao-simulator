import pandas as pd
from brasileirao_simulator.domain.teams import Teams


punters: pd.DataFrame = pd.read_json("files/json/punters.json")
doubles: pd.DataFrame = pd.read_json("files/json/doubles.json")
fixtures: pd.DataFrame = pd.read_csv("files/csvs/fixtures_20241102.csv")

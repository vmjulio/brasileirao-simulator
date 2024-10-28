import pandas as pd
from brasileirao_simulator.domain.teams import Teams


punters = pd.read_json("files/punters.json")
doubles= pd.read_json("files/doubles.json")
fixtures = pd.read_csv("files/fixtures_20241027.csv")

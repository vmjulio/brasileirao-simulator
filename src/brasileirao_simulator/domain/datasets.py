import pandas as pd
from brasileirao_simulator.domain.teams import Teams
from brasileirao_simulator.config.settings import DATASETS_PATH


punters: pd.DataFrame = pd.read_json(f"{DATASETS_PATH}/punters.json")
doubles: pd.DataFrame = pd.read_json(f"{DATASETS_PATH}/doubles.json")
fixtures: pd.DataFrame = pd.read_csv(f"{DATASETS_PATH}/fixtures_20241102.csv")

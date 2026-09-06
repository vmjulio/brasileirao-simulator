import json
import os
from typing import Optional

import duckdb
import pandas as pd

from brasileirao_simulator.config.settings import DATASETS_PATH


class SeasonMissingDataError(Exception):
    """A season folder lacks a file the simulation needs."""


class SeasonData:
    """Every dataset one season needs, loaded from files/datasets/{season}/.

    Previous-season fixtures are read from that season's own folder rather than
    a copy, so there is one source of truth for a season's results. The
    previous season matters because the lookback windows (19 games per venue,
    or 12) reach back past the start of the current season, which is what makes
    an early-season simulation meaningful.
    """

    def __init__(self, season: int, datasets_path: str = DATASETS_PATH) -> None:
        self.season: int = season
        self._datasets_path: str = datasets_path

        self.fixtures: pd.DataFrame = self._read_fixtures(season)
        self.previous_year: pd.DataFrame = self._read_fixtures(season - 1)
        self.dates: list[str] = self._read_dates()
        self.punters: Optional[pd.DataFrame] = self._read_optional_json("punters.json")
        self.doubles: Optional[pd.DataFrame] = self._read_optional_json("doubles.json")

    def register(self, con: duckdb.DuckDBPyConnection) -> None:
        """Bind the frames onto the names the SQL files select from."""
        con.register("fixtures", self.fixtures)
        con.register("previous_year", self.previous_year)

    def _season_dir(self, season: int) -> str:
        return f"{self._datasets_path}/{season}"

    def _read_fixtures(self, season: int) -> pd.DataFrame:
        return pd.read_csv(self._require(f"{self._season_dir(season)}/fixtures.csv"))

    def _read_dates(self) -> list[str]:
        with open(self._require(f"{self._season_dir(self.season)}/dates.json")) as f:
            return json.load(f)["dates"]

    def _read_optional_json(self, file_name: str) -> Optional[pd.DataFrame]:
        path = f"{self._season_dir(self.season)}/{file_name}"
        return pd.read_json(path) if os.path.isfile(path) else None

    def _require(self, path: str) -> str:
        if not os.path.isfile(path):
            raise SeasonMissingDataError(
                f"season {self.season} needs {path}, which does not exist"
            )
        return path

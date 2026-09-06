# Season Parameterization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the season a runtime parameter so 2026 can be simulated without disturbing 2025's data, results, or reproducibility.

**Architecture:** Per-season input folders under `files/datasets/{season}/` loaded by a new `SeasonData` module, which also binds DataFrames onto the DuckDB connection explicitly instead of relying on replacement scans. SQL files carry `$season` / `$previous_season` placeholders substituted by `Queries`. Results and CSV exports become season-scoped directories.

**Tech Stack:** Python 3.11 (Docker image), pandas 2.2.3, duckdb 1.1.1, numpy 2.1.2, pytest (added by Task 1), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-06-season-parameterization-design.md`

## Global Constraints

- **Tests run in Docker only.** `duckdb` and `pandas` are not installed on the host, and the package is installed into the image via `pip install -e /src`. Every test command is `docker-compose run --rm app pytest ...`.
- **Docker `WORKDIR` is `/src`.** All runtime paths in `settings.py` are relative to it (`files/datasets`, `files/pkl`). Never make them absolute.
- **`docker-compose.yml` mounts `./src:/src` and `./tests:/tests`**, so code and test edits are live without a rebuild. Rebuild (`make build`) only after changing `requirements.txt` or the `Dockerfile`.
- **Season is `int` in Python, `str` in SQL.** The `season` column is int64 from `pd.read_csv`; SQL compares quoted literals that DuckDB casts. Pandas-side filters take `int`, SQL substitution takes the value interpolated into quotes. Reversing this returns an empty DataFrame with no exception.
- **Every SQL file is read through `Queries`**, which substitutes with `string.Template.safe_substitute` — never `str.format`.
- **League stays hardcoded** as `league_id = 71`. Do not parameterize it.
- **Lookback windows stay hardcoded** (`rn <= 19`, `rn <= 12`). Out of scope.
- **Preserve git history on data moves:** use `git mv`, never `rm` + re-add.
- **2024 is data-only.** It exists to be 2025's previous year. It gets no `dates.json` and is not simulatable (that would need a 2023 folder).

---

### Task 1: Test infrastructure and the 2025 regression baseline

Nothing in `tests/` exists yet, and the whole point of this refactor is that 2025 keeps working. That guarantee needs to exist as a runnable test *before* anything changes.

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_regression_2025.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a pytest setup runnable as `docker-compose run --rm app pytest /tests -v`. No `conftest.py` is needed: the package is installed into the image with `pip install -e /src`, so imports resolve, and `WORKDIR /src` makes the relative paths in `settings.py` valid.

- [ ] **Step 1: Add pytest to requirements**

In `requirements.txt`, append:

```
pytest==8.3.3
```

- [ ] **Step 2: Rebuild the image so pytest is available**

Run: `make build`
Expected: build succeeds, `pytest` installs.

- [ ] **Step 3: Write the 2025 regression baseline test**

This test characterizes current behavior against the code as it stands today. It must keep passing, unchanged, through every later task.

Create `tests/test_regression_2025.py`:

```python
"""Characterization test: the 2025 season must simulate identically before and
after season parameterization. This file is the contract that the refactor did
not change behaviour for the season that already worked."""

import numpy as np
import pytest

from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)


BRASILEIRAO_TEAM_COUNT = 20


@pytest.fixture
def mid_season_date() -> str:
    """A date with roughly half the 2025 season played, so the simulation has
    both real results behind it and fixtures left to simulate."""
    return "2025-08-31"


def test_2025_standings_have_twenty_teams(mid_season_date):
    np.random.seed(42)
    tables = Tables()
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=mid_season_date)
    remaining = tables.remaining_games(blank_from_date=mid_season_date)

    adapter = PoissonSameVenueAverageAdapter("average")
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    standings = adapter.get_brasileirao_standings(simulated)

    assert len(standings) == BRASILEIRAO_TEAM_COUNT
    assert standings["rank_"].min() == 1
    assert standings["rank_"].max() == BRASILEIRAO_TEAM_COUNT


def test_2025_blanking_leaves_games_to_simulate(mid_season_date):
    """The as-of-date feature: results after the date are blanked, so those
    fixtures come back as games still to play."""
    tables = Tables()
    remaining = tables.remaining_games(blank_from_date=mid_season_date)

    assert len(remaining) > 0, "blanking produced no remaining games to simulate"
    assert remaining["goals_for"].isnull().all()


def test_2025_every_team_gets_a_full_season(mid_season_date):
    np.random.seed(42)
    tables = Tables()
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=mid_season_date)
    remaining = tables.remaining_games(blank_from_date=mid_season_date)

    adapter = PoissonSameVenueAverageAdapter("average")
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    standings = adapter.get_brasileirao_standings(simulated)

    assert (standings["g"] == 38).all(), "a simulated season must have 38 games per team"
```

- [ ] **Step 4: Run the tests and verify they pass against current code**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: 3 passed. If any fail, stop — the baseline must be green before refactoring. Report the failure rather than adjusting the test to match.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/test_regression_2025.py
git commit -m "test: add pytest and 2025 regression baseline"
```

---

### Task 2: `SeasonData` and per-season data folders

**Files:**
- Create: `src/brasileirao_simulator/domain/season_data.py`
- Create: `src/brasileirao_simulator/domain/season_dates.py`
- Create: `src/brasileirao_simulator/entrypoints/generate_season_dates.py`
- Create: `tests/test_season_data.py`
- Create (data): `src/files/datasets/2024/`, `2025/`, `2026/`

**Interfaces:**
- Consumes: `DATASETS_PATH` from `config/settings.py`.
- Produces:
  - `SeasonData(season: int, datasets_path: str = DATASETS_PATH)` with attributes `season: int`, `fixtures: pd.DataFrame`, `previous_year: pd.DataFrame`, `dates: list[str]`, `punters: Optional[pd.DataFrame]`, `doubles: Optional[pd.DataFrame]`, and method `register(con: duckdb.DuckDBPyConnection) -> None`.
  - `SeasonMissingDataError(Exception)`.
  - `dates_from_fixtures(fixtures: pd.DataFrame) -> list[str]`.

This task only *copies* data into the new layout. Old flat files stay in place so existing code keeps running; Task 6 removes them.

- [ ] **Step 1: Create the season folders and copy data in**

```bash
cd src/files/datasets
mkdir -p 2024 2025 2026
cp fixtures_2024.csv 2024/fixtures.csv
git mv punters_2024.json 2024/punters.json
git mv doubles_2024.json 2024/doubles.json
cp fixtures_2025.csv 2025/fixtures.csv
cp punters.json 2025/punters.json
cp doubles.json 2025/doubles.json
cp ~/Documents/GitHub/lean-pype/app/files/processed_fixtures_2026_71.csv 2026/fixtures.csv
```

Both fixtures files are **copied, not moved**: `domain/datasets.py:8-9` still
reads `fixtures_2025.csv` and `fixtures_2024.csv` from the flat paths at import
time, and it survives until Task 6. Moving either one here would make every
import of `tables.py` raise `FileNotFoundError`, failing the Task 1 baseline.
Task 6 deletes the flat originals once nothing reads them.

`punters_2024.json` and `doubles_2024.json` are safe to `git mv` — `datasets.py`
reads the unsuffixed `punters.json` / `doubles.json`, not these.

- [ ] **Step 2: Verify the copied data**

Run:
```bash
cd src/files/datasets && wc -l 2024/fixtures.csv 2025/fixtures.csv 2026/fixtures.csv
```
Expected: 381 lines each (380 fixtures + header).

- [ ] **Step 3: Write the failing test for date derivation**

Create `tests/test_season_data.py`:

```python
import json
import os

import pandas as pd
import pytest

from brasileirao_simulator.domain.season_dates import dates_from_fixtures


def test_dates_are_local_match_dates():
    """Kickoff times are stored in UTC; the season's dates are Brazilian local
    dates, which is UTC-3. A 2025-03-29T21:30Z kickoff is a 2025-03-29 match."""
    fixtures = pd.DataFrame(
        {"fixture_date": ["2025-03-29T21:30:00+00:00", "2025-03-30T00:30:00+00:00"]}
    )

    assert dates_from_fixtures(fixtures) == ["2025-03-29"]


def test_dates_are_sorted_and_deduplicated():
    fixtures = pd.DataFrame(
        {
            "fixture_date": [
                "2025-04-06T19:00:00+00:00",
                "2025-03-29T21:30:00+00:00",
                "2025-03-29T23:00:00+00:00",
            ]
        }
    )

    assert dates_from_fixtures(fixtures) == ["2025-03-29", "2025-04-06"]
```

- [ ] **Step 4: Run it and verify it fails**

Run: `docker-compose run --rm app pytest /tests/test_season_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brasileirao_simulator.domain.season_dates'`

- [ ] **Step 5: Implement `season_dates.py`**

Create `src/brasileirao_simulator/domain/season_dates.py`:

```python
import pandas as pd


# Kickoff times are stored in UTC; Brazilian local time is UTC-3. tidy_fixtures.sql
# applies the same shift, so a date list derived here lines up with the dates the
# simulation filters on.
BRAZIL_UTC_OFFSET_HOURS = 3


def dates_from_fixtures(fixtures: pd.DataFrame) -> list[str]:
    """Every distinct local match date in a season's fixtures, ascending."""
    kickoffs = pd.to_datetime(fixtures["fixture_date"], utc=True)
    local_dates = kickoffs - pd.Timedelta(hours=BRAZIL_UTC_OFFSET_HOURS)
    return sorted(local_dates.dt.strftime("%Y-%m-%d").unique().tolist())
```

- [ ] **Step 6: Run it and verify it passes**

Run: `docker-compose run --rm app pytest /tests/test_season_data.py -v`
Expected: 2 passed.

- [ ] **Step 7: Write the `dates.json` generator entrypoint**

Create `src/brasileirao_simulator/entrypoints/generate_season_dates.py`:

```python
"""Write files/datasets/{season}/dates.json from that season's fixtures.

The date list is committed rather than derived at runtime so a season's dates
are inspectable and stable, but generating it avoids transcribing them by hand.
"""

import argparse
import json

import pandas as pd

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.season_dates import dates_from_fixtures


def generate_season_dates(season: int) -> list[str]:
    season_dir = f"{DATASETS_PATH}/{season}"
    fixtures = pd.read_csv(f"{season_dir}/fixtures.csv")
    dates = dates_from_fixtures(fixtures)

    with open(f"{season_dir}/dates.json", "w") as f:
        json.dump({"dates": dates}, f, indent=2)

    return dates


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    written = generate_season_dates(args.season)
    print(f"wrote {len(written)} dates for season {args.season}")
```

- [ ] **Step 8: Generate dates for 2025 and 2026**

Run:
```bash
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/generate_season_dates.py --season 2025
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/generate_season_dates.py --season 2026
```
Expected: both print a date count. 2025 should be 107.

- [ ] **Step 9: Verify 2025's generated dates match the existing `DATES` list**

This proves backfill will replay 2025 identically.

Run:
```bash
docker-compose run --rm app python3 -c "
import json
from brasileirao_simulator.config.settings import DATES
generated = json.load(open('files/datasets/2025/dates.json'))['dates']
print('generated:', len(generated), 'settings:', len(DATES))
print('identical:', generated == DATES)
print('only in generated:', sorted(set(generated) - set(DATES)))
print('only in settings:', sorted(set(DATES) - set(generated)))
"
```
Expected: `identical: True`. If not, do not proceed — investigate the difference and report it. A mismatch means the derived dates would change backfill's output.

- [ ] **Step 10: Write the failing tests for `SeasonData`**

Append to `tests/test_season_data.py`:

```python
from brasileirao_simulator.domain.season_data import SeasonData, SeasonMissingDataError


def test_loads_the_requested_season():
    season_data = SeasonData(2026)

    assert season_data.season == 2026
    assert len(season_data.fixtures) == 380
    assert (season_data.fixtures["league_season"] == 2026).all()


def test_previous_year_resolves_to_the_prior_season_folder():
    season_data = SeasonData(2026)

    assert (season_data.previous_year["league_season"] == 2025).all()


def test_dates_come_from_the_season_folder():
    season_data = SeasonData(2026)

    assert season_data.dates[0].startswith("2026-")
    assert season_data.dates == sorted(season_data.dates)


def test_missing_previous_season_names_the_path():
    """2024 is data-only: simulating it would need a 2023 folder that is absent."""
    with pytest.raises(SeasonMissingDataError) as excinfo:
        SeasonData(2024)

    assert "2023" in str(excinfo.value)


def test_optional_datasets_are_none_when_absent():
    """The bolao feature is dormant; a season without punters still simulates."""
    season_data = SeasonData(2026)

    assert season_data.punters is None
    assert season_data.doubles is None


def test_register_binds_frames_for_sql(tmp_path):
    import duckdb

    season_data = SeasonData(2026)
    con = duckdb.connect()
    season_data.register(con)

    assert con.sql("select count(*) as n from fixtures").df()["n"].iloc[0] == 380
    assert con.sql("select count(*) as n from previous_year").df()["n"].iloc[0] == 380
```

- [ ] **Step 11: Run them and verify they fail**

Run: `docker-compose run --rm app pytest /tests/test_season_data.py -v`
Expected: the six new tests FAIL with `ModuleNotFoundError: No module named 'brasileirao_simulator.domain.season_data'`

- [ ] **Step 12: Implement `SeasonData`**

Create `src/brasileirao_simulator/domain/season_data.py`:

```python
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
```

- [ ] **Step 13: Run the tests and verify they pass**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: all pass, including the Task 1 regression baseline (still untouched).

- [ ] **Step 14: Commit**

```bash
git add src/brasileirao_simulator/domain/season_data.py \
        src/brasileirao_simulator/domain/season_dates.py \
        src/brasileirao_simulator/entrypoints/generate_season_dates.py \
        tests/test_season_data.py \
        src/files/datasets/2024 src/files/datasets/2025 src/files/datasets/2026
git commit -m "feat: add SeasonData and per-season data folders"
```

---

### Task 3: Thread the season through queries, tables, and adapters

The cutover. `Queries`, `Tables`, and both adapters change together because the SQL placeholders are meaningless until every caller supplies a season. Splitting them would leave the tree broken between commits.

**Files:**
- Modify: `src/brasileirao_simulator/domain/queries.py`
- Modify: `src/files/queries/tidy_fixtures.sql:17,34,53`
- Modify: `src/files/queries/standings.sql:16`
- Modify: `src/brasileirao_simulator/domain/tables.py`
- Modify: `src/brasileirao_simulator/domain/simulation_params.py`
- Modify: `src/brasileirao_simulator/adapters/poisson_same_venue_average_adapter.py`
- Modify: `src/brasileirao_simulator/adapters/poisson_weighted_venue_average_adapter.py`
- Modify: `src/brasileirao_simulator/service_layer/simulation_service.py`
- Modify: `tests/test_regression_2025.py`
- Create: `tests/test_queries.py`

**Interfaces:**
- Consumes: `SeasonData` and `SeasonMissingDataError` from Task 2.
- Produces:
  - `Queries(season: int)` with `read_sql(file_name: str) -> str` and the existing per-query methods unchanged in name.
  - `Tables(season_data: SeasonData)` with `enriched_tidy_fixtures(blank_from_date: str = None) -> pd.DataFrame` and `remaining_games(blank_from_date: str = None) -> pd.DataFrame`.
  - Both adapters as `Adapter(strategy: Optional[str], season: int)`.
  - `SimulationParams(season: int, iterations: int = 500, max_batch_size: int = 50, load_results: bool = True, ignore_results_after: str = None, strategy: Optional[str] = "average")` — `season` is the first field because a dataclass cannot put a defaulted field before a required one.

- [ ] **Step 1: Write the failing test for `Queries`**

Create `tests/test_queries.py`:

```python
import pytest

from brasileirao_simulator.domain.queries import Queries


def test_season_is_substituted_into_sql():
    sql = Queries(2026).tidy_fixtures()

    assert "'2026'" in sql
    assert "'2025'" in sql, "the previous season must appear for the lookback union"


def test_no_placeholder_survives_substitution():
    """An unsubstituted placeholder is a silent wrong answer, not a crash:
    DuckDB would fail or filter on nothing depending on where it landed."""
    for sql in (Queries(2026).tidy_fixtures(), Queries(2026).standings()):
        assert "$" not in sql


def test_stale_season_literals_are_gone():
    sql = Queries(2026).tidy_fixtures()

    assert "'2024'" not in sql, "a hardcoded 2024 survived parameterization"


def test_standings_filters_the_requested_season():
    assert "season = '2026'" in Queries(2026).standings()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `docker-compose run --rm app pytest /tests/test_queries.py -v`
Expected: FAIL — `TypeError: Queries() takes no arguments` (the current class has no `__init__`).

- [ ] **Step 3: Add placeholders to the SQL**

In `src/files/queries/tidy_fixtures.sql`, replace line 17:

```sql
    where league_season = '$previous_season' and league_id = 71
```

line 34:

```sql
    where league_season = '$season' and league_id = 71
```

and line 53:

```sql
    WHERE fixtures.league_season in ('$season', '$previous_season')
```

In `src/files/queries/standings.sql`, replace line 16:

```sql
      and season = '$season'
```

- [ ] **Step 4: Implement `Queries` substitution**

Replace the top of `src/brasileirao_simulator/domain/queries.py`:

```python
from string import Template

from brasileirao_simulator.config.settings import QUERIES_PATH


class Queries:
    """SQL text with the season bound in.

    Uses string.Template rather than str.format so a literal brace in SQL can
    never be mistaken for a placeholder.
    """

    def __init__(self, season: int) -> None:
        self.season: int = season

    def read_sql(self, file_name: str) -> str:
        with open(f"{QUERIES_PATH}/{file_name}", "r") as f:
            template = Template(f.read())
        return template.safe_substitute(
            season=self.season, previous_season=self.season - 1
        )
```

Leave every method below `read_sql` exactly as it is.

- [ ] **Step 5: Run the query tests and verify they pass**

Run: `docker-compose run --rm app pytest /tests/test_queries.py -v`
Expected: 4 passed.

- [ ] **Step 6: Rewrite `Tables` to take `SeasonData`**

Replace `src/brasileirao_simulator/domain/tables.py` entirely:

```python
import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData


class Tables:
    """The fixture tables a simulation starts from, for one season."""

    def __init__(self, season_data: SeasonData) -> None:
        self.season_data: SeasonData = season_data
        self.con = duckdb.connect()
        self.season_data.register(self.con)
        self.queries: Queries = Queries(season_data.season)

    def enriched_tidy_fixtures(self, blank_from_date: str = None) -> pd.DataFrame:
        """Fixtures with running totals, form, and rank per round.

        blank_from_date erases results after a date so the simulation can be run
        "as of" that day. The blanked frame is registered before the enriching
        query runs, which is what makes the blanking take effect downstream.
        """
        tidy_fixtures: pd.DataFrame = self.con.sql(self.queries.tidy_fixtures()).df()

        if blank_from_date:
            condition = tidy_fixtures["fixture_date"] > (blank_from_date + " 23:59:59")
            new_values = {"goals_for": np.nan, "goals_against": np.nan, "points": 0.0}
            tidy_fixtures.loc[condition, ["goals_for", "goals_against", "points"]] = (
                tidy_fixtures.loc[
                    condition, ["goals_for", "goals_against", "points"]
                ].assign(**new_values)
            )

        self.con.register("tidy_fixtures", tidy_fixtures)
        return self.con.sql(self.queries.enriched_tidy_fixtures()).df()

    def remaining_games(self, blank_from_date: str = None) -> pd.DataFrame:
        enriched_tidy_fixtures: pd.DataFrame = self.enriched_tidy_fixtures(blank_from_date)
        home_filter = enriched_tidy_fixtures["venue"] == "home"
        not_played_filter = enriched_tidy_fixtures["goals_for"].isnull()

        return enriched_tidy_fixtures[(not_played_filter) & (home_filter)]
```

Two changes beyond the season: `self.con.register("tidy_fixtures", ...)` replaces the replacement scan that previously bound the local variable by name (without it, blanking silently stops working), and the unused `debug_specific_round` parameter is dropped.

- [ ] **Step 7: Make `season` a required field on `SimulationParams`**

Replace `src/brasileirao_simulator/domain/simulation_params.py`:

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class SimulationParams:
    # season has no default and so must come first: a dataclass cannot place a
    # defaulted field before a required one. It is required because a default is
    # how a 2026 run ends up reading 2025 data unnoticed.
    season: int
    iterations: int = 500
    max_batch_size: int = 50
    load_results: bool = True
    ignore_results_after: str = None
    strategy: Optional[str] = "average"
```

- [ ] **Step 8: Update the same-venue adapter**

In `src/brasileirao_simulator/adapters/poisson_same_venue_average_adapter.py`, replace the constructor:

```python
    def __init__(self, strategy: Optional[str] = None, season: int = None) -> None:
        super(FixtureSimulatorPort, self).__init__()
        self.con = duckdb.connect()
        self.strategy: Optional[str] = strategy
        self.season: int = season
        self.queries: Queries = Queries(season)
```

replace line 31 inside `_simulate_average`:

```python
        # int, not str: the season column is int64 from read_csv, while the SQL
        # compares quoted literals that DuckDB casts.
        new_fixtures = new_fixtures[new_fixtures["season"] == self.season]
```

and replace the four query methods, each registering its frame explicitly:

```python
    def get_team_params(self, new_fixtures: pd.DataFrame) -> pd.DataFrame:
        self.con.register("new_fixtures", new_fixtures)
        return self.con.sql(self.queries.team_params_same_venue_average()).df()

    def get_brasileirao_standings(self, df: pd.DataFrame) -> pd.DataFrame:
        self.con.register("enriched_tidy_fixtures", df)
        return self.con.sql(self.queries.standings()).df()

    def get_bolao_standings(self, df: pd.DataFrame) -> pd.DataFrame:
        self.con.register("df", df)
        return self.con.sql(self.queries.bolao_standings()).df()

    def get_match_results(self, df: pd.DataFrame) -> pd.DataFrame:
        self.con.register("df", df)
        return self.con.sql(self.queries.match_results()).df()
```

- [ ] **Step 9: Update the weighted-venue adapter the same way**

In `src/brasileirao_simulator/adapters/poisson_weighted_venue_average_adapter.py`, apply the identical constructor and the identical four query methods, except `get_team_params` calls `self.queries.team_params_weighted_venue_average()`.

Then add the season filter this adapter is currently missing — replace the end of `_simulate_weighted` (line 31):

```python
        # Present in the same-venue adapter but absent here, so this adapter has
        # been returning previous-season rows to the caller. The standings query
        # filtered them out, which hid it.
        new_fixtures = new_fixtures[new_fixtures["season"] == self.season]
        return new_fixtures
```

- [ ] **Step 10: Update `SimulationService` to build `SeasonData`**

In `src/brasileirao_simulator/service_layer/simulation_service.py`, add the import:

```python
from brasileirao_simulator.domain.season_data import SeasonData
```

and replace the body of `run_simulation`:

```python
    def run_simulation(self, print_results: bool = False) -> None:
        tables = Tables(SeasonData(self.params.season))

        simulation_runner = SimulationRunner(
            fixtures=tables.enriched_tidy_fixtures(blank_from_date=self.params.ignore_results_after),
            remaining_games=tables.remaining_games(blank_from_date=self.params.ignore_results_after),
            params=self.params,
            simulator=self.simulator_adapter,
            logger=ResultLogger(),
            persistence=self.persistence_adapter,
            file_suffix=self.params.ignore_results_after
        )

        simulation_runner.run()
```

- [ ] **Step 11: Update the regression test's construction calls**

In `tests/test_regression_2025.py`, add the import:

```python
from brasileirao_simulator.domain.season_data import SeasonData
```

and in all three tests replace `Tables()` with `Tables(SeasonData(2025))` and `PoissonSameVenueAverageAdapter("average")` with `PoissonSameVenueAverageAdapter("average", season=2025)`.

The assertions do not change. That is the point: same expected behavior, new wiring.

- [ ] **Step 12: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: all pass. The 2025 regression assertions passing unchanged is the evidence that the cutover preserved behavior.

- [ ] **Step 13: Add the 2026 equivalent of the regression test**

Create `tests/test_simulation_2026.py`:

```python
"""The new season must simulate as completely as the old one."""

import numpy as np

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


BRASILEIRAO_TEAM_COUNT = 20
AS_OF_DATE = "2026-03-01"


def test_2026_simulates_a_full_season_as_of_an_early_date():
    """Early in a season the lookback window reaches into 2025 for its averages.
    A full 20-team, 38-game table proves that fallback worked."""
    np.random.seed(42)
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF_DATE)
    remaining = tables.remaining_games(blank_from_date=AS_OF_DATE)

    adapter = PoissonSameVenueAverageAdapter("average", season=2026)
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    standings = adapter.get_brasileirao_standings(simulated)

    assert len(standings) == BRASILEIRAO_TEAM_COUNT
    assert (standings["g"] == 38).all()


def test_2026_standings_contain_no_2025_rows():
    """The adapter's season filter and the standings query must agree; a leak
    would show up as more than 20 teams or as a team with more than 38 games."""
    np.random.seed(42)
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF_DATE)
    remaining = tables.remaining_games(blank_from_date=AS_OF_DATE)

    adapter = PoissonSameVenueAverageAdapter("average", season=2026)
    simulated = adapter.simulate_fixtures(fixtures, remaining)

    assert (simulated["season"] == 2026).all()
```

- [ ] **Step 14: Run the full suite again**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: all pass, both seasons.

- [ ] **Step 15: Commit**

```bash
git add src/brasileirao_simulator/domain/queries.py \
        src/brasileirao_simulator/domain/tables.py \
        src/brasileirao_simulator/domain/simulation_params.py \
        src/brasileirao_simulator/adapters/ \
        src/brasileirao_simulator/service_layer/simulation_service.py \
        src/files/queries/tidy_fixtures.sql src/files/queries/standings.sql \
        tests/
git commit -m "feat: thread season through queries, tables and adapters"
```

---

### Task 4: Season-scoped result persistence

**Files:**
- Modify: `src/brasileirao_simulator/adapters/pickle_adapter.py`
- Create: `tests/test_pickle_adapter.py`
- Move (data): `src/files/pkl/*.pkl`, `src/files/pkl_bkp/*.pkl`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `PickleAdapter(directory: str, season: int)` writing to `{directory}/{season}/{strategy}_results{_suffix}.pkl`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pickle_adapter.py`:

```python
import os

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter


def test_results_are_written_under_the_season(tmp_path):
    adapter = PickleAdapter(str(tmp_path), season=2026)
    adapter.save_results({"brasileirao_title": {"Flamengo": 3}}, strategy="average")

    assert os.path.isfile(tmp_path / "2026" / "average_results.pkl")


def test_seasons_do_not_collide(tmp_path):
    """The no-suffix case wrote average_results.pkl for every season, so a 2026
    run silently overwrote the 2025 file."""
    PickleAdapter(str(tmp_path), season=2025).save_results({"a": 1}, strategy="average")
    PickleAdapter(str(tmp_path), season=2026).save_results({"b": 2}, strategy="average")

    assert PickleAdapter(str(tmp_path), season=2025).load_results("average") == {"a": 1}
    assert PickleAdapter(str(tmp_path), season=2026).load_results("average") == {"b": 2}


def test_missing_results_load_as_none(tmp_path):
    assert PickleAdapter(str(tmp_path), season=2026).load_results("average") is None


def test_suffix_still_separates_dates(tmp_path):
    adapter = PickleAdapter(str(tmp_path), season=2026)
    adapter.save_results({"a": 1}, strategy="average", suffix="2026-03-01")

    assert os.path.isfile(tmp_path / "2026" / "average_results_2026-03-01.pkl")
```

- [ ] **Step 2: Run it and verify it fails**

Run: `docker-compose run --rm app pytest /tests/test_pickle_adapter.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'season'`

- [ ] **Step 3: Implement the season-scoped adapter**

Replace `src/brasileirao_simulator/adapters/pickle_adapter.py`:

```python
import os
import pickle
from typing import Any

from brasileirao_simulator.ports.persistence_port import PersistencePort


class PickleAdapter(PersistencePort):
    """Simulation results on disk, one directory per season."""

    def __init__(self, directory: str, season: int) -> None:
        self.directory: str = f"{directory}/{season}"
        os.makedirs(self.directory, exist_ok=True)

    def save_results(self, results: Any, strategy: str, suffix: str = None) -> None:
        with open(self._file_path(strategy, suffix), "wb") as f:
            pickle.dump(results, f)

    def load_results(self, strategy: str, suffix: str = None) -> Any:
        file_path = self._file_path(strategy, suffix)
        if os.path.isfile(file_path):
            with open(file_path, "rb") as f:
                return pickle.load(f)
        return None

    def _file_path(self, strategy: str, suffix: str = None) -> str:
        appendix = f"_{suffix}" if suffix else ""
        return f"{self.directory}/{strategy}_results{appendix}.pkl"
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `docker-compose run --rm app pytest /tests/test_pickle_adapter.py -v`
Expected: 4 passed.

- [ ] **Step 5: Migrate the existing pickles into season directories**

The season is taken from the date already in each filename. This holds for every file present because a Brasileirão season sits inside one calendar year — 2026 opens on 2026-01-28. The assumption is used only here, never at runtime.

```bash
cd src/files
for dir in pkl pkl_bkp; do
  mkdir -p "$dir/2024" "$dir/2025"
  for f in "$dir"/*_20*.pkl; do
    [ -e "$f" ] || continue
    year=$(basename "$f" | grep -oE '20[0-9]{2}' | head -1)
    git mv "$f" "$dir/$year/$(basename "$f")"
  done
done
```

`pkl/average_results.pkl` has no date in its name, so its season cannot be determined. Leave it where it is — it is the collision artifact this task fixes, and guessing its season would be worse than leaving it visible.

- [ ] **Step 6: Verify the migration**

Run:
```bash
cd src/files && ls pkl/2024 | wc -l && ls pkl/2025 | wc -l && ls pkl/*.pkl 2>/dev/null | wc -l
```
Expected: 64, 108, and 1 (the undated legacy file).

- [ ] **Step 7: Commit**

```bash
git add src/brasileirao_simulator/adapters/pickle_adapter.py tests/test_pickle_adapter.py src/files/pkl src/files/pkl_bkp
git commit -m "feat: scope pickled results by season"
```

---

### Task 5: Season-aware entrypoints, exports, and Docker

**Files:**
- Modify: `src/brasileirao_simulator/config/settings.py`
- Modify: `src/brasileirao_simulator/entrypoints/current_probabilities.py`
- Modify: `src/brasileirao_simulator/entrypoints/backfill.py`
- Modify: `src/brasileirao_simulator/entrypoints/results_history_dataset.py`
- Modify: `src/brasileirao_simulator/entrypoints/matches_history_dataset.py`
- Modify: `src/brasileirao_simulator/entrypoints/positions_history_dataset.py`
- Modify: `src/brasileirao_simulator/service_layer/data_transformation_service.py`
- Modify: `entrypoint.sh`, `docker-compose.yml`

**Interfaces:**
- Consumes: `SimulationParams(season=...)`, `PickleAdapter(directory, season)`, `SeasonData(season)`.
- Produces: every entrypoint accepting `--season`; `EXPORTS_PATH = "files/exports"` in settings.

- [ ] **Step 1: Add the exports path to settings**

In `src/brasileirao_simulator/config/settings.py`, add below `QUERIES_PATH`:

```python
EXPORTS_PATH = "files/exports"
```

Leave `DATES` and `BACKFILL_DATES` in place for now; Task 6 removes them once nothing imports them.

- [ ] **Step 2: Give `current_probabilities.py` a `--season` flag**

Replace the file:

```python
import argparse

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService


def current_probabilities(season: int, iterations: int = 100, date: str = None) -> None:
    params = SimulationParams(
        season=season,
        iterations=iterations,
        max_batch_size=25,
        ignore_results_after=date,
        load_results=False,
    )
    simulation_service = SimulationService(
        persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season),
        simulator_adapter=PoissonSameVenueAverageAdapter(params.strategy, season),
        params=params,
    )
    simulation_service.run_simulation()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate a season's remaining fixtures.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--date", default=None, help="simulate as of this date (YYYY-MM-DD)")
    args = parser.parse_args()

    current_probabilities(args.season, args.iterations, args.date)
```

- [ ] **Step 3: Rewrite `backfill.py` to slice dates by range**

Replace the file:

```python
"""Replay a season day by day, simulating as of each date.

Dates come from the season's own dates.json. --from-date/--to-date select a
slice, replacing the old habit of commenting entries out of a settings list.
"""

import argparse

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService


def backfill_dates(season: int, from_date: str = None, to_date: str = None) -> list[str]:
    dates = SeasonData(season).dates
    if from_date:
        dates = [d for d in dates if d >= from_date]
    if to_date:
        dates = [d for d in dates if d <= to_date]
    return dates


def backfill(season: int, date: str, iterations: int = 200) -> None:
    params = SimulationParams(
        season=season,
        iterations=iterations,
        max_batch_size=100,
        ignore_results_after=date,
        load_results=True,
    )
    simulation_service = SimulationService(
        persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season),
        simulator_adapter=PoissonSameVenueAverageAdapter(params.strategy, season),
        params=params,
    )
    simulation_service.run_simulation(print_results=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()

    for date in backfill_dates(args.season, args.from_date, args.to_date):
        print(f"backfilling {args.season} as of {date}")
        backfill(args.season, date, args.iterations)
```

- [ ] **Step 4: Guard the transformation service against missing pickles**

In `src/brasileirao_simulator/service_layer/data_transformation_service.py`, each of the four `*_pkl_to_rows` methods calls `.items()` on a result that is `None` when the file is absent. Replace the four loop bodies with this shape (shown for `results_pkl_to_rows`; apply the same guard to `relegation_points_pkl_to_rows`, `positions_pkl_to_rows`, and `matches_pkl_to_rows`, keeping each one's own `_create_rows_*` call):

```python
    def results_pkl_to_rows(self, suffix_list: list = []) -> list:
        pivot_results = []
        for suffix in suffix_list:
            results = self.persistence_adapter.load_results(self.strategy, suffix=suffix)
            if results is None:
                # A date that was never simulated is a gap, not a failure.
                continue
            pivot_results.extend(self._create_rows_results(results, suffix))
        return pivot_results
```

- [ ] **Step 5: Make the three export entrypoints season-aware**

Replace `src/brasileirao_simulator/entrypoints/results_history_dataset.py`:

```python
import argparse
import os

import pandas as pd

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH, RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.service_layer.data_transformation_service import (
    DataTransformationService,
)


def results_history_dataset(season: int):
    dates = SeasonData(season).dates
    service = DataTransformationService(
        strategy="average", persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season)
    )
    return (
        service.results_pkl_to_rows(dates),
        service.relegation_points_pkl_to_rows(dates),
        service.positions_pkl_to_rows(dates),
    )


def dump_rows_to_file(rows, season: int, file_name: str) -> None:
    export_dir = f"{EXPORTS_PATH}/{season}"
    os.makedirs(export_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(f"{export_dir}/{file_name}", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a season's simulation history.")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    results, relegation_points, positions = results_history_dataset(args.season)
    dump_rows_to_file(results, args.season, "results_pivot.csv")
    dump_rows_to_file(relegation_points, args.season, "relegation_points.csv")
    dump_rows_to_file(positions, args.season, "positions.csv")
```

Replace `src/brasileirao_simulator/entrypoints/matches_history_dataset.py`:

```python
import argparse
import os

import pandas as pd

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH, RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.service_layer.data_transformation_service import (
    DataTransformationService,
)


def matches_history_dataset(season: int):
    service = DataTransformationService(
        strategy="average", persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season)
    )
    return service.matches_pkl_to_rows(SeasonData(season).dates)


def dump_rows_to_file(rows, season: int) -> None:
    export_dir = f"{EXPORTS_PATH}/{season}"
    os.makedirs(export_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(f"{export_dir}/matches_results_pivot.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a season's simulated match odds.")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    dump_rows_to_file(matches_history_dataset(args.season), args.season)
```

Replace `src/brasileirao_simulator/entrypoints/positions_history_dataset.py`:

```python
import argparse
import os

import pandas as pd

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH, RESULTS_DIRECTORY
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.service_layer.data_transformation_service import (
    DataTransformationService,
)


def positions_history_dataset(season: int):
    service = DataTransformationService(
        strategy="average", persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season)
    )
    return service.positions_pkl_to_rows(SeasonData(season).dates)


def dump_rows_to_file(rows, season: int) -> None:
    export_dir = f"{EXPORTS_PATH}/{season}"
    os.makedirs(export_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(f"{export_dir}/positions_pivot.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export a season's simulated positions.")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    dump_rows_to_file(positions_history_dataset(args.season), args.season)
```

- [ ] **Step 6: Pass the season through Docker**

Replace `entrypoint.sh`:

```bash
#!/bin/bash
set -euo pipefail

SEASON="${SEASON:-2026}"

python3 brasileirao_simulator/entrypoints/backfill.py --season "$SEASON";
python3 brasileirao_simulator/entrypoints/results_history_dataset.py --season "$SEASON";
python3 brasileirao_simulator/entrypoints/matches_history_dataset.py --season "$SEASON";
```

In `docker-compose.yml`, add to the `app` service's `environment` block:

```yaml
      - SEASON=${SEASON:-2026}
```

- [ ] **Step 7: Verify an entrypoint runs end to end for 2026**

Run:
```bash
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/current_probabilities.py --season 2026 --iterations 2 --date 2026-03-01
```
Expected: prints batch progress, exits 0, and writes `src/files/pkl/2026/average_results.pkl`.

- [ ] **Step 8: Verify the 2025 results are untouched**

Run:
```bash
cd src/files && ls pkl/2025 | wc -l
```
Expected: 108, unchanged.

- [ ] **Step 9: Add the end-to-end probability test**

The tests so far check a single simulated table. This one runs the whole service
and checks the aggregate probabilities it exists to produce.

Create `tests/test_simulation_service.py`:

```python
"""End to end: the service turns a season plus an as-of date into probabilities."""

import numpy as np

from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.service_layer.simulation_service import SimulationService


ITERATIONS = 4


def _run(season: int, as_of: str, tmp_path) -> dict:
    np.random.seed(42)
    params = SimulationParams(
        season=season,
        iterations=ITERATIONS,
        max_batch_size=ITERATIONS,
        ignore_results_after=as_of,
        load_results=False,
    )
    persistence = PickleAdapter(str(tmp_path), season)
    SimulationService(
        persistence_adapter=persistence,
        simulator_adapter=PoissonSameVenueAverageAdapter(params.strategy, season),
        params=params,
    ).run_simulation()

    return persistence.load_results(params.strategy, suffix=as_of)


def test_2026_title_probabilities_sum_to_one_hundred_percent(tmp_path):
    results = _run(2026, "2026-03-01", tmp_path)
    titles = results["brasileirao_title"]

    assert sum(titles.values()) == ITERATIONS, "every iteration must crown one champion"
    assert all(team for team in titles), "a blank team name means the season filter dropped rows"


def test_2026_relegation_has_four_teams_per_iteration(tmp_path):
    """Positions 17 through 20 go down, so four teams are relegated per run."""
    results = _run(2026, "2026-03-01", tmp_path)

    assert sum(results["brasileirao_relegation"].values()) == ITERATIONS * 4


def test_2025_still_produces_probabilities(tmp_path):
    """The same guarantee for the season that already worked."""
    results = _run(2025, "2025-08-31", tmp_path)

    assert sum(results["brasileirao_title"].values()) == ITERATIONS
```

- [ ] **Step 10: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add src/brasileirao_simulator/config/settings.py \
        src/brasileirao_simulator/entrypoints/ \
        src/brasileirao_simulator/service_layer/data_transformation_service.py \
        entrypoint.sh docker-compose.yml tests/test_simulation_service.py
git commit -m "feat: accept --season in entrypoints and scope exports by season"
```

---

### Task 6: Remove the superseded paths

Everything now reads the season-scoped layout, so the old flat inputs and the module-level globals can go. Doing this last means every prior commit left a working tree.

**Files:**
- Delete: `src/brasileirao_simulator/domain/datasets.py`
- Delete: `src/brasileirao_simulator/entrypoints/backfill2.py`
- Modify: `src/brasileirao_simulator/entrypoints/inspect_dataset.py`
- Modify: `src/brasileirao_simulator/config/settings.py`
- Delete (data): `src/files/datasets/fixtures_2025.csv`, `punters.json`, `doubles.json`
- Modify: `README.md`

- [ ] **Step 1: Confirm nothing still imports the old module**

Run:
```bash
grep -rn "domain.datasets\|from brasileirao_simulator.domain import datasets\|BACKFILL_DATES\|settings import.*DATES" src/ tests/
```
Expected: only `inspect_dataset.py`. If anything else appears, update it before continuing.

- [ ] **Step 2: Point `inspect_dataset.py` at `SeasonData`**

The `punters`, `doubles`, and `fixtures` imports are never referenced in this
file, and neither is its `cols` list (it names a `home_punter` column that
nothing produces). Dropping them is the whole migration.

Replace `src/brasileirao_simulator/entrypoints/inspect_dataset.py`:

```python
"""Dump a season's intermediate tables to CSV for eyeballing."""

import argparse
import os

import pandas as pd

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.config.settings import EXPORTS_PATH
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


def inspect_dataset(season: int) -> None:
    tables = Tables(SeasonData(season))
    enriched_tidy_fixtures = tables.enriched_tidy_fixtures()

    adapter = PoissonSameVenueAverageAdapter("average", season)
    standings = adapter.get_brasileirao_standings(enriched_tidy_fixtures)
    team_params = adapter.get_team_params(enriched_tidy_fixtures)

    pd.options.display.max_columns = 20
    pd.options.display.max_rows = 50
    pd.set_option("display.width", 1000)

    export_dir = f"{EXPORTS_PATH}/{season}"
    os.makedirs(export_dir, exist_ok=True)
    enriched_tidy_fixtures.to_csv(f"{export_dir}/out_enriched_tidy_fixtures.csv", index=False)
    standings.to_csv(f"{export_dir}/out_standings.csv", index=False)
    team_params.to_csv(f"{export_dir}/out_team_params.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    inspect_dataset(args.season)
```

Note this also fixes two latent bugs: `Tables()` was constructed twice and
`remaining_games` was assigned as a bound method rather than called.

- [ ] **Step 3: Delete the superseded modules and data**

```bash
git rm src/brasileirao_simulator/domain/datasets.py
git rm src/brasileirao_simulator/entrypoints/backfill2.py
git rm src/files/datasets/fixtures_2025.csv src/files/datasets/fixtures_2024.csv \
       src/files/datasets/punters.json src/files/datasets/doubles.json
```

Both flat fixtures files go here, not at Task 2, because `datasets.py` read them
until this task removed it.

- [ ] **Step 4: Remove the date lists from settings**

In `src/brasileirao_simulator/config/settings.py`, delete the `BACKFILL_DATES` and `DATES` lists entirely, leaving only the four path constants. The dates now live in each season's `dates.json`.

- [ ] **Step 5: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: all pass. A failure here means something still depended on a deleted path.

- [ ] **Step 6: Update the README**

Replace `README.md` with instructions matching the new interface:

```markdown
# brasileirao-simulator

Monte Carlo simulation of the Brasileirão. Remaining fixtures are simulated
with a Poisson model built from each team's recent scoring and conceding
averages, over many iterations, to produce title and relegation probabilities.

## Running

```
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/current_probabilities.py --season 2026
```

Simulate as of a past date with `--date 2026-03-01`, and set the iteration
count with `--iterations`.

Replay a whole season day by day:

```
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/backfill.py --season 2026 --from-date 2026-01-28
```

`make all` runs the full pipeline for the season in `$SEASON` (default 2026):

```
SEASON=2025 make all
```

Results are pickled under `src/files/pkl/{season}/` and CSV exports land in
`src/files/exports/{season}/`.

## Adding a season

1. Create `src/files/datasets/{season}/` and copy that season's fixtures in as
   `fixtures.csv`. The source is the `lean-pype` pipeline's
   `processed_fixtures_{season}_71.csv` (league 71 is Serie A).
2. Generate the date list:
   `docker-compose run --rm app python3 brasileirao_simulator/entrypoints/generate_season_dates.py --season {season}`
3. Run with `--season {season}`.

The previous season's folder must exist: early-season simulations reach back
into it for scoring averages, since a team has not yet played enough games in
the new season to fill the lookback window.

## Tests

```
docker-compose run --rm app pytest /tests -v
```
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove flat season files and settings date lists"
```

---

### Task 7: Verify 2026 end to end

**Files:** none modified — this task is verification.

- [ ] **Step 1: Run a real 2026 simulation**

Run:
```bash
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/current_probabilities.py --season 2026 --iterations 100
```
Expected: exits 0 and writes `src/files/pkl/2026/average_results.pkl`.

- [ ] **Step 2: Print the resulting probabilities**

Run:
```bash
docker-compose run --rm app python3 -c "
import pickle
results = pickle.load(open('files/pkl/2026/average_results.pkl','rb'))
total = sum(results['brasileirao_title'].values())
print('iterations:', total)
for team, count in sorted(results['brasileirao_title'].items(), key=lambda kv: -kv[1])[:5]:
    print(f'{team:24s} {100*count/total:5.1f}%')
"
```
Expected: 100 iterations, twenty-ish teams represented across title and relegation, percentages that sum to 100.

- [ ] **Step 3: Confirm 2025 still runs identically**

Run:
```bash
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/current_probabilities.py --season 2025 --iterations 10 --date 2025-08-31
docker-compose run --rm app pytest /tests -v
```
Expected: both succeed; the 2025 regression tests written in Task 1 still pass with their original assertions.

- [ ] **Step 4: Confirm the working tree is clean apart from new results**

Run: `git status --short`
Expected: only new pickle files under `src/files/pkl/2026/`, plus the pre-existing untracked `.DS_Store`, `all.csv`, and `leagues.json`. No modified source files.

---

## Notes for the executor

- **The 2025 regression tests from Task 1 are the contract.** If a later task makes them fail, the refactor changed behavior. Fix the code, not the test — the only sanctioned edit to that file is Step 11 of Task 3, which changes construction calls and leaves every assertion alone.
- **An empty DataFrame is the failure mode to watch for.** A season mismatch between `int` and `str`, or a frame that was never registered, produces zero rows rather than an exception. Whenever a test fails on a count or a length, check those two things first.
- **`con.register` overwrites by name.** `Tables.enriched_tidy_fixtures` re-registers `tidy_fixtures` on every call, which is deliberate: each call may blank at a different date.

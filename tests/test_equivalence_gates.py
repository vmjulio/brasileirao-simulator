"""T2.2 - equivalence and prediction-set gates.

WHY THIS EXISTS. `MatchStore` (domain/match_store.py, T2.1) reads every shard
under `src/files/datasets/competitions/` and nothing on the prediction side
consumes it yet - "other competitions never enter the SQL" is decision 1 of
docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md. This
module is the proof, not an assumption: `MatchStore`'s mere existence, and the
`competitions/` directory's mere presence on disk, must change nothing about
what `Tables`/`build_baseline` compute today.

Four gates:
  1. `remaining_games`'s fixture-id set is unchanged, whether or not a
     `MatchStore` has been constructed, and whether or not the fixtures are
     read from a datasets root that has a `competitions/` directory at all.
  2. The set of DuckDB relation names registered on `Tables`' own connection
     is unchanged by the same two conditions - asserted by listing
     registrations (`information_schema.tables`), never by reading the SQL.
  3. Every lambda `build_baseline` produces is `np.array_equal` (bit-identical,
     not merely close) whether the datasets root has a `competitions/`
     directory or not.
  4. A deliberate break: register a bogus relation on a `Tables` connection
     and confirm gate 2's own assertion catches it, then confirm removing it
     restores a pass - proof the gate actually bites, not just that it never
     fires.

HOW "the competitions/ directory is present" IS VARIED. Neither `SeasonData`
nor `Tables` reads `domain.match_store` or `config.settings.DATASETS_PATH` at
call time - `SeasonData.__init__`'s `datasets_path` parameter is the only
thing that can point it elsewhere, and it already exists on this branch
(test_season_data.py's `test_missing_previous_season_names_the_path` uses the
same parameter). `_datasets_root_without_competitions` below builds a temp
root holding only the season/prior-season files `SeasonData` reads
(`fixtures.csv`, `dates.json`, and the optional `punters.json`/`doubles.json`)
- with no `competitions/` subdirectory anywhere under it - copied from the
real root. Passing that temp root to `SeasonData` is "the competitions
directory hidden" without touching `match_store.py`, `competitions.py`, or
any SQL.
"""

import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.config.settings import DATASETS_PATH
from brasileirao_simulator.domain.batch_simulation import build_baseline
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables

MID_SEASON_DATE = {2025: "2025-06-01", 2026: "2026-06-01"}

_SEASON_FILES = ("fixtures.csv", "dates.json", "punters.json", "doubles.json")


def _copy_season_files(season: int, dest_root: Path) -> None:
    src_dir = Path(DATASETS_PATH) / str(season)
    dest_dir = dest_root / str(season)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in _SEASON_FILES:
        src = src_dir / name
        if src.is_file():
            shutil.copy(src, dest_dir / name)


def _datasets_root_without_competitions(tmp_path: Path, season: int) -> str:
    """A datasets root holding only `season` and `season - 1`'s files - no
    `competitions/` subdirectory anywhere under it - copied from the real
    root `Tables`/`SeasonData` read from everywhere else in this test suite.
    """
    root = tmp_path / "datasets_no_competitions"
    _copy_season_files(season, root)
    _copy_season_files(season - 1, root)
    assert not (root / "competitions").exists()
    return str(root)


def _remaining_fixture_ids(season: int, as_of_date: str, datasets_path: str = None) -> set:
    season_data = (
        SeasonData(season, datasets_path=datasets_path) if datasets_path else SeasonData(season)
    )
    tables = Tables(season_data)
    remaining = tables.remaining_games(blank_from_date=as_of_date)
    return set(remaining["fixture_id"])


def _relation_names(con: duckdb.DuckDBPyConnection) -> set:
    return set(con.sql("select table_name from information_schema.tables").df()["table_name"])


def _build_tables_and_remaining(season: int, as_of_date: str, datasets_path: str = None):
    season_data = (
        SeasonData(season, datasets_path=datasets_path) if datasets_path else SeasonData(season)
    )
    tables = Tables(season_data)
    remaining = tables.remaining_games(blank_from_date=as_of_date)
    return tables, remaining


def _baseline_lambdas_by_fixture(season: int, as_of_date: str, datasets_path: str = None):
    """The same sequence of public calls `variant_sweep.analytic_forecasts_for_date`
    makes (Tables -> enriched_tidy_fixtures/remaining_games -> team_params query
    -> build_baseline), reproduced here as a direct call so the raw lambda
    arrays - not the probabilities derived from them - are available to
    compare with `np.array_equal`.

    Returned indexed by `fixture_id` and sorted on it, not left in
    `build_baseline`'s own row order: `remaining_games`/`enriched_tidy_fixtures`
    read from a `.sql` query with no `ORDER BY` on ties, and DuckDB does not
    promise a stable row order run to run even against the exact same input
    (confirmed empirically - two back-to-back calls against the identical,
    real datasets root can already return `lam_home`/`lam_away` permuted
    against each other before this ticket's change enters the picture at
    all). That pre-existing non-determinism is orthogonal to what this gate
    checks and would make a positional `np.array_equal` flaky regardless of
    `MatchStore`; indexing by `fixture_id` - the one thing that does not move
    - is what makes "every lambda is bit-identical" the fixture-keyed claim
    the ticket means, not an accident of row order.
    """
    season_data = (
        SeasonData(season, datasets_path=datasets_path) if datasets_path else SeasonData(season)
    )
    tables = Tables(season_data)
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    remaining_games = tables.remaining_games(blank_from_date=as_of_date)

    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    team_params = con.sql(Queries(season).team_params_same_venue_average()).df()

    baseline = build_baseline(fixtures, remaining_games, team_params, season)
    lam_home = pd.Series(baseline.lam_home, index=baseline.fixture_id).sort_index()
    lam_away = pd.Series(baseline.lam_away, index=baseline.fixture_id).sort_index()
    return lam_home, lam_away


# ---------------------------------------------------------------------------
# Gate 1 - remaining_games' fixture-id set.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("season", [2025, 2026])
def test_remaining_games_fixture_ids_unaffected_by_match_store_construction(season):
    """Constructing a `MatchStore` touches no shared state `Tables`/`SeasonData`
    could see - it never registers anything on a DuckDB connection and never
    imports duckdb at all (see domain/match_store.py). This pins that down
    directly rather than leaving it as an assumption about the class's
    implementation: build a `MatchStore` from the real `competitions/` tree,
    exercise its public surface, then confirm `remaining_games`'s fixture-id
    set is identical to what it was before the store existed.
    """
    as_of_date = MID_SEASON_DATE[season]

    before = _remaining_fixture_ids(season, as_of_date)

    store = MatchStore()
    store.matches
    store.before(as_of_date)
    store.coverage(season)

    after = _remaining_fixture_ids(season, as_of_date)

    assert before == after
    assert len(before) > 0


@pytest.mark.parametrize("season", [2025, 2026])
def test_remaining_games_fixture_ids_unaffected_by_competitions_directory_absence(season, tmp_path):
    """Same fixture-id set whether `SeasonData` reads from the real root
    (which has a `competitions/` directory) or a temp root built to have no
    `competitions/` directory anywhere under it."""
    as_of_date = MID_SEASON_DATE[season]
    hidden_root = _datasets_root_without_competitions(tmp_path, season)

    with_directory = _remaining_fixture_ids(season, as_of_date)
    without_directory = _remaining_fixture_ids(season, as_of_date, datasets_path=hidden_root)

    assert with_directory == without_directory
    assert len(with_directory) > 0


# ---------------------------------------------------------------------------
# Gate 2 - the set of DuckDB relations Tables registers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("season", [2025, 2026])
def test_relation_set_unaffected_by_match_store_construction(season):
    as_of_date = MID_SEASON_DATE[season]

    tables_before, _ = _build_tables_and_remaining(season, as_of_date)
    relations_before = _relation_names(tables_before.con)

    store = MatchStore()
    store.matches
    store.before(as_of_date)

    tables_after, _ = _build_tables_and_remaining(season, as_of_date)
    relations_after = _relation_names(tables_after.con)

    assert relations_before == relations_after
    assert len(relations_before) > 0


@pytest.mark.parametrize("season", [2025, 2026])
def test_relation_set_unaffected_by_competitions_directory_absence(season, tmp_path):
    as_of_date = MID_SEASON_DATE[season]
    hidden_root = _datasets_root_without_competitions(tmp_path, season)

    tables_with, _ = _build_tables_and_remaining(season, as_of_date)
    relations_with = _relation_names(tables_with.con)

    tables_without, _ = _build_tables_and_remaining(season, as_of_date, datasets_path=hidden_root)
    relations_without = _relation_names(tables_without.con)

    assert relations_with == relations_without


# ---------------------------------------------------------------------------
# Gate 3 - build_baseline's lambdas, bit-identical.
# ---------------------------------------------------------------------------


def test_build_baseline_lambdas_bit_identical_with_competitions_directory_hidden(tmp_path):
    season, as_of_date = 2025, MID_SEASON_DATE[2025]
    hidden_root = _datasets_root_without_competitions(tmp_path, season)

    lam_home_with, lam_away_with = _baseline_lambdas_by_fixture(season, as_of_date)
    lam_home_without, lam_away_without = _baseline_lambdas_by_fixture(
        season, as_of_date, datasets_path=hidden_root
    )

    assert len(lam_home_with) > 0
    assert list(lam_home_with.index) == list(lam_home_without.index)
    assert np.array_equal(lam_home_with.to_numpy(), lam_home_without.to_numpy())
    assert np.array_equal(lam_away_with.to_numpy(), lam_away_without.to_numpy())


# ---------------------------------------------------------------------------
# Gate 4 - deliberate break: prove gate 2's assertion actually bites.
# ---------------------------------------------------------------------------


def test_deliberate_break_bogus_relation_is_caught_then_clears(tmp_path):
    """Registers a bogus relation on one Tables connection and confirms the
    gate-2-style equality check fails while it is there, then confirms
    unregistering it restores a pass - proof the relation-set assertion is
    actually sensitive to an unexpected relation, not vacuously true."""
    season, as_of_date = 2025, MID_SEASON_DATE[2025]

    reference_tables, _ = _build_tables_and_remaining(season, as_of_date)
    reference_relations = _relation_names(reference_tables.con)

    tables, _ = _build_tables_and_remaining(season, as_of_date)
    clean_relations = _relation_names(tables.con)
    assert clean_relations == reference_relations

    tables.con.register("bogus_relation", MatchStore().matches)
    broken_relations = _relation_names(tables.con)
    assert broken_relations != reference_relations
    assert "bogus_relation" in broken_relations

    tables.con.unregister("bogus_relation")
    restored_relations = _relation_names(tables.con)
    assert restored_relations == reference_relations

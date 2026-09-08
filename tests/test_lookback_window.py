"""The $lookback parameter on team_params_same_venue_average.sql.

See docs/superpowers/specs/2026-09-08-lookback-sweep-design.md: the query
used the literal 19 for two different jobs - the window size (`rn <= 19`)
and the shrinkage-blend denominator (`19 as data_points`). Both had to move
together via one $lookback substitution, or a window sweep would confound
window size with shrinkage strength.

test_default_lookback_renders_byte_identical_sql is the most important test
in this file: the whole batch-equivalence guarantee (every existing caller
of Queries, none of which passes a lookback) rests on the default
reproducing today's query exactly.
"""

import re

import duckdb

from brasileirao_simulator.config.settings import QUERIES_PATH
from brasileirao_simulator.domain.batch_simulation import FULL_WINDOW_MATCHES
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


def _rendered_by_hand(season: int) -> str:
    """What the query rendered before $lookback existed: $season substituted,
    every $lookback replaced by the literal 19 it used to be.

    Built by reading the raw file directly rather than going through Queries,
    so this fixture cannot agree with Queries merely because both share the
    same (possibly wrong) substitution code - it is independent ground truth
    for the equivalence gate below.
    """
    with open(f"{QUERIES_PATH}/team_params_same_venue_average.sql") as f:
        raw = f.read()
    return raw.replace("$season", str(season)).replace("$lookback", "19")


def test_default_lookback_renders_byte_identical_sql():
    """THE gate. Queries(season) with no lookback argument must render
    byte-for-byte what today's file rendered. If this fails, nothing else in
    the sweep - or in any existing caller of Queries - can be trusted."""
    season = 2025
    sql = Queries(season).team_params_same_venue_average()

    assert sql == _rendered_by_hand(season)


def test_default_lookback_is_the_shared_full_window_constant():
    assert Queries(2025).lookback == FULL_WINDOW_MATCHES == 19


def _goals_for_average_by_team_venue(season: int, lookback: int):
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures()
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    df = con.sql(Queries(season, lookback=lookback).team_params_same_venue_average()).df()
    return df.set_index(["team_name", "venue"])["goals_for_average"]


def test_lookback_changes_team_parameters_on_real_data():
    """A knob that does nothing would make the sweep's result indistinguishable
    from a genuine ceiling - this is the check that it actually bites."""
    short = _goals_for_average_by_team_venue(2025, lookback=8)
    full = _goals_for_average_by_team_venue(2025, lookback=FULL_WINDOW_MATCHES)

    common = short.index.intersection(full.index)
    assert len(common) > 0, "no shared (team, venue) rows to compare"
    assert not short.loc[common].equals(full.loc[common]), (
        "lookback=8 produced identical team parameters to lookback=19; "
        "the $lookback substitution is not taking effect"
    )


def test_no_bare_19_survives_rendering():
    """Both jobs - window size and blend denominator - must move together.
    A bare 19 surviving a non-default lookback would mean one occurrence
    silently stayed fixed while the other moved, confounding window size
    with shrinkage strength (see the design doc's confound section)."""
    sql = Queries(2025, lookback=8).team_params_same_venue_average()

    assert not re.search(r"\b19\b", sql)

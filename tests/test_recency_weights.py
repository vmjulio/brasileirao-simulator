"""The $weight_recent/$weight_mid/$weight_base parameters on
team_params_same_venue_average.sql.

The query weighted matches inside the lookback window by recency: most
recent match counted 4, the next four counted 3, the rest counted 1 - 53% of
the estimate on the last 5 matches. See entrypoints/variant_sweep.py's
DEFAULT_RECENCY_PROFILES for the swept range (flat 1/1/1 through very_steep
16/6/1).

test_default_weights_render_byte_identical_sql is this file's version of
test_lookback_window.py's central gate: unlike prior_weight (see
test_prior_weight.py), this parameterisation is a pure literal swap - no
structural change to the formula - so byte-identical text is achievable and
is the strongest form of the equivalence guarantee.
"""

import duckdb

from brasileirao_simulator.config.settings import QUERIES_PATH
from brasileirao_simulator.domain.queries import (
    DEFAULT_WEIGHT_BASE,
    DEFAULT_WEIGHT_MID,
    DEFAULT_WEIGHT_RECENT,
    Queries,
)
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


def _rendered_by_hand(season: int) -> str:
    """Every placeholder in the file replaced by the literal it defaults to
    (season substituted, lookback->19, weight_recent/mid/base->4/3/1,
    prior_weight->1.0) - independent ground truth built from the raw file,
    not from Queries' own substitution code. See test_lookback_window.py's
    twin of this helper for why it stays independent."""
    with open(f"{QUERIES_PATH}/team_params_same_venue_average.sql") as f:
        raw = f.read()
    return (
        raw.replace("$season", str(season))
        .replace("$lookback", "19")
        .replace("$weight_recent", "4")
        .replace("$weight_mid", "3")
        .replace("$weight_base", "1")
        .replace("$prior_weight", "1.0")
    )


def test_default_weights_render_byte_identical_sql():
    """THE gate for this sweep. Queries(season) with no weight overrides must
    render byte-for-byte what today's file renders at its defaults - if this
    fails, the recency-weight sweep is not measuring the constant that ships."""
    season = 2025
    sql = Queries(season).team_params_same_venue_average()

    assert sql == _rendered_by_hand(season)


def test_defaults_are_4_3_1():
    assert (DEFAULT_WEIGHT_RECENT, DEFAULT_WEIGHT_MID, DEFAULT_WEIGHT_BASE) == (4, 3, 1)


def _goals_for_average_by_team_venue(season: int, weights: tuple):
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures()
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    queries = Queries(season, weight_recent=weights[0], weight_mid=weights[1], weight_base=weights[2])
    df = con.sql(queries.team_params_same_venue_average()).df()
    return df.set_index(["team_name", "venue"])["goals_for_average"]


def test_flat_weights_change_team_parameters_on_real_data():
    """A knob that does nothing would make the sweep's result indistinguishable
    from a genuine ceiling - this is the check that it actually bites."""
    flat = _goals_for_average_by_team_venue(2025, (1, 1, 1))
    current = _goals_for_average_by_team_venue(2025, (4, 3, 1))

    common = flat.index.intersection(current.index)
    assert len(common) > 0, "no shared (team, venue) rows to compare"
    assert not flat.loc[common].equals(current.loc[common]), (
        "weights=(1,1,1) produced identical team parameters to (4,3,1); "
        "the recency-weight substitution is not taking effect"
    )


def test_very_steep_weights_change_team_parameters_on_real_data():
    steep = _goals_for_average_by_team_venue(2025, (16, 6, 1))
    current = _goals_for_average_by_team_venue(2025, (4, 3, 1))

    common = steep.index.intersection(current.index)
    assert len(common) > 0
    assert not steep.loc[common].equals(current.loc[common])

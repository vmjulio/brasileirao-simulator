"""How many real matches back each team's parameters.

team_params_same_venue_average.sql reports data_points as
`greatest(t.data_points, 19)`, which is always 19 - so it cannot tell a
promoted side with 12 matches from an established one with 19. Parameter
uncertainty needs that distinction, hence a separate query.
"""

import duckdb

from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


PROMOTED_2026 = {"Atletico Paranaense", "Chapecoense-sc", "Coritiba", "Remo"}


def _counts(season: int, as_of: str = None):
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    return con.sql(Queries(season).team_match_counts()).df()


def test_every_team_has_a_row_per_venue():
    counts = _counts(2026)

    assert len(counts) == 40
    assert set(counts["venue"]) == {"home", "away"}


def test_promoted_sides_have_fewer_matches_than_established_ones():
    """The distinction the existing query cannot express."""
    counts = _counts(2026)
    promoted = counts[counts["team_name"].isin(PROMOTED_2026)]["match_count"]
    established = counts[~counts["team_name"].isin(PROMOTED_2026)]["match_count"]

    assert promoted.max() < 19, "promoted sides should not have a full window"
    assert (established == 19).all(), "established sides span into the previous season"


def test_the_count_never_exceeds_the_window():
    """The lookback is 19 per venue; more matches do not widen it."""
    counts = _counts(2026)

    assert counts["match_count"].max() <= 19


def test_counts_shrink_at_an_earlier_as_of_date():
    """Fewer matches played means less evidence, which is the whole point."""
    early = _counts(2026, as_of="2026-03-01")["match_count"].sum()
    late = _counts(2026, as_of="2026-09-05")["match_count"].sum()

    assert early < late

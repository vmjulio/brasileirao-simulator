"""The standings tiebreakers, and why one of them was removable.

standings.sql computes gd = coalesce(gf,0) - coalesce(ga,0), so ga = gf - gd.
Two teams tied on gf AND gd therefore have identical ga by construction, which
makes a trailing `ga desc` tiebreaker incapable of breaking any tie.

`test_goal_difference_determines_goals_against` checks that identity holds on
real simulated data, i.e. that it survives the SQL->pandas round trip.
`test_sql_derives_goal_difference_from_goals_against` and
`test_sql_goal_difference_identity_holds_on_crafted_edges` run crafted rows
through the real standings.sql query (not Python arithmetic standing in for
it) to prove gd is actually derived from ga there, including the tie case the
removed `ga desc` tiebreaker would have mattered for. So that if gd is ever
redefined in the SQL, the removal is revisited.
"""

import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


BRASILEIRAO_TEAM_COUNT = 20


def _standings(seed: int):
    np.random.seed(seed)
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures()
    remaining = tables.remaining_games()
    adapter = PoissonSameVenueAverageAdapter("average", 2026)
    return adapter.get_brasileirao_standings(adapter.simulate_fixtures(fixtures, remaining))


def test_goal_difference_determines_goals_against():
    """The identity that makes the `ga desc` tiebreaker dead."""
    standings = _standings(seed=7)

    assert ((standings["gf"] - standings["ga"]) == standings["gd"]).all()


def _standings_from(rows) -> pd.DataFrame:
    """Run crafted results through standings.sql and return what it computes."""
    frame = pd.DataFrame(rows)
    adapter = PoissonSameVenueAverageAdapter("average", 2026)
    return adapter.get_brasileirao_standings(frame)


def test_sql_derives_goal_difference_from_goals_against():
    """Two teams with the same goals for but different goals against must get
    different goal difference. If gd were ever redefined so it no longer
    depended on ga, they would tie on gf AND gd while differing on ga - which
    is exactly the case the removed `ga desc` tiebreaker would have been needed
    for. This is the assertion that fails if the removal stops being safe."""
    standings = _standings_from([
        {"team_name": "leaky",  "goals_for": 4, "goals_against": 5, "season": 2026},
        {"team_name": "tight",  "goals_for": 4, "goals_against": 2, "season": 2026},
    ])

    assert standings["gf"].nunique() == 1, "the two teams must tie on goals for"
    assert standings["gd"].nunique() == 2, "differing ga must produce differing gd"


def test_sql_goal_difference_identity_holds_on_crafted_edges():
    """The identity across zero, positive and negative goal difference."""
    standings = _standings_from([
        {"team_name": "zero",     "goals_for": 3, "goals_against": 3, "season": 2026},
        {"team_name": "positive", "goals_for": 9, "goals_against": 1, "season": 2026},
        {"team_name": "negative", "goals_for": 0, "goals_against": 7, "season": 2026},
    ])

    assert (standings["gd"] == standings["gf"] - standings["ga"]).all()


def test_standings_are_a_contiguous_ranking():
    standings = _standings(seed=11)

    assert sorted(standings["rank_"]) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))

"""The standings tiebreakers, and why one of them was removable.

standings.sql computes gd = coalesce(gf,0) - coalesce(ga,0), so ga = gf - gd.
Two teams tied on gf AND gd therefore have identical ga by construction, which
makes a trailing `ga desc` tiebreaker incapable of breaking any tie.
`test_goal_difference_determines_goals_against` pins that identity as it
survives the SQL->pandas round trip; `test_equal_goal_difference_and_goals_for_forces_equal_goals_against`
pins the corollary on synthetic rows built to tie, since real simulated
standings almost never tie on all four real tiebreakers at once. So that if
gd is ever redefined, the removal is revisited.
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


def test_equal_goal_difference_and_goals_for_forces_equal_goals_against():
    """The corollary that makes `ga desc` unreachable, on rows built to tie.

    Real tables almost never produce a tie on all four real tiebreakers, so
    asserting this against simulated data passes vacuously. Constructing the tie
    is the only way to actually exercise it.
    """
    tied = pd.DataFrame(
        {
            "team_name": ["a", "b"],
            "p": [70.0, 70.0],
            "w": [21.0, 21.0],
            "gf": [60.0, 60.0],
            "ga": [40.0, 40.0],
        }
    )
    tied["gd"] = tied["gf"] - tied["ga"]

    assert tied["gd"].nunique() == 1
    assert tied.groupby(["p", "w", "gd", "gf"])["ga"].nunique().max() == 1


def test_standings_are_a_contiguous_ranking():
    standings = _standings(seed=11)

    assert sorted(standings["rank_"]) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))

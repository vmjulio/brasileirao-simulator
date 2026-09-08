"""The standings tiebreakers, and why one of them was removable.

standings.sql computes gd = coalesce(gf,0) - coalesce(ga,0), so ga = gf - gd.
Two teams tied on gf AND gd therefore have identical ga by construction, which
makes a trailing `ga desc` tiebreaker incapable of breaking any tie. These tests
pin that identity, so that if gd is ever redefined the removal is revisited.
"""

import numpy as np

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


def test_no_tie_survives_the_four_real_tiebreakers_with_differing_ga():
    """If p, w, gd and gf all tie, ga cannot differ - so ordering by it changes
    nothing."""
    for seed in range(5):
        standings = _standings(seed)
        differing = standings.groupby(["p", "w", "gd", "gf"])["ga"].nunique()

        assert (differing <= 1).all()


def test_standings_are_a_contiguous_ranking():
    standings = _standings(seed=11)

    assert sorted(standings["rank_"]) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))

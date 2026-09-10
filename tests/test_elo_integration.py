"""E4 integration: replay, division seeds and snapshots together on the real
`MatchStore`. The three tickets were built in parallel against stubs; this is
the first place they meet.

The ordering gate is the spec's sanity check that the ratings mean something:
after burn-in, Série A clubs rate above Série B clubs, who rate above the
clubs seen only as cup opponents. Tiers come from `division_table` for the
season being closed, ratings from a snapshot on the first day of the next
year, so every match of that season has been absorbed and none of the next.
"""

import numpy as np
import pytest

from brasileirao_simulator.domain.elo import EloParams, ratings_as_of, replay
from brasileirao_simulator.domain.elo_seeds import division_table
from brasileirao_simulator.domain.elo_snapshots import team_strength
from brasileirao_simulator.domain.match_store import MatchStore

# 2019 onwards is in the store; the spec names 30 matches as the burn-in a
# club needs, so the first full season after that is the first one scored.
ORDERED_SEASONS = (2022, 2023, 2024, 2025)


@pytest.fixture(scope="module")
def store():
    return MatchStore()


@pytest.fixture(scope="module")
def history(store):
    return replay(store, EloParams())


def _median_by_tier(store, history, season):
    snapshot = ratings_as_of(history, f"{season + 1}-01-01")
    tiers = division_table(store)
    tiers = tiers[tiers["season"] == season]
    medians = {}
    for tier in (1, 2, 3):
        rated = [snapshot[t] for t in tiers[tiers["tier"].astype(str) == str(tier)]["team_id"] if t in snapshot]
        medians[tier] = float(np.median(rated)) if rated else None
    return medians


def test_replay_covers_every_admitted_match_with_real_seeds(store, history):
    ratings = history.ratings
    assert len(ratings) == 2 * len(store.matches)
    assert not ratings[["elo_before", "elo_after"]].isna().any().any()
    first_rows = ratings.sort_values(["fixture_date", "fixture_id"]).drop_duplicates("team_id")
    assert set(first_rows["elo_before"].unique()) <= set(EloParams().seeds.values())


@pytest.mark.parametrize("season", ORDERED_SEASONS)
def test_serie_a_median_above_serie_b_above_cup_only(store, history, season):
    medians = _median_by_tier(store, history, season)
    assert medians[1] is not None and medians[2] is not None
    assert medians[1] > medians[2]
    if medians[3] is not None:
        assert medians[2] > medians[3]


def test_snapshot_agrees_with_team_strength_on_the_real_store(history):
    as_of = "2025-07-01"
    snapshot = ratings_as_of(history, as_of)
    frame = team_strength(history, as_of)
    assert dict(zip(frame["team_id"], frame["elo"])) == snapshot
    assert (frame["matches_used"] > 0).all()

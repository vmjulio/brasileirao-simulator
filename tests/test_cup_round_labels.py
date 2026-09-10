"""cup-round-labels-2026: the 2026 shards carry round labels the stage scale
had never seen, and _stage_rank raises on unknown labels by design. Two
branches were each fine alone - match-store was built on <=2025 shards, and
retrieve-all-competitions landed 2026 shards before MatchStore existed - and
broke together: MatchStore() raised on the merged tree. This is that
integration test, plus the mapping decisions the fix makes.
"""

import pytest

from brasileirao_simulator.domain.competitions import (
    COMPETITIONS,
    GROUP_STAGE,
    ROUND_OF_16,
    _stage_rank,
)
from brasileirao_simulator.domain.match_store import MatchStore

NEW_2026_LABELS = {
    73: ["1/256-finals", "1/128-finals", "Round of 128", "Round of 64"],
    13: ["Qualification Round 1", "Qualification Round 2", "Qualification Round 3"],
    11: ["Qualification Round 1"],
}


def test_match_store_builds_from_the_real_root():
    """The integration bug itself: construction must not raise now that the
    2026 shards for 73, 13 and 11 are present alongside match_store."""
    MatchStore()


@pytest.mark.parametrize(
    "league_id,label", [(lid, label) for lid, labels in NEW_2026_LABELS.items() for label in labels]
)
def test_new_2026_labels_are_preliminary_and_dropped(league_id, label):
    assert _stage_rank(label) == 0
    assert not COMPETITIONS[league_id].from_round.admits(label)


def test_round_of_32_is_unchanged():
    """Below the round of 16 (Copa do Brasil drops it) and above the group
    stage (Sudamericana admits it) - exactly as before the fix."""
    assert not ROUND_OF_16.admits("Round of 32")
    assert GROUP_STAGE.admits("Round of 32")


def test_unseen_label_still_raises():
    with pytest.raises(ValueError):
        _stage_rank("Play-off Round")


def test_2026_cup_coverage_excludes_every_new_label():
    """The 2026 shards load, the new labels contribute nothing, and the
    competitions still contribute their post-cutoff rounds."""
    store = MatchStore()
    matches = store.matches
    season_2026 = matches[matches["season"] == 2026]

    new_labels = {label for labels in NEW_2026_LABELS.values() for label in labels}
    assert not set(season_2026["round"]).intersection(new_labels)

    for league_id in (73, 13, 11):
        admitted = (season_2026["league_id"] == league_id).sum()
        assert admitted > 0, f"league {league_id} admitted nothing for 2026"

    coverage = store.coverage(2026)
    assert set(coverage["competitions"]) >= {72, 73, 13, 11}

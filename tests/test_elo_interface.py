"""Shape tests for the elo-interface ticket. Nothing here tests replay,
snapshot or seeding BEHAVIOUR - those are the follow-up tickets' gates. This
file only pins the signatures and defaults those tickets are built against.
"""

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from brasileirao_simulator.domain import elo, elo_seeds
from brasileirao_simulator.domain.elo import EloHistory, EloParams, margin_multiplier


def test_elo_params_defaults():
    params = EloParams()
    assert params.k == 20.0
    assert params.home_advantage == 85.0
    assert dict(params.seeds) == {1: 1500.0, 2: 1400.0, 3: 1300.0, "foreign": 1450.0}
    assert params.margin_ladder == (1.0, 1.75, 2.5)


def test_elo_params_is_frozen():
    params = EloParams()
    with pytest.raises(FrozenInstanceError):
        params.k = 99.0


def test_elo_params_seeds_default_cannot_be_mutated():
    params = EloParams()
    assert isinstance(params.seeds, MappingProxyType)
    with pytest.raises(TypeError):
        params.seeds[1] = 9999.0


def test_elo_params_seeds_default_is_shared_immutable_object():
    # Two instances see the same underlying mapping - safe only because
    # SEED_BY_DIVISION itself is a MappingProxyType, never a plain dict.
    a, b = EloParams(), EloParams()
    assert dict(a.seeds) == dict(b.seeds)


@pytest.mark.parametrize("d", range(0, 9))
def test_margin_multiplier_symmetric(d):
    ladder = (1.0, 1.75, 2.5)
    assert margin_multiplier(d, ladder) == margin_multiplier(-d, ladder)


def test_margin_multiplier_monotone_non_decreasing():
    ladder = (1.0, 1.75, 2.5)
    values = [margin_multiplier(d, ladder) for d in range(0, 9)]
    for earlier, later in zip(values, values[1:]):
        assert later >= earlier


def test_margin_multiplier_matches_ladder_at_1_2_3():
    ladder = (1.0, 1.75, 2.5)
    assert margin_multiplier(0, ladder) == ladder[0]
    assert margin_multiplier(1, ladder) == ladder[0]
    assert margin_multiplier(2, ladder) == ladder[1]
    assert margin_multiplier(3, ladder) == ladder[2]


def test_margin_multiplier_extrapolates_beyond_3():
    ladder = (1.0, 1.75, 2.5)
    d = 5
    assert margin_multiplier(d, ladder) == pytest.approx(ladder[2] * (11 + d) / 14)


def test_replay_not_implemented_names_ticket():
    with pytest.raises(NotImplementedError, match="elo-replay"):
        elo.replay(store=None)


def test_ratings_as_of_not_implemented_names_ticket():
    with pytest.raises(NotImplementedError, match="elo-snapshots"):
        elo.ratings_as_of(history=None, as_of_date="2025-01-01")


def test_seed_for_is_the_elo_seeds_implementation():
    # elo-division-seeds is implemented: `elo.seed_for` is no longer a
    # stub, it is `elo_seeds.seed_for` re-exported.
    assert elo.seed_for is elo_seeds.seed_for


@pytest.mark.parametrize(
    "column",
    [
        "fixture_id",
        "fixture_date",
        "season",
        "league_id",
        "team_id",
        "elo_before",
        "elo_after",
        "opponent_id",
        "is_home",
        "is_neutral",
        "matches_used",
    ],
)
def test_elo_history_docstring_lists_every_column(column):
    assert column in EloHistory.__doc__

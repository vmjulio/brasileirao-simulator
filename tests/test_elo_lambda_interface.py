"""Shape tests for the elo-lambda-interface ticket. Nothing here tests
`fit_difference_map`, `total_goals_params` or `team_strength_with_totals`
BEHAVIOUR - those are the follow-up tickets' (elo-difference-map,
elo-total-goals) gates. This file only pins the signatures, defaults and the
one implemented function (`lambdas`) those tickets and elo-adapter are built
against.
"""

from dataclasses import FrozenInstanceError

import pytest

from brasileirao_simulator.domain import elo_lambda
from brasileirao_simulator.domain import elo_difference_map
from brasileirao_simulator.domain.elo import EloParams
from brasileirao_simulator.domain.elo_lambda import (
    DifferenceMap,
    EloLambdaParams,
    fit_difference_map,
    lambdas,
    total_goals_params,
    team_strength_with_totals,
)


def test_elo_lambda_params_defaults():
    params = EloLambdaParams()
    assert params.home_advantage == 85.0
    assert params.eps == 0.05
    assert params.burn_in_season == 2019


def test_elo_lambda_params_is_frozen():
    params = EloLambdaParams()
    with pytest.raises(FrozenInstanceError):
        params.eps = 0.5


def test_elo_lambda_params_home_advantage_matches_elo_params():
    assert EloLambdaParams().home_advantage == EloParams().home_advantage


def test_difference_map_is_frozen():
    dm = DifferenceMap(slope=0.002, intercept=0.1, fitted_on_season=2019, n_matches=380)
    with pytest.raises(FrozenInstanceError):
        dm.slope = 0.5


def test_difference_map_call_is_linear():
    dm = DifferenceMap(slope=0.002, intercept=0.1, fitted_on_season=2019, n_matches=380)
    assert dm(100) == pytest.approx(0.3)
    assert dm(0) == pytest.approx(0.1)


def test_difference_map_monotone_for_positive_slope():
    dm = DifferenceMap(slope=0.002, intercept=0.1, fitted_on_season=2019, n_matches=380)
    values = [dm(x) for x in range(-200, 201, 50)]
    for earlier, later in zip(values, values[1:]):
        assert later > earlier


def test_lambdas_zero_difference_map_splits_totals_evenly():
    zero_map = DifferenceMap(slope=0.0, intercept=0.0, fitted_on_season=2019, n_matches=380)
    lam_home, lam_away = lambdas(
        elo_home=1500.0, elo_away=1500.0, total_home=1.4, total_away=1.2, difference_map=zero_map
    )
    assert lam_home == pytest.approx(1.3)
    assert lam_away == pytest.approx(1.3)


def test_lambdas_floors_home_at_eps_for_large_negative_difference():
    # lam_home = (total + diff) / 2, lam_away = (total - diff) / 2 (see
    # `lambdas`'s docstring), so a strongly negative expected_difference -
    # here, an away side rated far above the home side, seen through a
    # positive-slope map - floors lam_home at eps and leaves lam_away
    # carrying (expected_total - expected_difference) / 2.
    params = EloLambdaParams(eps=0.05)
    dm = DifferenceMap(slope=0.01, intercept=0.0, fitted_on_season=2019, n_matches=380)
    lam_home, lam_away = lambdas(
        elo_home=1400.0,
        elo_away=1900.0,
        total_home=1.4,
        total_away=1.2,
        difference_map=dm,
        params=params,
    )
    expected_difference = dm(1400.0 + params.home_advantage - 1900.0)
    expected_total = 1.4 + 1.2
    assert lam_home == pytest.approx(params.eps)
    assert lam_away == pytest.approx(max(params.eps, (expected_total - expected_difference) / 2))


def test_lambdas_neutral_drops_home_advantage():
    dm = DifferenceMap(slope=0.002, intercept=0.1, fitted_on_season=2019, n_matches=380)
    params = EloLambdaParams()
    neutral = lambdas(
        elo_home=1550.0,
        elo_away=1500.0,
        total_home=1.4,
        total_away=1.2,
        difference_map=dm,
        params=params,
        is_neutral=True,
    )
    no_advantage = lambdas(
        elo_home=1550.0,
        elo_away=1500.0,
        total_home=1.4,
        total_away=1.2,
        difference_map=dm,
        params=EloLambdaParams(home_advantage=0.0),
        is_neutral=False,
    )
    assert neutral == pytest.approx(no_advantage)


def test_fit_difference_map_re_exports_the_elo_difference_map_implementation():
    assert elo_lambda.fit_difference_map is elo_difference_map.fit_difference_map


def test_total_goals_params_is_a_stub_naming_its_ticket():
    with pytest.raises(NotImplementedError, match="elo-total-goals"):
        total_goals_params(None, "2020-01-01")


def test_team_strength_with_totals_is_a_stub_naming_its_ticket():
    with pytest.raises(NotImplementedError, match="elo-total-goals"):
        team_strength_with_totals(None, None, "2020-01-01")

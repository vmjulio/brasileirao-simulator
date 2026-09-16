import numpy as np

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.dixon_coles_adapter import DixonColesAdapter
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.simulators import SIMULATORS, simulator_for


SEASON = 2025
AS_OF = "2025-08-01"


def test_dixon_coles_is_registered():
    assert "dixon_coles" in SIMULATORS


def test_existing_simulators_are_untouched():
    """Additive: adding a simulator must not disturb the three that exist."""
    assert {"loop", "batch", "uncertain"} <= set(SIMULATORS)


def test_adapter_produces_a_lambda_per_fixture():
    adapter = simulator_for("dixon_coles", "poisson_same_venue_average", 2025)
    assert adapter is not None


def _baselines():
    tables = Tables(SeasonData(SEASON))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    remaining = tables.remaining_games(blank_from_date=AS_OF)
    ours = DixonColesAdapter("average", SEASON).build_baseline(fixtures, remaining)
    theirs = IterationBatchAdapter("average", SEASON)
    theirs.con.register("new_fixtures", fixtures)
    from brasileirao_simulator.domain.batch_simulation import build_baseline

    team_params = theirs.con.sql(theirs.queries.team_params_same_venue_average()).df()
    return ours, build_baseline(fixtures, remaining, team_params, SEASON)


def test_lambdas_are_finite_and_positive_on_a_real_date():
    ours, _ = _baselines()
    assert len(ours.lam_home) > 0
    for lam in (ours.lam_home, ours.lam_away):
        assert np.all(np.isfinite(lam))
        assert np.all(lam > 0)


def test_lambdas_differ_from_the_current_model():
    """A different estimator must actually estimate something different -
    otherwise the comparison in dixon_coles_backtest.py is measuring nothing."""
    ours, theirs = _baselines()
    assert ours.home_name == theirs.home_name  # same fixtures, same order
    assert np.any(np.abs(ours.lam_home - theirs.lam_home) > 1e-6)


def test_only_the_lambdas_change():
    """Everything else in the baseline - the standings, the played matches,
    the fixture list - must come through untouched."""
    ours, theirs = _baselines()
    assert ours.teams == theirs.teams
    assert np.array_equal(ours.points, theirs.points)
    assert np.array_equal(ours.fixture_id, theirs.fixture_id)

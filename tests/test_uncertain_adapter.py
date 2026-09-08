"""The uncertain-parameters adapter, and simulate_batch's lambda overrides.

UncertainParamsAdapter is the same statistical model as IterationBatchAdapter,
with each simulated season drawing its own team strengths instead of reusing
one fixed estimate. At a huge n_eff_scale that draw degenerates onto the fixed
estimate, so the two adapters' title distributions must then agree - that is
the strongest test available that the wiring didn't change the model.
"""

import duckdb
import numpy as np
import pytest

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.adapters.uncertain_params_adapter import UncertainParamsAdapter
from brasileirao_simulator.domain.batch_simulation import build_baseline, simulate_batch
from brasileirao_simulator.domain.parameter_uncertainty import draw_team_rates, fixture_lambdas
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.simulators import simulator_for


AS_OF = "2026-05-03"
BRASILEIRAO_TEAM_COUNT = 20


def _frames():
    tables = Tables(SeasonData(2026))
    return (
        tables.enriched_tidy_fixtures(blank_from_date=AS_OF),
        tables.remaining_games(blank_from_date=AS_OF),
    )


def test_adapter_produces_a_full_batch():
    fixtures, remaining = _frames()
    outcome = UncertainParamsAdapter("average", 2026).simulate_batch(fixtures, remaining, 20)

    assert outcome.rank.shape == (20, BRASILEIRAO_TEAM_COUNT)
    for row in outcome.rank:
        assert sorted(row) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))


def test_the_adapter_carries_the_season_the_service_checks():
    """SimulationService rejects an adapter whose season differs from params."""
    assert UncertainParamsAdapter("average", 2026).season == 2026


def test_uncertain_selects_its_adapter():
    assert isinstance(simulator_for("uncertain", "average", 2026), UncertainParamsAdapter)


@pytest.mark.slow  # 1500 iterations per side to get a stable champion share
def test_a_huge_n_eff_scale_agrees_with_the_fixed_adapter():
    """C2 degenerating to C1: at n_eff_scale so large every draw collapses onto
    the fixed estimate, UncertainParamsAdapter must reproduce
    IterationBatchAdapter's title distribution - not merely a similar one.

    1500 iterations and a 0.06 tolerance match
    test_full_vector_adapter_matches_the_iteration_adapter in
    test_batch_adapter.py, for the same reason: both adapters are fast, so the
    cost here is iteration count squared against wall time, not a SQL round
    trip.
    """
    fixtures, remaining = _frames()

    fixed = IterationBatchAdapter(
        "average", 2026, rng=np.random.default_rng(10)
    ).simulate_batch(fixtures, remaining, 1500)
    uncertain = UncertainParamsAdapter(
        "average", 2026, rng=np.random.default_rng(11), n_eff_scale=100_000
    ).simulate_batch(fixtures, remaining, 1500)

    fixed_share = (fixed.rank == 1).mean(axis=0)
    uncertain_share = (uncertain.rank == 1).mean(axis=0)

    assert np.abs(fixed_share - uncertain_share).max() < 0.06


def test_simulate_batch_accepts_a_1d_lambda_override_unchanged():
    """A 1-D override must behave exactly like the baseline's own lambda -
    the override mechanism must not itself change the model."""
    fixtures, remaining = _frames()
    team_params = PoissonSameVenueAverageAdapter("average", 2026).get_team_params(
        fixtures.copy()
    )
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    default = simulate_batch(baseline, 30, np.random.default_rng(6))
    overridden = simulate_batch(
        baseline,
        30,
        np.random.default_rng(6),
        lam_home=baseline.lam_home,
        lam_away=baseline.lam_away,
    )

    assert np.array_equal(default.home_goals, overridden.home_goals)
    assert np.array_equal(default.away_goals, overridden.away_goals)


def test_simulate_batch_accepts_a_2d_lambda_override():
    """A per-iteration lambda (iterations, n_games), as fixture_lambdas
    produces, must be honoured fixture by fixture rather than collapsed back
    onto one row - in both the per-fixture default and the vectorised branch,
    the only judgement call this override mechanism required and the one
    branch whose 2-D support was only sandbox-verified, not tested."""
    fixtures, remaining = _frames()
    team_params = PoissonSameVenueAverageAdapter("average", 2026).get_team_params(
        fixtures.copy()
    )
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    iterations = 10
    n_games = len(baseline.lam_home)
    # A distinct lambda per iteration: row i is entirely i+1, so the goals
    # drawn for iteration i must come from a Poisson(i+1) rather than from
    # baseline.lam_home.
    lam_home = np.tile(np.arange(1, iterations + 1, dtype=float)[:, None], (1, n_games))
    lam_away = np.zeros((iterations, n_games))

    for vectorise_fixtures in (False, True):
        outcome = simulate_batch(
            baseline,
            iterations,
            np.random.default_rng(7),
            vectorise_fixtures=vectorise_fixtures,
            lam_home=lam_home,
            lam_away=lam_away,
        )

        assert outcome.home_goals.shape == (iterations, n_games)
        # away lambda is 0 everywhere, so every away goal must be 0.
        assert (outcome.away_goals == 0).all()
        # iteration 0 was drawn from Poisson(1); the last iteration from
        # Poisson(iterations). Their means must differ by roughly that ratio.
        assert outcome.home_goals[0].mean() < outcome.home_goals[-1].mean()


def test_the_adapter_uses_the_drawn_lambda_not_the_fixed_one():
    """Reproduce the adapter's own draw and demand identical scorelines.

    Exact rather than statistical: this is a wiring question, and a wiring
    question has a deterministic answer. It also catches a home/away swap,
    which an overdispersion signature cannot.

    Supersedes the old statistical version of this test (a var/mean > 1.02
    overdispersion proxy at 20_000 iterations, guarding at ~2 sigma with a
    hand-tuned threshold): this is strictly more powerful - any wiring
    difference at all breaks exact scoreline equality - and runs in
    milliseconds instead of needing a slow marker.
    """
    fixtures, remaining = _frames()
    outcome = UncertainParamsAdapter(
        "average", 2026, rng=np.random.default_rng(8)
    ).simulate_batch(fixtures, remaining, 50)

    rng = np.random.default_rng(8)
    draws = draw_team_rates(outcome.baseline, 50, rng)
    lam_home, lam_away = fixture_lambdas(outcome.baseline, draws)
    assert lam_home.ndim == 2, "a fixed lambda would be 1-D"

    expected = simulate_batch(outcome.baseline, 50, rng, lam_home=lam_home, lam_away=lam_away)
    assert np.array_equal(outcome.home_goals, expected.home_goals)
    assert np.array_equal(outcome.away_goals, expected.away_goals)

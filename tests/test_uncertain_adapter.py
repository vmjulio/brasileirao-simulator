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
    onto one row."""
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

    outcome = simulate_batch(
        baseline, iterations, np.random.default_rng(7), lam_home=lam_home, lam_away=lam_away
    )

    assert outcome.home_goals.shape == (iterations, n_games)
    # away lambda is 0 everywhere, so every away goal must be 0.
    assert (outcome.away_goals == 0).all()
    # iteration 0 was drawn from Poisson(1); the last iteration from
    # Poisson(iterations). Their means must differ by roughly that ratio.
    assert outcome.home_goals[0].mean() < outcome.home_goals[-1].mean()


@pytest.mark.slow  # 20_000 iterations for a stable variance estimate
def test_the_adapter_actually_uses_the_drawn_lambda_not_the_fixed_one():
    """Closes a coverage gap: nothing else in this file exercises the wiring
    that plugs draw_team_rates' output into simulate_batch - an adapter that
    silently computed the draw and then called simulate_batch with
    baseline.lam_home/lam_away instead (passing the FIXED lambda) still made
    every other test in this file pass, discovered by deliberately making
    that exact change and rerunning the suite.

    The signature this catches: a Poisson mixed over a Gamma-distributed rate
    is over-dispersed (Var(Y) = E[lambda] + Var(lambda) > E[lambda]), while a
    Poisson driven by one fixed lambda has Var(Y) == E[lambda] up to sampling
    noise. So a single fixture's goal counts having var/mean detectably above
    1 is direct evidence the lambda actually varied by iteration - which only
    happens if the drawn lambda, not the fixed one, reached simulate_batch.

    Picking the thinnest team's home fixture maximises the effect (smallest
    n_eff -> widest draw). At 20_000 iterations the null (fixed lambda) ratio
    stays within about +/-0.015 of 1.0 by direct simulation; the real
    adapter's ratio for this fixture lands around 1.05-1.06 across seeds.
    1.02 sits safely between the two.
    """
    fixtures, remaining = _frames()
    team_params = PoissonSameVenueAverageAdapter("average", 2026).get_team_params(
        fixtures.copy()
    )
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    match_counts = con.sql(Queries(2026).team_match_counts()).df()
    baseline = build_baseline(fixtures, remaining, team_params, 2026, match_counts=match_counts)

    thinnest_team = int(np.argmin(baseline.home_match_count))
    fixture = int(np.where(baseline.home_team == thinnest_team)[0][0])

    outcome = UncertainParamsAdapter(
        "average", 2026, rng=np.random.default_rng(8)
    ).simulate_batch(fixtures, remaining, 20_000)

    goals = outcome.home_goals[:, fixture]
    assert goals.var() / goals.mean() > 1.02

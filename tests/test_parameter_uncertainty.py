"""Drawing team strengths per simulated season.

The mean of every draw must equal C1's fixed estimate, so C1 and C2 differ in
spread alone. Spread comes from how many real matches back each estimate.
"""

from dataclasses import replace

import numpy as np

from brasileirao_simulator.domain.batch_simulation import ADJUSTMENT_WEIGHT, build_baseline
from brasileirao_simulator.domain.parameter_uncertainty import (
    draw_team_rates,
    fixture_lambdas,
)


AS_OF = "2026-05-03"
LOTS = 40_000


def _baseline_with_counts():
    """Baseline carrying real per-venue match counts (12-13 promoted, 19 else)."""
    import duckdb

    from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
        PoissonSameVenueAverageAdapter,
    )
    from brasileirao_simulator.domain.queries import Queries
    from brasileirao_simulator.domain.season_data import SeasonData
    from brasileirao_simulator.domain.tables import Tables

    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    remaining = tables.remaining_games(blank_from_date=AS_OF)
    team_params = PoissonSameVenueAverageAdapter("average", 2026).get_team_params(
        fixtures.copy()
    )

    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    match_counts = con.sql(Queries(2026).team_match_counts()).df()

    return build_baseline(fixtures, remaining, team_params, 2026, match_counts=match_counts)


def test_draws_are_centred_on_the_fixed_estimate():
    """Over many draws the mean must converge on C1's lambda - otherwise C2
    changes the model, not just its uncertainty."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, LOTS, np.random.default_rng(1))

    assert np.allclose(draws.home_attack.mean(axis=0), baseline.home_attack, rtol=0.02)
    assert np.allclose(draws.away_defence.mean(axis=0), baseline.away_defence, rtol=0.02)


def test_teams_with_less_evidence_are_drawn_more_widely():
    """The whole point: a parameter a third borrowed from a prior should not be
    as confident as one backed by 19 matches."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, LOTS, np.random.default_rng(2))

    spread = draws.home_attack.std(axis=0) / baseline.home_attack
    thin = baseline.home_match_count < 19
    assert thin.any(), "2026 should have promoted sides with a partial window"
    assert spread[thin].mean() > spread[~thin].mean()


def test_a_huge_n_eff_collapses_onto_the_fixed_estimate():
    """As evidence grows the draw degenerates to C1, which is the exact sense in
    which C2 contains C1.

    rtol is 1e-2, not 1e-3: 2026's thinnest team (Chapecoense-sc, 5 away
    matches) has n_eff = 5 * 100_000 = 500_000 even at this huge scale, so the
    draw's own std/rate is 1/sqrt(500_000) ~= 0.14%. Empirically the max
    relative deviation over 500 draws for that team runs ~0.45-0.55% (see the
    Task 3 report), so 1e-3 fails on real data essentially every run - not
    because the mean is wrong, but because the tolerance was tighter than
    sampling noise at this sample size. 1e-2 stays far below the spread a
    non-degenerate draw shows (several percent to tens of percent, per
    test_teams_with_less_evidence_are_drawn_more_widely) while comfortably
    clearing that noise floor."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, 500, np.random.default_rng(3), n_eff_scale=100_000)

    assert np.allclose(draws.home_attack, baseline.home_attack, rtol=1e-2)


def test_lambdas_use_the_same_blend_as_the_fixed_path():
    """Same 50/50 formula, different inputs - that is the entire change."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, 200, np.random.default_rng(4))
    lam_home, lam_away = fixture_lambdas(baseline, draws)

    assert lam_home.shape == (200, len(baseline.lam_home))
    expected_first = (
        ADJUSTMENT_WEIGHT * draws.home_attack[0, baseline.home_team[0]]
        + ADJUSTMENT_WEIGHT * draws.away_defence[0, baseline.away_team[0]]
    )
    assert lam_home[0, 0] == expected_first


def test_a_team_with_no_matches_is_not_drawn():
    """n_eff of zero makes the Gamma undefined; such a rate stays fixed."""
    baseline = _baseline_with_counts()
    counts = baseline.home_match_count.copy()
    counts[0] = 0
    stripped = replace(baseline, home_match_count=counts)   # dataclasses.replace

    draws = draw_team_rates(stripped, 100, np.random.default_rng(5))

    assert (draws.home_attack[:, 0] == stripped.home_attack[0]).all()

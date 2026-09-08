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

    for rates, drawn in (
        (baseline.home_attack, draws.home_attack),
        (baseline.home_defence, draws.home_defence),
        (baseline.away_attack, draws.away_attack),
        (baseline.away_defence, draws.away_defence),
    ):
        assert np.allclose(drawn.mean(axis=0), rates, rtol=0.02)


def test_spread_follows_the_inverse_square_root_of_the_evidence():
    """Relative SD must be 1/sqrt(n_eff), not merely decreasing in it - and each
    rate must be paired with its OWN venue's match count. Four near-identical
    _draw calls differing only in that pairing is where a copy-paste slip lives.
    """
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, LOTS, np.random.default_rng(6))

    for rates, counts, drawn in (
        (baseline.home_attack, baseline.home_match_count, draws.home_attack),
        (baseline.home_defence, baseline.home_match_count, draws.home_defence),
        (baseline.away_attack, baseline.away_match_count, draws.away_attack),
        (baseline.away_defence, baseline.away_match_count, draws.away_defence),
    ):
        assert np.allclose(drawn.std(axis=0) / rates, 1 / np.sqrt(counts), rtol=0.05)


def test_a_huge_n_eff_collapses_onto_the_fixed_estimate():
    """As evidence grows the draw degenerates to C1, which is the exact sense in
    which C2 contains C1.

    n_eff_scale is 1e8, not 100_000: 2026's thinnest team/venue overall
    (Chapecoense-sc, 5 away matches, governing all four rate arrays via
    min(home_match_count, away_match_count)) has n_eff = 5 * 1e8 = 5e8 at this
    scale, so the draw's own relative SD is 1/sqrt(5e8) ~= 4.5e-5. The expected
    max relative deviation over 500 draws for that team is ~4x its SD, ~1.8e-4,
    which supports the original rtol=1e-3 with ~5x headroom - comfortably below
    the spread a non-degenerate draw shows (several percent to tens of percent,
    per test_spread_follows_the_inverse_square_root_of_the_evidence) while
    clearing the sampling noise floor with room to spare."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, 500, np.random.default_rng(3), n_eff_scale=1e8)

    for rates, drawn in (
        (baseline.home_attack, draws.home_attack),
        (baseline.home_defence, draws.home_defence),
        (baseline.away_attack, draws.away_attack),
        (baseline.away_defence, draws.away_defence),
    ):
        assert np.allclose(drawn, rates, rtol=1e-3)


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


def test_a_rate_of_zero_is_not_drawn():
    """The other half of the zero guard: `rates > 0`, not just `n_eff > 0`. A
    rate of zero has no distribution to draw from either."""
    baseline = _baseline_with_counts()
    rates = baseline.home_attack.copy()
    rates[1] = 0.0
    stripped = replace(baseline, home_attack=rates)   # dataclasses.replace

    draws = draw_team_rates(stripped, 100, np.random.default_rng(5))

    assert (draws.home_attack[:, 1] == 0.0).all()

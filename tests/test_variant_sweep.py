"""analytic_forecasts_for_date, score_variant_matches and
run_multi_season_sweep - the analytic (non-Monte-Carlo) sweep harness that
replaces the season-simulation + pickle route lookback_sweep.py used to
take per as-of date. Exercised against real 2025/2026 fixtures at a short
date slice for the fast tests; the full multi-season wiring is checked once,
marked slow, since it necessarily replays whole seasons (score_variant_matches
takes `dates` explicitly, but run_multi_season_sweep does not - it always
replays backfill_dates(season) in full, same as
lookback_sweep.run_multi_season_sweep did).
"""

import numpy as np
import pytest

from brasileirao_simulator.domain.batch_simulation import FULL_WINDOW_MATCHES
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.variant_sweep import (
    analytic_forecasts_for_date,
    partial_window_team_venues,
    run_multi_season_sweep,
    score_variant_matches,
)


SEASON = 2025


# ---------------------------------------------------------------------------
# analytic_forecasts_for_date
# ---------------------------------------------------------------------------


def test_analytic_forecasts_for_date_probs_sum_to_one():
    forecasts = analytic_forecasts_for_date(SEASON, "2025-08-31")

    assert len(forecasts) > 0
    for match_key, probs in forecasts.items():
        assert sum(probs) == pytest.approx(1.0, abs=1e-9), match_key
        assert all(0.0 <= p <= 1.0 for p in probs)


def test_analytic_forecasts_for_date_keys_are_home_x_away():
    """analytic_forecasts_for_date only forecasts REMAINING fixtures (like
    the batch/uncertain simulators' own match_results - see
    domain/batch_simulation.py's remaining_games), so the match picked here
    must still be unplayed as of the as-of date, not merely any match from
    the season."""
    forecasts = analytic_forecasts_for_date(SEASON, "2025-05-10")
    assert "Mirassol x Juventude" in forecasts


def test_analytic_forecasts_for_date_lookback_changes_the_forecast():
    """A knob that does nothing would make the lookback rewire indistinguishable
    from a genuine no-op - this is the check that it actually bites, the
    analytic-route analogue of test_lookback_window.py's real-data check."""
    short = analytic_forecasts_for_date(SEASON, "2025-08-31", lookback=8)
    full = analytic_forecasts_for_date(SEASON, "2025-08-31", lookback=FULL_WINDOW_MATCHES)

    common = set(short) & set(full)
    assert len(common) > 0
    assert any(short[k] != full[k] for k in common), (
        "lookback=8 produced identical forecasts to lookback=19"
    )


def test_analytic_forecasts_for_date_adjustment_weight_changes_the_forecast():
    equal_weight = analytic_forecasts_for_date(SEASON, "2025-08-31", adjustment_weight=0.5)
    attacker_trusted = analytic_forecasts_for_date(SEASON, "2025-08-31", adjustment_weight=0.7)

    common = set(equal_weight) & set(attacker_trusted)
    assert len(common) > 0
    assert any(equal_weight[k] != attacker_trusted[k] for k in common), (
        "adjustment_weight=0.7 produced identical forecasts to 0.5"
    )


def test_analytic_forecasts_for_date_weights_changes_the_forecast():
    """The analytic-route analogue of test_recency_weights.py's real-data
    check - pins that the recency-weight knob actually reaches a forecast,
    not just team_params_same_venue_average.sql's own output."""
    current = analytic_forecasts_for_date(SEASON, "2025-08-31", weights=(4, 3, 1))
    flat = analytic_forecasts_for_date(SEASON, "2025-08-31", weights=(1, 1, 1))

    common = set(current) & set(flat)
    assert len(common) > 0
    assert any(current[k] != flat[k] for k in common), (
        "weights=(1,1,1) produced identical forecasts to (4,3,1)"
    )


def test_analytic_forecasts_for_date_prior_weight_changes_the_forecast():
    """The analytic-route analogue of test_prior_weight.py's real-data check,
    on an early-season date where partial-window teams are common."""
    default = analytic_forecasts_for_date(2016, "2016-06-01", prior_weight=1.0)
    strong = analytic_forecasts_for_date(2016, "2016-06-01", prior_weight=2.0)

    common = set(default) & set(strong)
    assert len(common) > 0
    assert any(default[k] != strong[k] for k in common), (
        "prior_weight=2.0 produced identical forecasts to 1.0"
    )


def test_analytic_forecasts_for_date_reusing_tables_matches_a_fresh_one():
    """score_variant_matches builds one Tables(SeasonData(season)) per season
    and reuses it across dates for speed - this pins that reuse to be a pure
    performance change, not a behaviour change."""
    fresh = analytic_forecasts_for_date(SEASON, "2025-08-31")

    tables = Tables(SeasonData(SEASON))
    reused = analytic_forecasts_for_date(SEASON, "2025-08-31", tables=tables)

    assert fresh == reused


def test_analytic_forecasts_for_date_returns_empty_when_nothing_remains():
    """A date on/after the season's last game has no remaining fixtures to
    forecast - build_baseline (see domain/batch_simulation.py) already
    handles the empty case; this pins that analytic_forecasts_for_date does
    not choke on it either."""
    forecasts = analytic_forecasts_for_date(SEASON, "2025-12-31")
    assert forecasts == {}


# ---------------------------------------------------------------------------
# score_variant_matches
# ---------------------------------------------------------------------------


def test_score_variant_matches_bounded_brier_and_nothing_missing():
    dates = ["2025-08-24", "2025-08-31"]
    matches = score_variant_matches(SEASON, dates)

    assert len(matches) > 0
    assert matches.attrs["missing"] == 0
    assert (matches["brier"] >= 0).all() and (matches["brier"] <= 2).all()
    assert (matches["reference_brier"] >= 0).all() and (matches["reference_brier"] <= 2).all()


def test_score_variant_matches_every_match_scored_exactly_once():
    dates = ["2025-08-24", "2025-08-31", "2025-09-07"]
    matches = score_variant_matches(SEASON, dates)

    assert matches["match_key"].is_unique


# ---------------------------------------------------------------------------
# run_multi_season_sweep - full wiring, necessarily a whole-season replay.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_run_multi_season_sweep_lookback_baseline_row_has_zero_diff():
    per_season, pooled, matches_by_season = run_multi_season_sweep(
        seasons=[SEASON],
        param_name="lookback",
        values=[12, FULL_WINDOW_MATCHES],
        baseline_value=FULL_WINDOW_MATCHES,
    )

    baseline_rows = per_season[per_season["lookback"] == FULL_WINDOW_MATCHES]
    assert (baseline_rows["diff_vs_baseline"] == 0.0).all()
    assert baseline_rows["beats_baseline"].isna().all()

    pooled_baseline = pooled[pooled["lookback"] == FULL_WINDOW_MATCHES].iloc[0]
    assert pooled_baseline["diff_vs_baseline"] == 0.0

    assert set(matches_by_season) == {SEASON}
    assert set(matches_by_season[SEASON]) == {12, FULL_WINDOW_MATCHES}


@pytest.mark.slow
def test_run_multi_season_sweep_adjustment_weight_baseline_row_has_zero_diff():
    per_season, pooled, _ = run_multi_season_sweep(
        seasons=[SEASON],
        param_name="adjustment_weight",
        values=[0.4, 0.5],
        baseline_value=0.5,
    )

    baseline_rows = per_season[per_season["adjustment_weight"] == 0.5]
    assert (baseline_rows["diff_vs_baseline"] == 0.0).all()

    challenger_rows = per_season[per_season["adjustment_weight"] == 0.4]
    assert challenger_rows["diff_ci_low"].notna().all()
    assert challenger_rows["diff_ci_high"].notna().all()
    assert (challenger_rows["diff_ci_low"] <= challenger_rows["diff_ci_high"]).all()


@pytest.mark.slow
def test_run_multi_season_sweep_weights_baseline_row_has_zero_diff():
    per_season, pooled, _ = run_multi_season_sweep(
        seasons=[SEASON],
        param_name="weights",
        values=[(1, 1, 1), (4, 3, 1)],
        baseline_value=(4, 3, 1),
    )

    baseline_rows = per_season[per_season["weights"] == (4, 3, 1)]
    assert (baseline_rows["diff_vs_baseline"] == 0.0).all()
    assert baseline_rows["beats_baseline"].isna().all()

    pooled_baseline = pooled[pooled["weights"] == (4, 3, 1)].iloc[0]
    assert pooled_baseline["diff_vs_baseline"] == 0.0


@pytest.mark.slow
def test_run_multi_season_sweep_prior_weight_baseline_row_has_zero_diff():
    per_season, pooled, _ = run_multi_season_sweep(
        seasons=[SEASON],
        param_name="prior_weight",
        values=[0.0, 1.0],
        baseline_value=1.0,
    )

    baseline_rows = per_season[per_season["prior_weight"] == 1.0]
    assert (baseline_rows["diff_vs_baseline"] == 0.0).all()

    challenger_rows = per_season[per_season["prior_weight"] == 0.0]
    assert challenger_rows["diff_ci_low"].notna().all()
    assert challenger_rows["diff_ci_high"].notna().all()
    assert (challenger_rows["diff_ci_low"] <= challenger_rows["diff_ci_high"]).all()


def test_run_multi_season_sweep_rejects_an_unknown_param_name():
    with pytest.raises(ValueError, match="param_name"):
        run_multi_season_sweep(
            seasons=[SEASON], param_name="not_a_real_param", values=[1, 2], baseline_value=1
        )


# ---------------------------------------------------------------------------
# partial_window_team_venues - who the prior_weight sweep can actually move.
# ---------------------------------------------------------------------------


def test_partial_window_team_venues_finds_thin_teams_early_in_a_season():
    """2016 is the first imported season - no prior-season history to draw
    on, so its early dates should have plenty of partial-window teams."""
    affected = partial_window_team_venues(2016, ["2016-06-01"])

    assert len(affected) > 0
    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in affected)


def test_partial_window_team_venues_matches_team_match_counts_directly():
    """Cross-check against an independent read of team_match_counts.sql for
    the same date, rather than assuming how far into a season windows fill up
    (empirically, a newly promoted side with no prior-season history can
    still be short of 19 within-season home games as late as November - see
    the sweep report) - this pins the function to the query it wraps instead
    of to an assumption about season progress."""
    import duckdb

    from brasileirao_simulator.domain.queries import Queries
    from brasileirao_simulator.domain.tables import Tables

    season, as_of_date = 2025, "2025-11-01"
    affected = partial_window_team_venues(season, [as_of_date])

    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    counts = con.sql(Queries(season).team_match_counts()).df()
    expected = set(
        zip(
            counts.loc[counts["match_count"] < FULL_WINDOW_MATCHES, "team_name"],
            counts.loc[counts["match_count"] < FULL_WINDOW_MATCHES, "venue"],
        )
    )

    assert affected == expected
    assert len(affected) > 0, "test fixture assumption broke - no partial-window teams on this date any more"

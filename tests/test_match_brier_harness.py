"""played_matches, assign_horizon0, generate_forecast_pickles and
score_horizon0 - the harness that turns real fixtures and simulator pickles
into one scored row per match. Deliberately exercises this against 2025 real
data (fixtures.csv, and files/pkl/2025's real production pickles for the
`batch` side) at tiny iteration counts / a short date slice for speed - the
join logic being correct does not depend on iteration count.
"""

import shutil

import numpy as np
import pytest

from brasileirao_simulator.entrypoints.match_brier_backtest import (
    assign_horizon0,
    generate_forecast_pickles,
    last_before,
    match_forecast_probs,
    played_matches,
    score_horizon0,
)
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter


SEASON = 2025
SCRATCH_ROOT = "files/pkl_match_brier_test"


@pytest.fixture
def scratch_root():
    yield SCRATCH_ROOT
    shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)


# ---------------------------------------------------------------------------
# played_matches
# ---------------------------------------------------------------------------


def test_played_matches_returns_one_row_per_played_fixture():
    played = played_matches(SEASON)
    assert len(played) == 380  # 2025 is fully played, no cancellations
    assert set(played["outcome"]) <= {"home", "draw", "away"}


def test_played_matches_match_key_is_home_x_away():
    played = played_matches(SEASON)
    row = played.iloc[0]
    assert row["match_key"] == f"{row['home_team']} x {row['away_team']}"


def test_played_matches_outcome_matches_the_real_scoreline():
    """Cross-check outcome against fixtures.csv directly for a known match:
    2025-03-29 Gremio 2 x 1 Atletico-MG -> home win."""
    played = played_matches(SEASON)
    row = played[played["match_key"] == "Gremio x Atletico-MG"].iloc[0]
    assert row["outcome"] == "home"
    assert row["local_date"] == "2025-03-29"
    assert row["round_"] == 1


# ---------------------------------------------------------------------------
# last_before / assign_horizon0
# ---------------------------------------------------------------------------


def test_last_before_returns_the_largest_date_strictly_less_than_target():
    dates = ["2025-03-29", "2025-04-05", "2025-04-12"]
    assert last_before(dates, "2025-04-12") == "2025-04-05"
    assert last_before(dates, "2025-04-06") == "2025-04-05"


def test_last_before_returns_none_when_nothing_is_earlier():
    dates = ["2025-03-29", "2025-04-05"]
    assert last_before(dates, "2025-03-29") is None
    assert last_before(dates, "2025-01-01") is None


def test_assign_horizon0_drops_matches_with_no_prior_forecast():
    played = played_matches(SEASON)
    dates = ["2025-03-29", "2025-04-05", "2025-04-12"]

    with_horizon = assign_horizon0(played, dates)

    # every match played ON 2025-03-29 (the first date) has no strictly
    # earlier date to forecast it from, and must be dropped.
    assert "2025-03-29" not in with_horizon["local_date"].tolist() or (
        with_horizon[with_horizon["local_date"] == "2025-03-29"].empty
    )
    assert with_horizon.attrs["dropped_no_prior_forecast"] == (
        (played["local_date"] == "2025-03-29").sum()
    )


def test_assign_horizon0_uses_the_most_recent_prior_date():
    played = played_matches(SEASON)
    dates = ["2025-03-29", "2025-04-05", "2025-04-12"]

    with_horizon = assign_horizon0(played, dates)

    later_matches = with_horizon[with_horizon["local_date"] == "2025-04-12"]
    assert (later_matches["as_of_date"] == "2025-04-05").all()


# ---------------------------------------------------------------------------
# match_forecast_probs, against real files/pkl/2025 production data
# ---------------------------------------------------------------------------


def test_match_forecast_probs_reads_real_production_pickle_and_sums_to_one():
    persistence = PickleAdapter("files/pkl", SEASON)
    played = played_matches(SEASON)
    # A match from round 5, forecast as of 2025-05-10 - both known to exist
    # in the real backfill history (see the task's own exploration notes).
    row = played[played["match_key"] == "Juventude x Mirassol"].iloc[0]

    probs = match_forecast_probs(persistence, "average", "2025-05-10", row["match_key"])

    assert probs is not None
    assert sum(probs) == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_match_forecast_probs_returns_none_for_a_missing_pickle():
    persistence = PickleAdapter("files/pkl", SEASON)
    probs = match_forecast_probs(persistence, "average", "1999-01-01", "Nobody x Nowhere")
    assert probs is None


def test_match_forecast_probs_returns_none_for_a_match_absent_from_match_results():
    persistence = PickleAdapter("files/pkl", SEASON)
    probs = match_forecast_probs(persistence, "average", "2025-05-10", "Nobody x Nowhere")
    assert probs is None


# ---------------------------------------------------------------------------
# generate_forecast_pickles - writes ONLY into the scratch root, never
# files/pkl/.
# ---------------------------------------------------------------------------


def test_generate_forecast_pickles_writes_only_under_scratch_root(scratch_root):
    variant_dir = generate_forecast_pickles(
        season=SEASON,
        simulator_name="uncertain",
        iterations=10,
        scratch_root=scratch_root,
        seed=0,
        dates=["2025-08-31"],
    )

    assert variant_dir == f"{scratch_root}/uncertain"
    persistence = PickleAdapter(variant_dir, SEASON)
    results = persistence.load_results(strategy="average", suffix="2025-08-31")
    assert results is not None
    assert "match_results" in results


def test_generate_forecast_pickles_rejects_an_unknown_simulator_name(scratch_root):
    with pytest.raises(ValueError, match="unsupported simulator"):
        generate_forecast_pickles(
            season=SEASON,
            simulator_name="not_a_real_simulator",
            iterations=10,
            scratch_root=scratch_root,
            dates=["2025-08-31"],
        )


# ---------------------------------------------------------------------------
# score_horizon0 - the full join, batch (real production data) vs a tiny
# freshly generated uncertain scratch run, over a short date slice.
# ---------------------------------------------------------------------------


def test_score_horizon0_produces_one_row_per_scored_match_with_bounded_brier(scratch_root):
    dates = ["2025-08-24", "2025-08-31"]
    uncertain_dir = generate_forecast_pickles(
        season=SEASON,
        simulator_name="uncertain",
        iterations=50,
        scratch_root=scratch_root,
        seed=0,
        dates=dates,
    )

    run = score_horizon0(season=SEASON, batch_root="files/pkl", uncertain_root=uncertain_dir)

    assert len(run.matches) > 0
    assert (run.matches["brier_batch"] >= 0).all() and (run.matches["brier_batch"] <= 2).all()
    assert (run.matches["brier_uncertain"] >= 0).all() and (run.matches["brier_uncertain"] <= 2).all()
    assert (run.matches["brier_reference"] >= 0).all() and (run.matches["brier_reference"] <= 2).all()
    # diff is exactly what it claims to be
    expected_diff = (run.matches["brier_uncertain"] - run.matches["brier_batch"]).to_numpy()
    assert np.allclose(run.matches["diff"].to_numpy(), expected_diff)


def test_score_horizon0_reference_probs_sum_to_one(scratch_root):
    uncertain_dir = generate_forecast_pickles(
        season=SEASON,
        simulator_name="uncertain",
        iterations=20,
        scratch_root=scratch_root,
        seed=0,
        dates=["2025-08-31"],
    )
    run = score_horizon0(season=SEASON, batch_root="files/pkl", uncertain_root=uncertain_dir)
    assert sum(run.reference_probs) == pytest.approx(1.0)


def test_score_horizon0_reports_dropped_reasons(scratch_root):
    uncertain_dir = generate_forecast_pickles(
        season=SEASON,
        simulator_name="uncertain",
        iterations=20,
        scratch_root=scratch_root,
        seed=0,
        dates=["2025-08-31"],
    )
    run = score_horizon0(season=SEASON, batch_root="files/pkl", uncertain_root=uncertain_dir)
    assert set(run.dropped) == {"no_prior_forecast", "missing_batch_pickle", "missing_uncertain_pickle"}
    # Since only one uncertain date was generated, virtually every match
    # whose as_of_date isn't that exact date has no uncertain forecast.
    assert run.dropped["missing_uncertain_pickle"] > 0

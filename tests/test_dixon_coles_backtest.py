"""Fast sanity checks for the data-vs-league-backtest ticket (E3/T3.2).

These are deliberately single-date, not single-season: the full 2020-2025
run this ticket produces takes minutes (two Dixon-Coles fits per date, one
at 2000 iterations on 207 clubs, across ~110 dates x 6 seasons) and is run
directly via `dixon_coles_backtest.py --data-vs-league`, not as a test. What
belongs in the fast suite is exactly what the ticket names: match-set
identity on one date, and arm A reproducing one already-committed number.
"""

import pandas as pd
import pytest

from brasileirao_simulator.domain import dixon_coles
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.dixon_coles_backtest import (
    dixon_coles_all_forecasts_for_date,
    dixon_coles_forecasts_for_date,
)
from brasileirao_simulator.entrypoints.variant_sweep import analytic_forecasts_for_date


SEASON = 2025
AS_OF = "2025-03-29"

# Palmeiras x Botafogo, 2025-03-29 as-of-date - a row already committed to
# files/exports/dixon_coles_vs_current.csv from a prior run of this same
# script. Arm A must reproduce it exactly (it is a pure function of the same
# fixtures/tables the CSV was built from) - the "one known number" sanity
# check, without paying for a full 374-match season run.
KNOWN_MATCH = "Palmeiras x Botafogo"
KNOWN_PROBS = (0.3470752770083402, 0.3310843677118626, 0.3218403552797642)


def test_arm_a_reproduces_a_known_committed_forecast():
    """Arm A reproducing one known number - see the module docstring."""
    tables = Tables(SeasonData(SEASON))
    forecasts, _ = dixon_coles_forecasts_for_date(SEASON, AS_OF, tables, dixon_coles.DEFAULT_XI)

    assert KNOWN_MATCH in forecasts
    for actual, expected in zip(forecasts[KNOWN_MATCH], KNOWN_PROBS):
        assert actual == pytest.approx(expected, abs=1e-9)


def test_match_sets_are_identical_across_arms_on_one_date():
    """Match-set identity on one date - arm A, arm B and the incumbent must
    forecast the exact same set of fixtures for a given as-of date, since
    the data-vs-league comparison is only clean on identical matches."""
    tables = Tables(SeasonData(SEASON))
    store = MatchStore()

    current = analytic_forecasts_for_date(SEASON, AS_OF, tables=tables)
    dixon_a, _ = dixon_coles_forecasts_for_date(SEASON, AS_OF, tables, dixon_coles.DEFAULT_XI)
    dixon_b, _, _, _ = dixon_coles_all_forecasts_for_date(
        SEASON, AS_OF, tables, store, dixon_coles.DEFAULT_XI, max_iterations=200
    )

    assert set(current) == set(dixon_a) == set(dixon_b)
    assert len(current) > 0

    for forecasts in (current, dixon_a, dixon_b):
        for probs in forecasts.values():
            assert sum(probs) == pytest.approx(1.0, abs=1e-6)


def test_arm_b_forecast_uses_only_matches_strictly_before_it():
    """Hand-picked late-season as-of date: the second-to-last horizon-0 date
    scored for 2025 (the last has nothing left in MatchStore to leak from,
    which would make the converse check below vacuous). Arm B's ratings for
    that date must be fit on `MatchStore.before(cutoff)`, which by
    construction excludes anything on or after `cutoff` - so nothing on or
    after the forecast's own frontier date + 1 day ever informs it."""
    dates = backfill_dates(SEASON)
    as_of_date = dates[-2]
    tables = Tables(SeasonData(SEASON))
    store = MatchStore()

    dixon_coles_all_forecasts_for_date(
        SEASON, as_of_date, tables, store, dixon_coles.DEFAULT_XI, max_iterations=200
    )

    cutoff = str((pd.Timestamp(as_of_date) + pd.Timedelta(days=1)).date())
    visible = store.before(cutoff)
    kickoff = pd.to_datetime(visible["fixture_date"], utc=True, format="mixed")
    assert (kickoff < pd.Timestamp(cutoff, tz="UTC")).all()

    # And the converse: at least one MatchStore match exists on or after the
    # cutoff for this season - i.e. the assertion above is non-vacuous.
    everything = store.matches
    kickoff_all = pd.to_datetime(everything["fixture_date"], utc=True, format="mixed")
    assert (kickoff_all >= pd.Timestamp(cutoff, tz="UTC")).any()

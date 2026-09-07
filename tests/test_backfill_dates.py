"""Which dates a backfill replays.

A season in progress has fixtures scheduled beyond today. Simulating "as of" one
of those future dates blanks nothing that is not already unknown, so it repeats
the latest real snapshot — identical work, stored under a misleading date. The
default upper bound is therefore the last date that actually has a result.
"""

import pandas as pd

from brasileirao_simulator.domain.season_dates import (
    dates_from_fixtures,
    latest_result_date,
)
from brasileirao_simulator.entrypoints.backfill import backfill_dates


def _fixtures(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """Fixtures as (kickoff, goals_home); a null goals_home means not yet played."""
    return pd.DataFrame(
        {
            "fixture_date": [kickoff for kickoff, _ in rows],
            "goals_home": [goals for _, goals in rows],
        }
    )


def test_latest_result_date_ignores_scheduled_fixtures():
    fixtures = _fixtures(
        [
            ("2026-03-01T21:00:00+00:00", 2.0),
            ("2026-03-08T21:00:00+00:00", 0.0),
            ("2026-03-15T21:00:00+00:00", None),
        ]
    )

    assert latest_result_date(fixtures) == "2026-03-08"


def test_latest_result_date_is_none_before_a_ball_is_kicked():
    """A season whose fixtures are all scheduled has nothing to replay."""
    fixtures = _fixtures([("2027-01-20T21:00:00+00:00", None)])

    assert latest_result_date(fixtures) is None


def test_latest_result_date_uses_local_dates():
    """A 00:30 UTC kickoff is the previous evening in Brazil, as everywhere else
    in this codebase."""
    fixtures = _fixtures([("2026-03-09T00:30:00+00:00", 1.0)])

    assert latest_result_date(fixtures) == "2026-03-08"
    assert latest_result_date(fixtures) == dates_from_fixtures(fixtures)[-1]


def test_backfill_stops_at_the_last_played_date_by_default():
    """2026 is mid-season: 86 dates are scheduled, 64 have results."""
    dates = backfill_dates(2026)

    assert dates[-1] == "2026-09-05"
    assert len(dates) == 64


def test_an_explicit_to_date_still_wins():
    dates = backfill_dates(2026, to_date="2026-03-31")

    assert dates[-1] <= "2026-03-31"
    assert len(dates) < 64


def test_a_to_date_beyond_the_results_is_honoured():
    """Asking for future dates explicitly is allowed - it is only the DEFAULT
    that stops at the last result."""
    dates = backfill_dates(2026, to_date="2026-12-31")

    assert dates[-1] > "2026-09-05"


def test_from_date_still_slices_the_start():
    dates = backfill_dates(2026, from_date="2026-08-01")

    assert dates[0] >= "2026-08-01"
    assert dates[-1] == "2026-09-05"

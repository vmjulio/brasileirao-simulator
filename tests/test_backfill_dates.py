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
    utc_cutoff,
    utc_cutoffs,
)
from brasileirao_simulator.entrypoints.backfill import backfill_dates


def test_utc_cutoff_is_local_midnight_not_utc_midnight():
    """A 21:30 kickoff in Brazil is 00:30 UTC the next day. It belongs to the
    day it was played on, so it must fall inside that day's cutoff and
    outside the previous one."""
    kickoff = pd.Timestamp("2026-03-22T00:30:00Z")  # 2026-03-21, 21:30 local

    assert kickoff < utc_cutoff("2026-03-22")
    assert kickoff > utc_cutoff("2026-03-21")


def test_utc_cutoffs_matches_the_scalar_form():
    dates = pd.Series(["2026-03-21", "2026-03-22"])

    assert list(utc_cutoffs(dates)) == [utc_cutoff(d) for d in dates]


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


def _last_played_date(season: int = 2026) -> str:
    """The season's last date with a result, read from the fixtures rather
    than pinned: these tests are about where `backfill_dates` stops, and the
    answer moves every time the season file is refreshed."""
    import csv

    from brasileirao_simulator.config.settings import DATASETS_PATH

    with open(f"{DATASETS_PATH}/{season}/fixtures.csv", encoding="utf-8") as f:
        played = [r["fixture_date"] for r in csv.DictReader(f)
                  if r["fixture_status_short"] in ("FT", "AET", "PEN")]
    # Local date, as latest_result_date does: a 00:30 UTC kick-off is the
    # previous evening in Brazil.
    return str(pd.to_datetime(max(played)).tz_convert("America/Sao_Paulo").date())


def test_backfill_stops_at_the_last_played_date_by_default():
    """Mid-season, the default run stops at the last date with a result."""
    dates = backfill_dates(2026)

    assert dates[-1] == _last_played_date()
    assert len(dates) == len(set(dates)) and len(dates) > 50


def test_an_explicit_to_date_still_wins():
    dates = backfill_dates(2026, to_date="2026-03-31")

    assert dates[-1] <= "2026-03-31"
    assert len(dates) < len(backfill_dates(2026))


def test_a_to_date_beyond_the_results_is_honoured():
    """Asking for future dates explicitly is allowed - it is only the DEFAULT
    that stops at the last result."""
    dates = backfill_dates(2026, to_date="2026-12-31")

    assert dates[-1] > _last_played_date()


def test_from_date_still_slices_the_start():
    dates = backfill_dates(2026, from_date="2026-08-01")

    assert dates[0] >= "2026-08-01"
    assert dates[-1] == _last_played_date()

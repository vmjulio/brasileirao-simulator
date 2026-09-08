import pandas as pd


# Kickoff times are stored in UTC; Brazilian local time is UTC-3. tidy_fixtures.sql
# applies the same shift, so a date list derived here lines up with the dates the
# simulation filters on.
BRAZIL_UTC_OFFSET_HOURS = 3


def dates_from_fixtures(fixtures: pd.DataFrame) -> list[str]:
    """Every distinct local match date in a season's fixtures, ascending."""
    return _local_dates(fixtures)


def latest_result_date(fixtures: pd.DataFrame) -> str:
    """The last local date carrying a result, or None if none has been played.

    This is the natural end of a backfill. Simulating "as of" a date beyond it
    blanks nothing that is not already unknown, so it would repeat the latest
    real snapshot under a date that has not happened yet.
    """
    played = fixtures[fixtures["goals_home"].notnull()]
    dates = _local_dates(played)
    return dates[-1] if dates else None


def _local_dates(fixtures: pd.DataFrame) -> list[str]:
    if fixtures.empty:
        return []
    kickoffs = pd.to_datetime(fixtures["fixture_date"], utc=True)
    local_dates = kickoffs - pd.Timedelta(hours=BRAZIL_UTC_OFFSET_HOURS)
    return sorted(local_dates.dt.strftime("%Y-%m-%d").unique().tolist())

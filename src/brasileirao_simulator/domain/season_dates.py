import pandas as pd


# Kickoff times are stored in UTC; Brazilian local time is UTC-3. tidy_fixtures.sql
# applies the same shift, so a date list derived here lines up with the dates the
# simulation filters on.
#
# The offset is fixed, not a tz-database lookup. Brazil observed DST until 2019
# (UTC-2 in summer), so local times in the 2016-2018 summer months are an hour
# off here. That only moves a match to another calendar date if it kicked off
# between 00:00 and 01:00 local, which never happens, so every date this module
# produces is still right. Anything wanting local times to the hour needs a real
# timezone.
BRAZIL_UTC_OFFSET_HOURS = 3


def utc_cutoff(local_date: str) -> pd.Timestamp:
    """The UTC instant at which `local_date` begins in Brazil.

    Every "as of" question here is asked in local dates - the dates in
    `dates.json`, the backfill's frontier, the adapters' cutoff - while
    kickoffs are stored as UTC instants. Reading a local date as a UTC one
    puts the boundary three hours early and so drops the night games: a 21:00
    kickoff is 00:00 UTC the *next* day, and 12% of a Série A season kicks off
    at or after 21:00. Use this to compare the two.
    """
    return pd.Timestamp(local_date, tz="UTC") + pd.Timedelta(hours=BRAZIL_UTC_OFFSET_HOURS)


def utc_cutoffs(local_dates: pd.Series) -> pd.Series:
    """`utc_cutoff` over a column of local dates, for the frames that carry
    an `as_of_date` per row."""
    return pd.to_datetime(local_dates).dt.tz_localize("UTC") + pd.Timedelta(
        hours=BRAZIL_UTC_OFFSET_HOURS
    )


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

import pandas as pd


# Kickoff times are stored in UTC; Brazilian local time is UTC-3. tidy_fixtures.sql
# applies the same shift, so a date list derived here lines up with the dates the
# simulation filters on.
BRAZIL_UTC_OFFSET_HOURS = 3


def dates_from_fixtures(fixtures: pd.DataFrame) -> list[str]:
    """Every distinct local match date in a season's fixtures, ascending."""
    kickoffs = pd.to_datetime(fixtures["fixture_date"], utc=True)
    local_dates = kickoffs - pd.Timedelta(hours=BRAZIL_UTC_OFFSET_HOURS)
    return sorted(local_dates.dt.strftime("%Y-%m-%d").unique().tolist())

import json
import os

import pandas as pd
import pytest

from brasileirao_simulator.domain.season_dates import dates_from_fixtures


def test_dates_are_local_match_dates():
    """Kickoff times are stored in UTC; the season's dates are Brazilian local
    dates, which is UTC-3. A 2025-03-29T21:30Z kickoff is a 2025-03-29 match."""
    fixtures = pd.DataFrame(
        {"fixture_date": ["2025-03-29T21:30:00+00:00", "2025-03-30T00:30:00+00:00"]}
    )

    assert dates_from_fixtures(fixtures) == ["2025-03-29"]


def test_dates_are_sorted_and_deduplicated():
    fixtures = pd.DataFrame(
        {
            "fixture_date": [
                "2025-04-06T19:00:00+00:00",
                "2025-03-29T21:30:00+00:00",
                "2025-03-29T23:00:00+00:00",
            ]
        }
    )

    assert dates_from_fixtures(fixtures) == ["2025-03-29", "2025-04-06"]


from brasileirao_simulator.domain.season_data import SeasonData, SeasonMissingDataError


def test_loads_the_requested_season():
    season_data = SeasonData(2026)

    assert season_data.season == 2026
    assert len(season_data.fixtures) == 380
    assert (season_data.fixtures["league_season"] == 2026).all()


def test_previous_year_resolves_to_the_prior_season_folder():
    season_data = SeasonData(2026)

    assert (season_data.previous_year["league_season"] == 2025).all()


def test_dates_come_from_the_season_folder():
    season_data = SeasonData(2026)

    assert season_data.dates[0].startswith("2026-")
    assert season_data.dates == sorted(season_data.dates)


def test_missing_previous_season_names_the_path():
    """2024 is data-only: simulating it would need a 2023 folder that is absent."""
    with pytest.raises(SeasonMissingDataError) as excinfo:
        SeasonData(2024)

    assert "2023" in str(excinfo.value)


def test_optional_datasets_are_none_when_absent():
    """The bolao feature is dormant; a season without punters still simulates."""
    season_data = SeasonData(2026)

    assert season_data.punters is None
    assert season_data.doubles is None


def test_register_binds_frames_for_sql(tmp_path):
    import duckdb

    season_data = SeasonData(2026)
    con = duckdb.connect()
    season_data.register(con)

    assert con.sql("select count(*) as n from fixtures").df()["n"].iloc[0] == 380
    assert con.sql("select count(*) as n from previous_year").df()["n"].iloc[0] == 380

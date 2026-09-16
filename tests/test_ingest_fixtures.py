"""Gates for the ingest step: it must do nothing when nothing was played, and
must refuse anything it cannot explain.

The download and the sharding are not exercised here - they are one `aws s3 cp`
and a call into `shard_competitions`, both covered elsewhere. What is worth
pinning is the decision, because it is what makes the refresh safe to run on a
schedule.
"""

import pytest

from brasileirao_simulator.entrypoints.ingest_fixtures import (
    NothingToDo,
    decide,
    league_state,
    local_last_result_date,
)


def manifest(last="2026-09-13", rows=380, played=265, version=1, league="71"):
    return {
        "run_id": "20260914_133714",
        "schema_version": version,
        "leagues": {league: {"season": 2026, "rows": rows, "played": played,
                             "last_result_date": last}},
    }


def _fixtures(tmp_path, rows):
    """A fixtures CSV with just the two columns the date scan reads."""
    path = tmp_path / "fixtures.csv"
    lines = ["fixture_date,goals_home"]
    lines += [f"{kick},{goals}" for kick, goals in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------
# the quiet case, which is most runs
# --------------------------------------------------------------------------


def test_same_date_is_nothing_to_do():
    with pytest.raises(NothingToDo):
        decide(manifest(last="2026-09-13"), "2026-09-13")


def test_newer_published_date_is_work():
    published, reason = decide(manifest(last="2026-09-20"), "2026-09-13")
    assert published == "2026-09-20"
    assert "2026-09-13" in reason and "2026-09-20" in reason


def test_no_local_fixtures_is_work():
    published, reason = decide(manifest(last="2026-09-13"), None)
    assert published == "2026-09-13"
    assert "no local fixtures" in reason


# --------------------------------------------------------------------------
# the refusals
# --------------------------------------------------------------------------


def test_going_backwards_is_refused():
    """Publishing older data than we hold means something is wrong upstream.
    Overwriting would silently destroy results already simulated on."""
    with pytest.raises(ValueError, match="older than local"):
        decide(manifest(last="2026-09-01"), "2026-09-13")


def test_an_unknown_schema_version_is_refused():
    with pytest.raises(ValueError, match="schema_version"):
        league_state(manifest(version=2), 71)


def test_a_missing_league_is_refused():
    with pytest.raises(ValueError, match="no league 72"):
        league_state(manifest(), 72)


# --------------------------------------------------------------------------
# reading the local date - the other half of the comparison
# --------------------------------------------------------------------------


def test_local_date_is_the_brazilian_one(tmp_path):
    """A 21:30 kickoff in Brazil is 00:30 UTC the next day. The local date is
    the day it was played, which is the same rule the rest of the project
    follows - see domain/season_dates.py."""
    path = _fixtures(tmp_path, [("2026-09-14T00:30:00+00:00", "2")])
    assert local_last_result_date(path) == "2026-09-13"


def test_unplayed_fixtures_do_not_count(tmp_path):
    path = _fixtures(tmp_path, [("2026-09-13T22:00:00+00:00", "1"),
                                ("2026-09-20T22:00:00+00:00", "")])
    assert local_last_result_date(path) == "2026-09-13"


def test_a_season_with_no_results_yet(tmp_path):
    path = _fixtures(tmp_path, [("2026-09-20T22:00:00+00:00", "")])
    assert local_last_result_date(path) is None


def test_a_missing_file_is_not_an_error(tmp_path):
    assert local_last_result_date(str(tmp_path / "nope.csv")) is None

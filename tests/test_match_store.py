import os

import pandas as pd
import pytest

from brasileirao_simulator.domain.competitions import COMPETITIONS, Rule
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.teams import Teams

# COMPETITIONS with every round filter lifted - used only to check the
# fulltime-scoring rule against every PEN/AET row the shards contain,
# independent of which rounds COMPETITIONS itself admits. Round inclusion
# has its own test; this one isolates "is the score right" from "is the
# match in scope at all".
_EVERY_ROUND = {
    league_id: Rule(rule.name, from_round=None, division=rule.division)
    for league_id, rule in COMPETITIONS.items()
}

# The full T1.1 shard set admits this many matches: see
# test_dropped_statuses_are_absent_and_total_admitted_matches_the_profile
# below for the status breakdown, and .superpowers/sdd/t2.1-report.md for
# the breakdown by rule (status vs round, per league).
TOTAL_ADMITTED = 15869

# fixture_id 350750: Linense v Botafogo-PB, Copa do Brasil 2016, "1st Round".
# Dropped by COMPETITIONS[73].from_round (ROUND_OF_16).
COPA_1ST_ROUND_FIXTURE_ID = 350750

# fixture_id 44728: Copa do Brasil 2016, labelled "8th Finals" - one of the
# two spellings the ticket requires ROUND_OF_16 to admit.
COPA_8TH_FINALS_FIXTURE_ID = 44728

# fixture_id 713588: Copa do Brasil 2021, labelled "Round of 16" - the other
# spelling of the same stage.
COPA_ROUND_OF_16_FIXTURE_ID = 713588

# fixture_id 801186: Palmeiras 2 Flamengo 1 after extra time - the 2021
# Libertadores Final, played at a neutral venue in Montevideo - 1-1 at the
# 90th minute. The one PEN/AET row the ticket names explicitly.
PALMEIRAS_FLAMENGO_AET_FIXTURE_ID = 801186


@pytest.fixture(scope="module")
def store() -> MatchStore:
    return MatchStore()


@pytest.fixture(scope="module")
def every_round_store() -> MatchStore:
    return MatchStore(rules=_EVERY_ROUND)


def test_loading_twice_is_idempotent(store):
    other = MatchStore()

    assert store.matches.equals(other.matches)


def test_copa_do_brasil_early_round_is_dropped_but_round_of_16_labels_are_kept(store):
    matches = store.matches

    assert COPA_1ST_ROUND_FIXTURE_ID not in matches["fixture_id"].values

    round_of_16 = matches[matches["fixture_id"] == COPA_ROUND_OF_16_FIXTURE_ID]
    eighth_finals = matches[matches["fixture_id"] == COPA_8TH_FINALS_FIXTURE_ID]
    assert len(round_of_16) == 1
    assert len(eighth_finals) == 1
    assert round_of_16["round"].iloc[0] == "Round of 16"
    assert eighth_finals["round"].iloc[0] == "8th Finals"


def test_league_absent_from_competitions_is_ignored_even_if_its_shard_exists(tmp_path):
    """A shard for a league id MatchStore does not know about must not leak
    in, even though nothing about reading the file itself would fail."""
    root = tmp_path / "competitions"

    known_dir = root / "71"
    known_dir.mkdir(parents=True)
    known_dir.joinpath("2025.csv").write_text(_fake_shard_csv(league_id=71, fixture_id=1))

    unlisted_dir = root / "999"
    unlisted_dir.mkdir(parents=True)
    unlisted_dir.joinpath("2025.csv").write_text(_fake_shard_csv(league_id=999, fixture_id=2))

    temp_store = MatchStore(root=str(root))
    matches = temp_store.matches

    assert set(matches["league_id"]) == {71}
    assert len(matches) == 1


def test_all_165_pen_and_aet_rows_are_scored_from_fulltime_not_goals(every_round_store):
    """Round inclusion is a separate rule with its own test
    (test_copa_do_brasil_early_round_is_dropped_but_round_of_16_labels_are_kept);
    this fixture lifts it so the 165 PEN/AET rows the shards contain - many
    of them in rounds COMPETITIONS itself would drop - are all in scope to
    check the fulltime-scoring rule against."""
    pen_aet = every_round_store.matches[every_round_store.matches["status"].isin(["PEN", "AET"])]

    assert len(pen_aet) == 165

    palmeiras_flamengo = pen_aet[pen_aet["fixture_id"] == PALMEIRAS_FLAMENGO_AET_FIXTURE_ID]
    assert len(palmeiras_flamengo) == 1
    row = palmeiras_flamengo.iloc[0]
    assert row["home_goals"] == 1
    assert row["away_goals"] == 1
    assert row["home_id"] == Teams.PALMEIRAS
    assert row["away_id"] == Teams.FLAMENGO
    # 2021 Libertadores final, played in Montevideo - a genuine neutral
    # site, and a real-world check on the is_neutral heuristic.
    assert row["is_neutral"]


def test_pen_and_aet_rows_admitted_by_the_real_rules_are_also_scored_from_fulltime(store):
    """The subset of the 165 that COMPETITIONS' round filters actually
    admit must carry the same fulltime scores."""
    admitted_pen_aet = store.matches[store.matches["status"].isin(["PEN", "AET"])]

    assert 0 < len(admitted_pen_aet) < 165

    palmeiras_flamengo = admitted_pen_aet[
        admitted_pen_aet["fixture_id"] == PALMEIRAS_FLAMENGO_AET_FIXTURE_ID
    ]
    assert len(palmeiras_flamengo) == 1
    row = palmeiras_flamengo.iloc[0]
    assert row["home_goals"] == 1
    assert row["away_goals"] == 1


def test_dropped_statuses_are_absent_and_total_admitted_matches_the_profile(store):
    matches = store.matches

    assert not set(matches["status"]) & {"CANC", "NS", "PST", "TBD", "AWD"}
    assert set(matches["status"]) == {"FT", "AET", "PEN"}

    print(f"MatchStore admits {len(matches)} matches")
    assert len(matches) == TOTAL_ADMITTED


def test_every_admitted_match_has_admitted_by_set(store):
    admitted_by = store.matches["admitted_by"]

    assert admitted_by.notna().all()
    assert (admitted_by.str.len() > 0).all()
    assert set(admitted_by) == {rule.name for rule in COMPETITIONS.values()}


def test_coverage_reports_five_competitions_and_serie_a_clubs_above_38(store):
    coverage = store.coverage(2025)

    assert set(coverage["competitions"]) == set(COMPETITIONS)
    assert len(coverage["competitions"]) == 5

    palmeiras_matches = coverage["clubs"][Teams.PALMEIRAS]
    assert palmeiras_matches > 38


def test_before_excludes_matches_on_or_after_the_cutoff(store):
    cutoff = "2025-06-01"
    before = store.before(cutoff)

    kickoff = pd.to_datetime(before["fixture_date"], utc=True, format="mixed")
    assert (kickoff < pd.Timestamp(cutoff, tz="UTC")).all()
    assert len(before) < len(store.matches)


def _fake_shard_csv(league_id: int, fixture_id: int) -> str:
    """A one-row shard with just enough columns for MatchStore to read,
    admitted under COMPETITIONS[71] (Série A, from_round=None) whenever
    `league_id` is 71."""
    header = (
        "fixture_id,fixture_date,fixture_venue_id,fixture_status_short,"
        "league_id,league_season,league_round,teams_home_id,teams_home_name,"
        "teams_away_id,teams_away_name,score_fulltime_home,score_fulltime_away,"
        "goals_home,goals_away"
    )
    row = (
        f"{fixture_id},2025-05-01T21:00:00+00:00,101,FT,"
        f"{league_id},2025,Regular Season - 1,{Teams.PALMEIRAS},Palmeiras,"
        f"{Teams.FLAMENGO},Flamengo,2,1,2,1"
    )
    return header + os.linesep + row + os.linesep

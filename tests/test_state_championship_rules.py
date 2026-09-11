"""state-championships: the extra rule sets its arms read, and that the
default rules are left alone."""

from brasileirao_simulator.domain.competitions import (
    COMPETITIONS,
    STATE_CHAMPIONSHIPS,
    with_every_copa_round,
    with_state_championships,
)


def test_the_default_rules_admit_no_state_championship():
    assert not set(STATE_CHAMPIONSHIPS) & set(COMPETITIONS)


def test_state_rules_add_all_thirteen_leagues_every_round_on_top_of_the_default():
    rules = with_state_championships()
    assert set(rules) == set(COMPETITIONS) | set(STATE_CHAMPIONSHIPS)
    assert len(STATE_CHAMPIONSHIPS) == 13
    assert all(rules[league].from_round is None for league in STATE_CHAMPIONSHIPS)
    assert all(rules[league] == COMPETITIONS[league] for league in COMPETITIONS)


def test_every_copa_round_changes_only_the_copa_do_brasil_cut():
    rules = with_every_copa_round()
    assert rules[73].from_round is None and COMPETITIONS[73].from_round is not None
    assert all(rules[league] == COMPETITIONS[league] for league in COMPETITIONS if league != 73)


def test_the_two_compose():
    rules = with_state_championships(with_every_copa_round())
    assert rules[73].from_round is None and set(STATE_CHAMPIONSHIPS) <= set(rules)


def _shard(root, league, season, rows):
    import csv
    import os

    os.makedirs(root / str(league), exist_ok=True)
    columns = ["fixture_id", "fixture_date", "fixture_venue_id", "fixture_status_short", "league_id", "league_season",
               "league_round", "teams_home_id", "teams_home_name", "teams_away_id", "teams_away_name",
               "score_fulltime_home", "score_fulltime_away", "goals_home", "goals_away"]
    with open(root / str(league) / f"{season}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for fid, day, venue, rnd, home, away in rows:
            writer.writerow({"fixture_id": fid, "fixture_date": f"{season}-{day}T20:00:00+00:00", "fixture_venue_id": venue,
                             "fixture_status_short": "FT", "league_id": league, "league_season": season,
                             "league_round": rnd, "teams_home_id": home, "teams_home_name": f"c{home}",
                             "teams_away_id": away, "teams_away_name": f"c{away}", "score_fulltime_home": 1,
                             "score_fulltime_away": 0, "goals_home": 1, "goals_away": 0})


def test_admitting_state_matches_does_not_move_a_clubs_usual_ground(tmp_path):
    from brasileirao_simulator.domain.match_store import MatchStore

    # Club 1 hosts Série A at venue 100 twice, but hosts three state matches at venue 200.
    _shard(tmp_path, 71, 2021, [(1, "06-01", 100, "Regular Season - 1", 1, 2), (2, "06-08", 100, "Regular Season - 2", 1, 3)])
    _shard(tmp_path, 475, 2021, [(10 + i, f"02-0{i + 1}", 200, "Regular Season - 1", 1, 4 + i) for i in range(3)])
    default = MatchStore(root=str(tmp_path)).matches.set_index("fixture_id")
    state = MatchStore(root=str(tmp_path), rules=with_state_championships()).matches.set_index("fixture_id")
    assert not default.loc[[1, 2], "is_neutral"].any()
    assert not state.loc[[1, 2], "is_neutral"].any()      # still club 1's ground
    assert state.loc[[10, 11, 12], "is_neutral"].all()     # the state venue is not

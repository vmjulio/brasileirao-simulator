"""Gates for the elo-snapshots ticket: `ratings_as_of` and `team_strength`
in `domain/elo_snapshots.py`, and their registration into DuckDB.

Fixture data: three clubs (1, 2, 3), six hand-built matches across two
kickoff dates (2025-03-01 = D1, 2025-03-10 = D2), mixing league ids 71 and
73. Club 3 has no match before D1, which is what exercises the "no prior
match -> absent" rule. `replay` is not implemented on this branch, so the
history is built by hand rather than produced by it - see the module
docstring of `elo.py` and this ticket's brief.

  fixture 1 (D1, league 71): 1 (home) v 2 -> elo_after 1=1516, 2=1484
  fixture 2 (D1, league 71): 2 (home) v 1 -> elo_after 2=1470, 1=1530
  fixture 3 (D2, league 73): 1 (home) v 3 -> elo_after 1=1540, 3=1290
  fixture 4 (D2, league 71): 2 (home) v 3 -> elo_after 2=1480, 3=1300
  fixture 5 (D2, league 73): 3 (home) v 1 -> elo_after 3=1310, 1=1550
  fixture 6 (D2, league 71): 1 (home) v 2 -> elo_after 1=1560, 2=1490

After D1 only: team 1 = 1530 (2 matches), team 2 = 1470 (2 matches), team 3
absent. After D2: team 1 = 1560 (5 matches, leagues {71, 73}), team 2 = 1490
(4 matches, league {71}), team 3 = 1310 (3 matches, leagues {71, 73}).
"""

import glob
import os

import duckdb
import pandas as pd

from brasileirao_simulator.config.settings import QUERIES_PATH
from brasileirao_simulator.domain import elo as elo_module
from brasileirao_simulator.domain.elo import EloHistory, EloParams
from brasileirao_simulator.domain.elo_snapshots import (
    ratings_as_of,
    register_team_strength,
    team_strength,
)

D1 = "2025-03-01"
D2 = "2025-03-10"

_ROWS = [
    # fixture_id, fixture_date, league_id, team_id, elo_before, elo_after, opponent_id, is_home, matches_used
    (1, D1, 71, 1, 1500.0, 1516.0, 2, True, 0),
    (1, D1, 71, 2, 1500.0, 1484.0, 1, False, 0),
    (2, D1, 71, 2, 1484.0, 1470.0, 1, True, 1),
    (2, D1, 71, 1, 1516.0, 1530.0, 2, False, 1),
    (3, D2, 73, 1, 1530.0, 1540.0, 3, True, 2),
    (3, D2, 73, 3, 1300.0, 1290.0, 1, False, 0),
    (4, D2, 71, 2, 1470.0, 1480.0, 3, True, 2),
    (4, D2, 71, 3, 1290.0, 1300.0, 2, False, 1),
    (5, D2, 73, 3, 1300.0, 1310.0, 1, True, 2),
    (5, D2, 73, 1, 1540.0, 1550.0, 3, False, 3),
    (6, D2, 71, 1, 1550.0, 1560.0, 2, True, 4),
    (6, D2, 71, 2, 1480.0, 1490.0, 1, False, 3),
]


def _history() -> EloHistory:
    frame = pd.DataFrame(
        _ROWS,
        columns=[
            "fixture_id",
            "fixture_date",
            "league_id",
            "team_id",
            "elo_before",
            "elo_after",
            "opponent_id",
            "is_home",
            "matches_used",
        ],
    )
    frame = frame.assign(season=2025, is_neutral=False)
    return EloHistory(frame, EloParams())


# ---------------------------------------------------------------------------
# ratings_as_of - re-export from elo.py
# ---------------------------------------------------------------------------


def test_ratings_as_of_is_importable_from_elo_and_is_the_elo_snapshots_implementation():
    assert elo_module.ratings_as_of is ratings_as_of


# ---------------------------------------------------------------------------
# No leakage - the ticket's gate.
# ---------------------------------------------------------------------------


def test_ratings_as_of_excludes_a_match_on_its_own_kickoff_date():
    history = _history()
    # No match strictly before D1 itself - everyone is absent.
    assert ratings_as_of(history, D1) == {}


def test_ratings_as_of_includes_a_match_the_day_after_its_kickoff_date():
    history = _history()
    result = ratings_as_of(history, "2025-03-02")
    assert result == {1: 1530.0, 2: 1470.0}
    assert 3 not in result


def test_team_strength_matches_used_excludes_a_match_on_its_own_kickoff_date():
    history = _history()
    frame = team_strength(history, D1)
    assert frame.empty


def test_team_strength_matches_used_includes_a_match_the_day_after_its_kickoff_date():
    history = _history()
    frame = team_strength(history, "2025-03-02").set_index("team_id")
    assert frame.loc[1, "matches_used"] == 2
    assert frame.loc[2, "matches_used"] == 2
    assert 3 not in frame.index


# ---------------------------------------------------------------------------
# Absence of a club with no prior match.
# ---------------------------------------------------------------------------


def test_club_with_no_prior_match_is_absent_from_ratings_as_of():
    history = _history()
    result = ratings_as_of(history, "2025-03-02")
    assert 3 not in result


def test_club_with_no_prior_match_is_absent_from_team_strength():
    history = _history()
    frame = team_strength(history, "2025-03-02")
    assert 3 not in set(frame["team_id"])


# ---------------------------------------------------------------------------
# team_strength shape and content, after both dates have played out.
# ---------------------------------------------------------------------------


def test_team_strength_columns_and_dtypes():
    history = _history()
    frame = team_strength(history, "2025-03-11")
    assert list(frame.columns) == ["team_id", "as_of_date", "elo", "matches_used", "competitions_used"]
    assert frame["team_id"].dtype == "int64"
    assert frame["as_of_date"].dtype == "object"
    assert frame["elo"].dtype == "float64"
    assert frame["matches_used"].dtype == "int64"
    assert frame["competitions_used"].dtype == "object"


def test_team_strength_values_after_both_dates():
    history = _history()
    frame = team_strength(history, "2025-03-11").set_index("team_id")

    assert frame.loc[1, "elo"] == 1560.0
    assert frame.loc[1, "matches_used"] == 5
    assert frame.loc[1, "competitions_used"] == "71,73"

    assert frame.loc[2, "elo"] == 1490.0
    assert frame.loc[2, "matches_used"] == 4
    assert frame.loc[2, "competitions_used"] == "71"

    assert frame.loc[3, "elo"] == 1310.0
    assert frame.loc[3, "matches_used"] == 3
    assert frame.loc[3, "competitions_used"] == "71,73"


def test_team_strength_as_of_date_echoed_verbatim():
    history = _history()
    frame = team_strength(history, "2025-03-11")
    assert set(frame["as_of_date"]) == {"2025-03-11"}


def test_team_strength_competitions_used_sorted_and_deduplicated():
    # Reorder team 1's rows so its league ids do not arrive pre-sorted -
    # competitions_used must still come out "71,73", not raw arrival order.
    history = _history()
    reordered = history.ratings.iloc[[4, 9, 0, 3]].reset_index(drop=True)  # fixtures 3,5,1,2 for team 1
    others = history.ratings[history.ratings["team_id"] != 1]
    frame = pd.concat([reordered, others], ignore_index=True)
    shuffled_history = EloHistory(frame, EloParams())

    result = team_strength(shuffled_history, "2025-03-11").set_index("team_id")
    assert result.loc[1, "competitions_used"] == "71,73"


def test_team_strength_row_order_by_team_id():
    history = _history()
    frame = team_strength(history, "2025-03-11")
    assert list(frame["team_id"]) == [1, 2, 3]


def test_ratings_as_of_agrees_with_team_strength():
    history = _history()
    as_of_date = "2025-03-11"

    from_ratings_as_of = ratings_as_of(history, as_of_date)
    from_team_strength = dict(
        zip(*team_strength(history, as_of_date)[["team_id", "elo"]].to_dict("list").values())
    )

    assert from_ratings_as_of == from_team_strength


# ---------------------------------------------------------------------------
# Registration into DuckDB.
# ---------------------------------------------------------------------------


def test_register_team_strength_row_count():
    history = _history()
    frame = team_strength(history, "2025-03-11")

    con = duckdb.connect()
    register_team_strength(con, frame)
    count = con.sql("select count(*) as n from team_strength").df()["n"].iloc[0]
    assert count == len(frame) == 3


def test_register_team_strength_twice_replaces_rather_than_errors():
    history = _history()
    wide = team_strength(history, "2025-03-11")
    narrow = team_strength(history, "2025-03-02")  # 2 rows, not 3

    con = duckdb.connect()
    register_team_strength(con, wide)
    register_team_strength(con, narrow)  # must not raise

    count = con.sql("select count(*) as n from team_strength").df()["n"].iloc[0]
    assert count == len(narrow) == 2


# ---------------------------------------------------------------------------
# Relation set unchanged - no existing query reads team_strength.
#
# The equivalence gate (tests/test_equivalence_gates.py, gate 2) proves that
# *constructing* a MatchStore never changes the relations a `Tables`
# connection registers - it is a runtime before/after comparison, not a
# static parse, and it does not itself register anything under the name
# "team_strength" to compare against. Explicitly registering team_strength
# on a Tables-owned connection would trivially grow that connection's own
# relation set (that is the whole point of `register_team_strength`), so
# re-running gate 2's comparison as-is would not be a meaningful check here.
#
# What actually matters for this ticket - "no existing query's relation set
# changes" - is that no .sql file the codebase reads mentions team_strength
# at all, so no query starts consuming it as a side effect of the table
# merely existing in a shared connection. That is a static property of the
# SQL files, so it is checked by grepping every .sql under QUERIES_PATH.
# ---------------------------------------------------------------------------


def test_no_existing_sql_query_references_team_strength():
    sql_files = glob.glob(os.path.join(QUERIES_PATH, "*.sql"))
    assert len(sql_files) > 0

    offenders = []
    for path in sql_files:
        with open(path) as f:
            if "team_strength" in f.read():
                offenders.append(path)

    assert offenders == []


def test_registering_team_strength_does_not_disturb_other_relations_already_on_the_connection():
    """A narrower, still-meaningful version of the equivalence gate's intent:
    registering team_strength onto a connection that already has other
    relations neither removes nor mutates them - it only adds its own name.
    """
    con = duckdb.connect()
    con.register("fixtures", pd.DataFrame({"a": [1, 2]}))
    before = set(con.sql("select table_name from information_schema.tables").df()["table_name"])

    history = _history()
    register_team_strength(con, team_strength(history, "2025-03-11"))

    after = set(con.sql("select table_name from information_schema.tables").df()["table_name"])
    assert after == before | {"team_strength"}
    assert con.sql("select count(*) as n from fixtures").df()["n"].iloc[0] == 2

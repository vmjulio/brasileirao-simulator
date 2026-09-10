"""`ratings_as_of` and `team_strength`: reading an `EloHistory` as of a date.

WHY A SEPARATE MODULE. `elo.py` owns `EloHistory`/`replay`, developed in a
parallel worktree on this same ticket set. This module only *reads* a
history that already exists - it never builds one - so it takes `history`
duck-typed (`.ratings` DataFrame, `.params`) rather than importing
`EloHistory` from `elo.py`, which would create a circular import once
`elo.py` re-exports this module's functions. See the FILE OWNERSHIP note in
`elo.py`'s module docstring.

NO LEAKAGE. Both functions below read only rows strictly before `as_of_date`
(UTC), matching `MatchStore.before`'s cutoff exactly (`pd.Timestamp(as_of_date,
tz="UTC")` compared against `pd.to_datetime(..., utc=True, format="mixed")`)
so a forecast built from `team_strength` never sees a match's own result.

DAILY-SNAPSHOT PATTERN. `new-elo.ipynb` cell 18 computed this in SQL: for
each club, sort its rows by kickoff and take the last one before the cutoff.
`ratings_as_of`/`team_strength` do the same thing in pandas - sort once,
`groupby(...).tail(1)` for the latest row and `.size()` for the count - so
there is no per-club Python loop.
"""

from typing import TYPE_CHECKING

import duckdb
import pandas as pd

if TYPE_CHECKING:
    from brasileirao_simulator.domain.elo import EloHistory


def ratings_as_of(history: "EloHistory", as_of_date: str) -> dict[int, float]:
    """`{team_id: elo}` using only matches strictly before `as_of_date`
    (UTC). A club with no admitted match strictly before `as_of_date` is
    absent from the returned dict rather than defaulting to its seed.
    """
    before = _rows_before(history.ratings, as_of_date)
    if before.empty:
        return {}

    latest = before.groupby("team_id", sort=False).tail(1)
    return {
        int(team_id): float(elo)
        for team_id, elo in zip(latest["team_id"], latest["elo_after"])
    }


def team_strength(history: "EloHistory", as_of_date: str) -> pd.DataFrame:
    """One row per club with a match strictly before `as_of_date`:
    `team_id`, `as_of_date` (echoed back exactly as given), `elo` (that
    club's `elo_after` from its last such match), `matches_used` (how many
    such matches it has), and `competitions_used` (its distinct `league_id`s
    over those matches, comma-joined in sorted order, e.g. `"71,73"`).
    Sorted by `team_id`.
    """
    before = _rows_before(history.ratings, as_of_date)
    if before.empty:
        return pd.DataFrame(
            columns=["team_id", "as_of_date", "elo", "matches_used", "competitions_used"]
        ).astype(
            {
                "team_id": "int64",
                "as_of_date": "object",
                "elo": "float64",
                "matches_used": "int64",
                "competitions_used": "object",
            }
        )

    grouped = before.groupby("team_id", sort=False)
    latest_elo = grouped["elo_after"].last()
    matches_used = grouped.size()
    competitions_used = grouped["league_id"].agg(
        lambda ids: ",".join(str(i) for i in sorted(set(ids)))
    )

    frame = pd.DataFrame(
        {
            "team_id": latest_elo.index,
            "as_of_date": as_of_date,
            "elo": latest_elo.to_numpy(dtype="float64"),
            "matches_used": matches_used.reindex(latest_elo.index).to_numpy(dtype="int64"),
            "competitions_used": competitions_used.reindex(latest_elo.index).to_numpy(),
        }
    )
    return frame.sort_values("team_id", kind="mergesort").reset_index(drop=True)


def register_team_strength(
    connection: duckdb.DuckDBPyConnection, frame: pd.DataFrame, name: str = "team_strength"
) -> None:
    """Bind `frame` onto `name` in `connection` - the same
    `DuckDBPyConnection.register` mechanism `SeasonData.register` and
    `Tables.enriched_tidy_fixtures` already use to bind their own frames.
    Registering twice under the same name replaces the relation rather than
    erroring (DuckDB's own `register` behaviour).
    """
    connection.register(name, frame)


def _rows_before(ratings: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    """`ratings` rows strictly before `as_of_date` (UTC), sorted by
    `(fixture_date, fixture_id)` - the same cutoff `MatchStore.before`
    applies to matches, and `EloHistory.ratings`'s own chronological order -
    so a later `.tail(1)`/`.last()` per club picks the most recent row
    regardless of the order rows arrived in."""
    cutoff = pd.Timestamp(as_of_date, tz="UTC")
    kickoff = pd.to_datetime(ratings["fixture_date"], utc=True, format="mixed")
    before = ratings[kickoff < cutoff]
    return before.sort_values(["fixture_date", "fixture_id"], kind="mergesort")

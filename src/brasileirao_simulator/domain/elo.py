"""Elo ratings for every club encountered - league sides, cup-only state
clubs, foreign opposition - replayed in chronological order over the whole
`MatchStore`.

NO WINDOW. Elo's memory is `K`: a club with thirty extra cup matches gets
thirty extra updates, not a re-weighted average. There is no lookback and no
per-competition cutoff to tune.

REPLAY, NOT INCREMENT. The full store is a few thousand matches, so a
chronological replay from scratch is milliseconds and idempotent by
construction - no ledger, no "already applied" bookkeeping, and a corrected
score upstream just flows through on the next replay. See
docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md,
section "Elo", for the full design rationale.

FILE OWNERSHIP (parallel tickets - read before editing this file):
  - elo-replay      owns `replay` in THIS file (domain/elo.py).
  - elo-division-seeds implements `seed_for` in `domain/elo_seeds.py` and
    this file re-exports it.
  - elo-snapshots   implements `ratings_as_of` and the `team_strength` table
    in `domain/elo_snapshots.py` and this file re-exports them.
Each ticket edits only its own file; do not touch another ticket's stub body
or its dedicated module.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

import pandas as pd

from brasileirao_simulator.domain.elo_snapshots import ratings_as_of
from brasileirao_simulator.domain.match_store import MatchStore

# A club's seed on first appearance, keyed by the highest division it is
# found in that season (1 = Série A, 2 = Série B, 3 = Série C/D or
# state-only) or "foreign" for clubs outside the Brazilian pyramid. See
# `seed_for` for how a club's division is derived from league ids.
SEED_BY_DIVISION = MappingProxyType({1: 1500.0, 2: 1400.0, 3: 1300.0, "foreign": 1450.0})


@dataclass(frozen=True)
class EloParams:
    """Elo replay parameters. Frozen and immutable so one instance can be
    shared safely across a replay and every snapshot read from it.

    k: the update's overall scale, multiplied by `margin_multiplier` and the
        result surprise - see the module-level design doc for the update
        formula.
    home_advantage: rating points added to the home side's expectation
        (never its logged rating) for a non-neutral match; zero for a
        neutral one.
    seeds: division -> starting rating, keyed as in `SEED_BY_DIVISION`. A
        `MappingProxyType` default so no caller can mutate the shared
        default by mutating one instance's `seeds`.
    margin_ladder: the three goal-difference multipliers `margin_multiplier`
        reads for |goal difference| in {0 or 1, 2, 3}; beyond 3 it
        extrapolates from `margin_ladder[2]` - see `margin_multiplier`.
    """

    k: float = 20.0
    home_advantage: float = 85.0
    seeds: Mapping = field(default_factory=lambda: SEED_BY_DIVISION)
    margin_ladder: tuple[float, float, float] = (1.0, 1.75, 2.5)


@dataclass(frozen=True)
class EloHistory:
    """The output of `replay`: every club's rating trajectory over the whole
    `MatchStore`, plus the `EloParams` it was produced with.

    `ratings` has one row per club per admitted match it played (so two rows
    per match, one per side), sorted by `(fixture_date, fixture_id, is_home
    desc)` - the same chronological order `replay` processes the store in.
    Columns:

      fixture_id     - the match this row's update came from.
      fixture_date   - the match's kickoff (UTC), the sort key.
      season         - the match's season, as in `MatchStore.matches`.
      league_id      - the match's competition, as in `MatchStore.matches`.
      team_id        - the club this row's rating belongs to.
      elo_before     - the club's rating immediately before this match. For
                       a club's first row this equals its division seed
                       (see `seed_for`).
      elo_after      - the club's rating immediately after this match's
                       update is applied.
      opponent_id    - the other club in this match.
      is_home        - whether `team_id` was the home side in this match.
      is_neutral     - whether this match was played at a neutral venue, as
                       in `MatchStore.matches` (no home advantage applied
                       when true).
      matches_used   - count of this club's admitted matches strictly
                       before this one (0 on its first row).

    `params` is the `EloParams` the replay was run with, so a caller holding
    only an `EloHistory` can still see what produced it.
    """

    ratings: pd.DataFrame
    params: EloParams


# Column order EloHistory.ratings is built and returned in - must match the
# order the EloHistory docstring lists them in (a gate in its own right).
_HISTORY_COLUMNS = [
    "fixture_id",
    "fixture_date",
    "season",
    "league_id",
    "team_id",
    "elo_before",
    "elo_after",
    "opponent_id",
    "is_home",
    "is_neutral",
    "matches_used",
]

# dtypes for every column except fixture_date, which is carried through
# exactly as MatchStore gives it (see EloHistory's docstring).
_HISTORY_DTYPES = {
    "fixture_id": "int64",
    "season": "int64",
    "league_id": "int64",
    "team_id": "int64",
    "elo_before": "float64",
    "elo_after": "float64",
    "opponent_id": "int64",
    "is_home": "bool",
    "is_neutral": "bool",
    "matches_used": "int64",
}


def replay(store: MatchStore, params: EloParams = EloParams()) -> EloHistory:
    """Chronological Elo replay over every match in `store.matches`.

    Implemented by elo-replay. Contract: process `store.matches` in
    `(fixture_date, fixture_id)` order (the order `MatchStore` already
    sorts to); seed each club from `seed_for` on its first appearance;
    apply the standard logistic expectation with `params.home_advantage`
    added to the home side only when the match is not neutral; update both
    sides by `params.k * margin_multiplier(goal_difference, params.margin_ladder)
    * (actual - expected)`, symmetric so an away win moves ratings exactly
    as much as the same-margin home win; score every match from
    `home_goals`/`away_goals` (already 90-minute results per
    `MatchStore`'s contract). Must be a pure function of `store` and
    `params` - replaying the same store twice returns bit-identical
    `EloHistory.ratings`.
    """
    matches = store.matches.sort_values(["fixture_date", "fixture_id"], kind="mergesort")

    ratings: dict[int, float] = {}
    matches_used: dict[int, int] = {}
    rows = []

    for match in matches.itertuples(index=False):
        home_id, away_id = int(match.home_id), int(match.away_id)
        season = int(match.season)

        if home_id not in ratings:
            ratings[home_id] = seed_for(home_id, season, store, params)
            matches_used[home_id] = 0
        if away_id not in ratings:
            ratings[away_id] = seed_for(away_id, season, store, params)
            matches_used[away_id] = 0

        r_home, r_away = ratings[home_id], ratings[away_id]
        is_neutral = bool(match.is_neutral)
        expected_home = _expected_home(r_home, r_away, is_neutral, params.home_advantage)

        home_goals, away_goals = match.home_goals, match.away_goals
        if home_goals > away_goals:
            actual_home = 1.0
        elif home_goals < away_goals:
            actual_home = 0.0
        else:
            actual_home = 0.5

        margin = margin_multiplier(int(home_goals - away_goals), params.margin_ladder)
        delta = params.k * margin * (actual_home - expected_home)
        new_r_home, new_r_away = r_home + delta, r_away - delta

        rows.append(
            _history_row(match, home_id, r_home, new_r_home, away_id, True, is_neutral, matches_used[home_id])
        )
        rows.append(
            _history_row(match, away_id, r_away, new_r_away, home_id, False, is_neutral, matches_used[away_id])
        )

        ratings[home_id], ratings[away_id] = new_r_home, new_r_away
        matches_used[home_id] += 1
        matches_used[away_id] += 1

    ratings_frame = pd.DataFrame(rows, columns=_HISTORY_COLUMNS)
    ratings_frame = ratings_frame.astype(_HISTORY_DTYPES)
    ratings_frame = ratings_frame.sort_values(
        ["fixture_date", "fixture_id", "is_home"], ascending=[True, True, False], kind="mergesort"
    ).reset_index(drop=True)

    return EloHistory(ratings=ratings_frame, params=params)


def _expected_home(r_home: float, r_away: float, is_neutral: bool, home_advantage: float) -> float:
    """The logistic win expectation for the home side. `home_advantage`
    enters the expectation only, never a stored rating, and only when the
    match is not neutral."""
    h = 0.0 if is_neutral else home_advantage
    return 1.0 / (1.0 + 10 ** (-((r_home + h) - r_away) / 400.0))


def _history_row(match, team_id, elo_before, elo_after, opponent_id, is_home, is_neutral, used) -> dict:
    """One `EloHistory.ratings` row for `team_id`'s side of `match`."""
    return {
        "fixture_id": int(match.fixture_id),
        "fixture_date": match.fixture_date,
        "season": int(match.season),
        "league_id": int(match.league_id),
        "team_id": team_id,
        "elo_before": elo_before,
        "elo_after": elo_after,
        "opponent_id": opponent_id,
        "is_home": is_home,
        "is_neutral": is_neutral,
        "matches_used": used,
    }


def seed_for(team_id: int, season: int, store: MatchStore, params: EloParams) -> float:
    """A club's starting Elo rating the first time it is encountered in
    `season`.

    Implemented by elo-division-seeds (see also `domain/elo_seeds.py`,
    which owns the implementation this function re-exports). Contract: the
    seed is set by the highest division the club appears in during
    `season`, derived from which league ids it played in that season -
    never a hand-typed table. League id 71 (Série A) maps to division 1,
    league id 72 (Série B) to division 2; any other domestic league id
    (state championships, Copa do Brasil, cup competitions) maps to
    division 3, the lowest tier, including a club seen only as a cup
    opponent; a club whose matches that season are all against foreign
    confederations takes the `"foreign"` seed. The returned value is always
    one of `params.seeds`'s values - never a fallback absent from that
    mapping.
    """
    raise NotImplementedError("elo-division-seeds")


def margin_multiplier(goal_difference: int, ladder: tuple[float, float, float]) -> float:
    """The goal-difference multiplier `G` applied to `k`, symmetric in the
    sign of `goal_difference` (a 4-0 away win moves ratings exactly as much
    as a 4-0 home win).

    `d = abs(goal_difference)`:
      d in {0, 1} -> ladder[0]
      d == 2      -> ladder[1]
      d == 3      -> ladder[2]
      d > 3       -> ladder[2] * (11 + d) / 14

    The `d > 3` branch keeps rising smoothly past the ladder's last rung
    rather than flattening at `ladder[2]`; it is continuous with `d == 3`
    exactly when `ladder[2] == ladder[2] * 14 / 14`, i.e. always, by
    construction (14 = 11 + 3).
    """
    d = abs(goal_difference)
    if d <= 1:
        return ladder[0]
    if d == 2:
        return ladder[1]
    if d == 3:
        return ladder[2]
    return ladder[2] * (11 + d) / 14

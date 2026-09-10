"""A club's Elo starting rating on first appearance in a season, derived
from the highest division it plays in that season - never a hand-typed
club table. Implements `seed_for`, re-exported by `domain/elo.py` (see that
module's FILE OWNERSHIP note); also exposes `division_table` for
snapshot/report work that wants to inspect the season -> team_id -> tier
mapping directly.

WHY A SEPARATE MODULE. `elo.py` imports this module to re-export
`seed_for`, so this module must not import anything from `elo.py` - that
would be a circular import. It only needs `MatchStore`'s shape (`matches`
with `season`, `league_id`, `home_id`, `away_id` columns) and reads
`params.seeds` as a plain `Mapping`, never importing `EloParams` itself.

DIVISION RULE. For `(team_id, season)`, look at every league id the club
appears in (home or away) that season in `store.matches`:
  - any match in league 71 (Serie A)        -> tier 1
  - else any match in league 72 (Serie B)    -> tier 2
  - else any match in a domestic league id
    other than 71/72 (Copa do Brasil 73,
    state championships, ...)                -> tier 3
  - else (every match that season is in a
    continental competition, 13 Libertadores
    or 11 Sudamericana)                       -> "foreign"
"Domestic" here just means "not one of the two continental league ids" -
`MatchStore` only ever loads the league ids named in its `rules`
(`domain.competitions.COMPETITIONS` by default), so in practice the only
non-71/72 id seen is 73, but the rule does not hard-code that.

CACHING. `seed_for` is called once per club per season during a replay of
the whole store (~18k matches), so the `(season, team_id) -> tier` table is
built once per `MatchStore` and cached on the store instance itself (a
private attribute, set the first time this module sees that store) rather
than recomputed per call or kept in a module-level dict keyed by object
identity - the store already owns its derived data (`coverage`, `before`
follow the same "compute once in __init__" shape), and an instance
attribute is freed automatically when the store is garbage collected, with
no `WeakKeyDictionary` bookkeeping needed to get that for free.
"""

from typing import Mapping, Union

import pandas as pd

from brasileirao_simulator.domain.match_store import MatchStore

# The two continental competitions in `domain.competitions.COMPETITIONS`.
# Any league id outside this set is treated as domestic.
_CONTINENTAL_LEAGUES = frozenset({13, 11})

_SERIE_A = 71
_SERIE_B = 72

_TABLE_CACHE_ATTR = "_elo_seeds_division_table"
_LOOKUP_CACHE_ATTR = "_elo_seeds_division_lookup"

Tier = Union[int, str]


def _tier_for_leagues(league_ids: pd.Series) -> Tier:
    leagues = set(league_ids)
    if _SERIE_A in leagues:
        return 1
    if _SERIE_B in leagues:
        return 2
    if leagues - _CONTINENTAL_LEAGUES:
        return 3
    return "foreign"


def _build_division_table(store: MatchStore) -> pd.DataFrame:
    matches = store.matches
    appearances = pd.concat(
        [
            matches[["season", "league_id", "home_id"]].rename(columns={"home_id": "team_id"}),
            matches[["season", "league_id", "away_id"]].rename(columns={"away_id": "team_id"}),
        ],
        ignore_index=True,
    )
    if appearances.empty:
        return pd.DataFrame(columns=["season", "team_id", "tier"])

    table = (
        appearances.groupby(["season", "team_id"])["league_id"]
        .agg(_tier_for_leagues)
        .rename("tier")
        .reset_index()
    )
    return table[["season", "team_id", "tier"]]


def _cached_table(store: MatchStore) -> pd.DataFrame:
    table = getattr(store, _TABLE_CACHE_ATTR, None)
    if table is None:
        table = _build_division_table(store)
        setattr(store, _TABLE_CACHE_ATTR, table)
    return table


def _cached_lookup(store: MatchStore) -> dict:
    lookup = getattr(store, _LOOKUP_CACHE_ATTR, None)
    if lookup is None:
        table = _cached_table(store)
        lookup = dict(zip(zip(table["season"], table["team_id"]), table["tier"]))
        setattr(store, _LOOKUP_CACHE_ATTR, lookup)
    return lookup


def division_table(store: MatchStore) -> pd.DataFrame:
    """`(season, team_id) -> tier` for every club that appears in
    `store.matches`, one row per pair. `tier` is `1`, `2`, `3` or
    `"foreign"` per the module docstring's division rule. Returns a copy so
    a caller mutating the result cannot corrupt the cache other calls (and
    `seed_for`) share."""
    return _cached_table(store).copy()


def seed_for(team_id: int, season: int, store: MatchStore, params: Mapping) -> float:
    """A club's starting Elo rating the first time it is encountered in
    `season`. See the module docstring for the division rule and the
    caching strategy. `params` is duck-typed - anything with a `seeds`
    mapping attribute, so this module never imports `EloParams`."""
    lookup = _cached_lookup(store)
    key = (season, team_id)
    if key not in lookup:
        raise ValueError(f"club {team_id} has no match in season {season} - cannot seed it")

    tier = lookup[key]
    seeds = params.seeds
    if tier not in seeds:
        raise KeyError(
            f"club {team_id} is division {tier!r} in season {season}, "
            f"but params.seeds has no seed for that division: {dict(seeds)!r}"
        )
    return seeds[tier]

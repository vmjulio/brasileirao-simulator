"""The `total` half of `team_strength`: each club's opponent-adjusted
contribution to the total goals a match it plays in tends to produce.

WHY OPPONENT-ADJUSTED. A club's raw average total goals (goals for plus
goals against, across its own matches) confounds two things: how much
scoring the club itself is involved in, and how much scoring its opponents
happen to be involved in. `dixon_coles.py` solves the analogous confound for
attack/defence by fitting both jointly rather than forming marginal
averages (see that module's docstring); `total_goals_params` applies the
same idea to a single combined "how many goals does a match involving this
club tend to produce" parameter, solving every club's contribution jointly
so that "who they played" cancels by construction.

THE FIXED POINT. Call club i's contribution `c_i`. For every match a club
plays, "the rest of the match's total" is credited to the opponent, so the
defining equation is

    c_i = mean over i's matches of (total_goals_of_match - c_opponent)

which is linear in every `c`, and solved the same all-arithmetic,
no-scipy way `dixon_coles.fit` solves its own attack/defence fixed point:
iterate from a flat start (`c_i = window mean total goals / 2` for every
club - not each club's own mean, the single shared starting point the
ticket specifies) to a tolerance, capped at `_MAX_ITERATIONS` sweeps.

SEQUENTIAL (GAUSS-SEIDEL) UPDATES, NOT SIMULTANEOUS. A sweep updates every
club in `team_id` order, each one immediately using whatever value its
opponents hold AT THAT POINT in the sweep - some already updated this
sweep, some still carrying the previous sweep's value - rather than every
club reading last sweep's values simultaneously (Jacobi). This is not a
stylistic choice: on the real store, Jacobi updates contract at a rate so
close to 1 (the club-to-club update matrix is row-stochastic, and the real
schedule graph is large and only loosely connected once Série A, Série B
and every cup competition in a year all sit in the same window) that
`_MAX_ITERATIONS` sweeps of it still leave a max-change around 0.1, nowhere
near `_TOLERANCE` - confirmed by running it out to thousands of sweeps
before switching. The same Gauss-Seidel sweep on the same data reaches
`_TOLERANCE` in under twenty sweeps, comfortably inside the ticket's 200-sweep
cap, and converges to the identical fixed point (the linear system has one
solution regardless of which iterative scheme finds it) - see
tests/test_elo_total_goals.py for the convergence gates, both the small
synthetic ones and the real-store 2% gate this specifically had to pass.

RE-CENTRING IS ADDITIVE, NOT MULTIPLICATIVE. Unlike `dixon_coles.fit`'s
attack/defence, whose degeneracy is multiplicative (attack * c, defence / c
leaves every lambda unchanged, so a multiplicative rescale is the natural
fix - see that module's docstring), this fixed point's defining equation is
already additive (a mean of a DIFFERENCE, `total - c_opponent`). Its own
small residual - the fixed point solves each club's per-club mean exactly,
but "the mean over MATCHES of c_home + c_away" is a different aggregate
(matches are not evenly split one-per-club) and the iteration itself only
reaches a rather than the fixed point to a numerical tolerance - is
corrected the same way it was introduced: additively. Shifting every c_i by
the same constant delta shifts every match's `c_home + c_away` by `2 *
delta` and nothing else (every opponent-adjusted difference between clubs
is preserved exactly), so adding half the observed-minus-fitted gap to
every club's contribution is the correction that changes nothing but the
one quantity the gate checks. A multiplicative rescale would instead shrink
or stretch the spread between clubs, which nothing about the fixed point's
own equations calls for.

NO WINDOW MEMORY ACROSS CALLS. Like `elo_snapshots.team_strength`, this is
a pure function of `store` and `as_of_date` - refit from scratch every time,
no incremental state.
"""

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from brasileirao_simulator.domain.elo_snapshots import team_strength
from brasileirao_simulator.domain.match_store import MatchStore

if TYPE_CHECKING:
    from brasileirao_simulator.domain.elo import EloHistory

# How far back `total_goals_params` looks, in days before `as_of_date`. A
# club's scoring environment drifts (squad changes, division changes), so
# this is a rolling window rather than the "every match ever" memory Elo
# itself uses - see the module docstring of `elo.py` for that contrast.
TOTAL_GOALS_WINDOW_DAYS = 365

_MAX_ITERATIONS = 200
_TOLERANCE = 1e-9


def total_goals_params(store: MatchStore, as_of_date: str) -> dict[int, float]:
    """`{team_id: contribution}` - see the module docstring for the fixed
    point and the re-centring. Fit on `store.before(as_of_date)` restricted
    to matches whose kickoff falls within `TOTAL_GOALS_WINDOW_DAYS` days
    before `as_of_date` (UTC); a club with no such match is absent."""
    windowed = _windowed_matches(store, as_of_date)
    if windowed.empty:
        return {}

    total = (windowed["home_goals"] + windowed["away_goals"]).to_numpy(dtype="float64")
    teams = sorted(set(windowed["home_id"]) | set(windowed["away_id"]))
    index = {team_id: i for i, team_id in enumerate(teams)}
    n = len(teams)
    home_idx = windowed["home_id"].map(index).to_numpy()
    away_idx = windowed["away_id"].map(index).to_numpy()

    # Each club's adjacency list: (opponent's index, that match's total
    # goals) for every match it played - built once so a sweep is pure
    # array/list lookups, no DataFrame work. See the module docstring's
    # "SEQUENTIAL (GAUSS-SEIDEL) UPDATES" note for why the sweep below
    # updates sequentially rather than all at once.
    opponents: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for home, away, match_total in zip(home_idx, away_idx, total):
        opponents[home].append((away, match_total))
        opponents[away].append((home, match_total))

    window_mean_total = float(total.mean())
    contribution = np.full(n, window_mean_total / 2.0, dtype="float64")

    for _ in range(_MAX_ITERATIONS):
        max_change = 0.0
        for i in range(n):
            matches_i = opponents[i]
            updated = sum(match_total - contribution[opp] for opp, match_total in matches_i) / len(
                matches_i
            )
            change = abs(updated - contribution[i])
            if change > max_change:
                max_change = change
            contribution[i] = updated
        if max_change < _TOLERANCE:
            break

    # Re-centre: see the module docstring's "RE-CENTRING IS ADDITIVE" note.
    fitted_mean = float(np.mean(contribution[home_idx] + contribution[away_idx]))
    observed_mean = window_mean_total
    contribution = contribution + (observed_mean - fitted_mean) / 2.0

    return {team_id: float(contribution[i]) for team_id, i in index.items()}


def team_strength_with_totals(history: "EloHistory", store: MatchStore, as_of_date: str) -> pd.DataFrame:
    """`elo_snapshots.team_strength(history, as_of_date)` with one added
    `total` column (`float64`) from `total_goals_params(store, as_of_date)`;
    `NaN` for a club absent from that dict. Same rows, same order, columns
    `team_id, as_of_date, elo, total, matches_used, competitions_used`."""
    frame = team_strength(history, as_of_date)
    totals = total_goals_params(store, as_of_date)

    result = frame.copy()
    result["total"] = frame["team_id"].map(totals).astype("float64")
    return result[["team_id", "as_of_date", "elo", "total", "matches_used", "competitions_used"]]


def _windowed_matches(store: MatchStore, as_of_date: str) -> pd.DataFrame:
    """`store.before(as_of_date)` restricted to the last `TOTAL_GOALS_WINDOW_DAYS`
    days before the cutoff - same UTC cutoff convention `MatchStore.before`
    and `elo_snapshots._rows_before` use."""
    before = store.before(as_of_date)
    if before.empty:
        return before

    cutoff = pd.Timestamp(as_of_date, tz="UTC")
    window_start = cutoff - pd.Timedelta(days=TOTAL_GOALS_WINDOW_DAYS)
    kickoff = pd.to_datetime(before["fixture_date"], utc=True, format="mixed")
    return before[kickoff >= window_start].reset_index(drop=True)

# Batch simulation

**Date:** 2026-09-07
**Status:** Draft for review

## Problem

Simulating one date costs `remaining_games × 0.00145 s × iterations`. A 64-date
2026 backfill is 38.5 minutes at 100 iterations per date, 2.6 hours at 400.

That cost is not computation. Profiling one date (2026-05-03, 243 remaining
fixtures, 0.361 s per iteration) attributes it as:

| phase | s/iteration | share |
|---|---:|---:|
| λ lookups — 4 boolean masks per game over `team_params` | 0.142 | 39% |
| `_update_fixtures` — 2 boolean masks per game over 1,520 rows | 0.140 | 39% |
| `get_team_params` (DuckDB) | 0.025 | 7% |
| `standings` + `match_results` (DuckDB) | 0.020 | 5% |
| `fixtures.copy()` | ~0.000 | ~0% |

78% is pandas building boolean masks to locate single rows. The Poisson draws do
not register. `get_team_params` is pure waste: it reads the *unsimulated*
fixtures, so it returns identical values on every iteration — recomputed 100
times per date, 6,400 times per backfill pass.

The consequence that matters is not the wall-clock. At 100 iterations the
standard error on a 50% probability is 5 percentage points, which is wider than
most of the week-to-week movement in the exported time series. Today, buying
precision costs hours.

## Goal

Make iteration count cheap enough that sampling noise stops being a design
constraint, without foreclosing a modelling improvement the current
architecture already blocks.

## Approach: vectorise across iterations

Every remaining fixture's λ pair is constant across iterations — `team_params`
is computed once from the unsimulated fixtures and never re-read inside the
loop (verified: values identical before and after a simulated season). So all
N iterations of a single fixture can be drawn in one call:

```python
home_goals = np.random.poisson(lam_home, size=n_iterations)   # one fixture, all iterations
```

The loop over fixtures stays; the loop over iterations disappears. Points, wins
and goals accumulate into `(n_iterations, n_teams)` arrays, and the table is
ranked once per iteration at the end.

### Why not vectorise across fixtures as well

Drawing `(n_iterations, n_games)` in a single call measures **735× faster**
(34.87 s → 0.05 s for 100 iterations at 2026-05-03). It is rejected anyway.

Collapsing the fixture loop hard-codes the assumption that λ never changes
within a simulated season. That assumption is currently true, and it is a real
limitation:

- **Parameters go stale.** The window is the last 19 matches per venue. At
  2026-09-05 every team has 12 or 13 home matches in 2026, so a round-38 fixture
  is simulated with parameters ~37% built from 2025 — even though by round 38 a
  real 19-match window would hold no 2025 games at all.
- **Runs cannot occur.** Form in football is autocorrelated; fixed λ with
  independent draws produces near-binomial variance around a fixed mean, which
  understates how often an outsider goes on the streak that wins a title.
- **It is internally inconsistent.** The simulation generates 17 rounds of
  scorelines and then discards the information they contain.

Updating λ from simulated results is not obviously correct either — naive
feedback (a team that randomly wins three becomes stronger and wins more)
manufactures false certainty, and doing it well needs damping. The point is
that keeping the fixture loop leaves that experiment available. At the speeds
this design reaches, the extra 7× is worth nothing measurable.

## Design

### Components

**`domain/batch_simulation.py`** — the mechanism, no I/O.

```python
@dataclass(frozen=True)
class SeasonBaseline:
    """Everything the simulation needs about one season, as arrays.

    Built once per as-of date from results already played plus the fixed λ of
    every remaining fixture.
    """
    teams: list[str]                 # index order for every array below
    points: np.ndarray               # (n_teams,) from matches already played
    wins: np.ndarray
    goals_for: np.ndarray
    goals_against: np.ndarray
    home_team: np.ndarray            # (n_games,) index into teams
    away_team: np.ndarray            # (n_games,)
    lam_home: np.ndarray             # (n_games,) expected home goals
    lam_away: np.ndarray             # (n_games,)
    fixture_id: np.ndarray           # (n_games,) for per-match odds
    round_: np.ndarray               # (n_games,)


def build_baseline(fixtures, remaining_games, team_params, season) -> SeasonBaseline: ...


@dataclass(frozen=True)
class BatchOutcome:
    """One batch of simulated seasons."""
    rank: np.ndarray                 # (n_iterations, n_teams) final position, 1-based
    home_goals: np.ndarray           # (n_iterations, n_games)
    away_goals: np.ndarray


def simulate_batch(baseline: SeasonBaseline, iterations: int, rng) -> BatchOutcome: ...
```

`simulate_batch` loops over fixtures, drawing `iterations` scorelines per
fixture, accumulating into `(iterations, n_teams)` tables, then ranks.

**`ports/batch_simulator_port.py`** — a second port. The existing
`FixtureSimulatorPort`'s unit of work is one season, so it cannot express this;
rather than distort it, add a port whose unit is a batch.

```python
class BatchSimulatorPort(ABC):
    @abstractmethod
    def simulate_batch(self, fixtures, remaining_games, iterations: int) -> BatchOutcome: ...
```

**`adapters/vector_poisson_adapter.py`** — implements it, owning the DuckDB
connection and the `team_params` query (run once, not once per iteration).

**`domain/result_logger.py`** — gains one method that ingests a `BatchOutcome`
and increments the same counters the existing per-iteration methods do. The
stored pickle format does not change, so every existing export, the accumulate
behaviour of `--force`, and all 64 backfilled 2026 pickles stay valid.

**`domain/simulation_runner.py`** — dispatches on which port it was given. The
existing path is untouched.

### Ranking must match `standings.sql` exactly

The SQL orders by `p desc, w desc, gd desc, gf desc, ga desc`. The vectorised
ranking must use `np.lexsort` over those same five keys in that order — not a
packed arithmetic key, which can collide.

Note `ga desc` ranks a team *higher* for conceding more, which is almost
certainly a bug in the existing query (fewer conceded should rank better). It
is replicated exactly here regardless: this change is about speed, and a silent
ranking change would corrupt comparability with the 2025 and 2026 pickles
already on disk. Fixing it is separate work with its own before/after.

## Validation

Equivalence is provable rather than statistical, by decomposing it:

1. **λ vectors are identical.** Already measured — `max abs difference 0.00e+00`
   across 243 fixtures. Becomes a test.
2. **Ranking is identical given identical scorelines.** Feed a fixed set of
   scorelines through both `standings.sql` and the vectorised ranker and assert
   the tables match row for row. This is exact, not statistical.
3. Given 1 and 2, the only remaining difference is which pseudo-random numbers
   are drawn, and both draw Poisson at the same λ.

Plus: a statistical check that title counts from both paths agree within
sampling error at high iteration counts, and the existing 32 tests — including
the 2025 regression baseline written against the original code — must stay
green.

## Scope

In scope: the batch path, its port and adapter, logger ingestion, runner
dispatch, and an opt-in flag on the entrypoints.

Out of scope, deliberately:

- **Removing the existing adapters.** They stay as the reference implementation
  to validate against. Deleting them would leave nothing to check against.
- **Dynamic λ.** This design preserves the option; it does not implement it.
- **The `ga desc` ranking bug.** Replicated, not fixed. See above.
- **`league_id` and the lookback window.** Still deferred.
- **Parallelising dates across cores.** Composes with this and is unnecessary at
  the resulting speed.

## Expected outcome

The 64-date backfill drops from 38.5 minutes to seconds, and 100,000 iterations
per date becomes cheaper than today's 100. The ±5pp noise band on a 50%
probability falls to ±0.16pp, which is what makes the exported time series
readable at the resolution the movements actually occur.

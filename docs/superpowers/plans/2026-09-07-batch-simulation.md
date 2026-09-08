# Batch Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Monte Carlo iteration cheap enough that sampling noise stops constraining the design, by vectorising across iterations — with two adapters so the speed/flexibility trade-off can be measured rather than argued.

**Architecture:** A second port whose unit of work is a batch of seasons rather than one season. A shared `SeasonBaseline` turns one as-of date into fixed λ arrays plus the already-played table; two adapters consume it (one looping fixtures, one collapsing that loop too); `ResultLogger` gains bulk ingestion; `SimulationRunner` dispatches on which port it was handed. The existing per-season path is untouched and becomes the reference implementation to validate against.

**Tech Stack:** Python 3.11 (Docker image), numpy 2.1.2, pandas 2.2.3, duckdb 1.1.1, pytest 8.3.3.

**Spec:** `docs/superpowers/specs/2026-09-07-batch-simulation-design.md`

## Global Constraints

- **Tests run in Docker only.** `duckdb`/`pandas`/`numpy` are not installed on the host. Every test command is `docker-compose run --rm app pytest ...`. Baseline is **32 passing**; that number must not drop.
- **Docker `WORKDIR` is `/src`.** Paths in `settings.py` stay relative.
- **`./src:/src` and `./tests:/tests` are mounted** — edits are live; rebuild only for `requirements.txt`/`Dockerfile`.
- **Season is `int` in Python, `str` in SQL.** The `season` column is int64 from `read_csv`; SQL compares quoted literals DuckDB casts. Reversing this yields an empty DataFrame with no exception.
- **No behavioural change to results.** 108 pickles for 2025 and 64 for 2026 (all at 200 iterations) must stay comparable. The only sanctioned edit to ranking is dropping the provably dead `ga desc` clause.
- **`league_id = 71`, the `rn <= 19` / `rn <= 12` lookback windows, and head-to-head tiebreaking are all out of scope.** Do not touch them.
- **The existing adapters and `FixtureSimulatorPort` are not modified or deleted.** They are the reference implementation.
- **Ranking order is `p desc, w desc, gd desc, gf desc`** — four keys, via `np.lexsort`, never a packed arithmetic key.

---

### Task 1: Drop the dead `ga desc` clause

Smallest possible change, landed first and alone so its no-op character is visible in one commit.

**Files:**
- Modify: `src/files/queries/standings.sql:20`
- Create: `tests/test_standings_ranking.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing new. `standings.sql` keeps its existing output columns and row order.

- [ ] **Step 1: Write the test that pins the no-op claim**

Create `tests/test_standings_ranking.py`:

```python
"""The standings tiebreakers, and why one of them was removable.

standings.sql computes gd = coalesce(gf,0) - coalesce(ga,0), so ga = gf - gd.
Two teams tied on gf AND gd therefore have identical ga by construction, which
makes a trailing `ga desc` tiebreaker incapable of breaking any tie. These tests
pin that identity, so that if gd is ever redefined the removal is revisited.
"""

import numpy as np

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


BRASILEIRAO_TEAM_COUNT = 20


def _standings(seed: int):
    np.random.seed(seed)
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures()
    remaining = tables.remaining_games()
    adapter = PoissonSameVenueAverageAdapter("average", 2026)
    return adapter.get_brasileirao_standings(adapter.simulate_fixtures(fixtures, remaining))


def test_goal_difference_determines_goals_against():
    """The identity that makes the `ga desc` tiebreaker dead."""
    standings = _standings(seed=7)

    assert ((standings["gf"] - standings["ga"]) == standings["gd"]).all()


def test_no_tie_survives_the_four_real_tiebreakers_with_differing_ga():
    """If p, w, gd and gf all tie, ga cannot differ - so ordering by it changes
    nothing."""
    for seed in range(5):
        standings = _standings(seed)
        differing = standings.groupby(["p", "w", "gd", "gf"])["ga"].nunique()

        assert (differing <= 1).all()


def test_standings_are_a_contiguous_ranking():
    standings = _standings(seed=11)

    assert sorted(standings["rank_"]) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))
```

- [ ] **Step 2: Run it against the unmodified query**

Run: `docker-compose run --rm app pytest /tests/test_standings_ranking.py -v`
Expected: 3 passed. These describe the current behaviour, so they pass before the edit — that is the point. If any fails, STOP and report: the removal would not be a no-op and the spec's reasoning is wrong.

- [ ] **Step 3: Remove the clause**

In `src/files/queries/standings.sql`, line 20, change:

```sql
select row_number() over (order by p desc, w desc, gd desc, gf desc, ga desc) as rank_, *
```

to:

```sql
-- ga is omitted deliberately: gd = gf - ga, so a tie on gf and gd forces a tie
-- on ga and it can never break one. See tests/test_standings_ranking.py.
select row_number() over (order by p desc, w desc, gd desc, gf desc) as rank_, *
```

- [ ] **Step 4: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: 35 passed (32 + 3 new). A failure here means the removal was not a no-op.

- [ ] **Step 5: Commit**

```bash
git add src/files/queries/standings.sql tests/test_standings_ranking.py
git commit -m "refactor: drop the dead ga tiebreaker from standings"
```

---

### Task 2: `SeasonBaseline` — one as-of date as arrays

**Files:**
- Create: `src/brasileirao_simulator/domain/batch_simulation.py`
- Create: `tests/test_batch_simulation.py`

**Interfaces:**
- Consumes: `Tables(SeasonData(season))` output frames, and a `team_params` DataFrame with columns `team_name`, `venue`, `goals_for_average`, `goals_against_average`.
- Produces:
  - `SeasonBaseline` (frozen dataclass) with fields `teams: list[str]`, `points`, `wins`, `goals_for`, `goals_against` (each `np.ndarray` shape `(n_teams,)`), and `home_team`, `away_team`, `lam_home`, `lam_away`, `fixture_id`, `round_` (each shape `(n_games,)`).
  - `build_baseline(fixtures: pd.DataFrame, remaining_games: pd.DataFrame, team_params: pd.DataFrame, season: int) -> SeasonBaseline`
  - `ADJUSTMENT_WEIGHT = 0.5`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_simulation.py`:

```python
"""Turning one as-of date into arrays.

The whole batch design rests on one property: every remaining fixture's lambda
pair is constant across iterations, because team_params is computed from the
UNSIMULATED fixtures and never re-read inside the loop. These tests pin the
lambdas to the existing adapter's, so the two paths are provably the same model
rather than merely similar.
"""

import numpy as np

from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.batch_simulation import build_baseline
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


AS_OF = "2026-05-03"


def _setup():
    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    remaining = tables.remaining_games(blank_from_date=AS_OF)
    adapter = PoissonSameVenueAverageAdapter("average", 2026)
    team_params = adapter.get_team_params(fixtures.copy())
    return fixtures, remaining, team_params, adapter


def test_lambdas_match_the_existing_adapter_exactly():
    """Not 'close': bit-identical. Any difference means a different model."""
    fixtures, remaining, team_params, adapter = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    games = remaining.sort_values(by=["fixture_date"]).to_dict(orient="records")
    expected = np.array(
        [adapter._calculate_adjusted_averages(game, team_params) for game in games]
    )

    assert np.array_equal(baseline.lam_home, expected[:, 0])
    assert np.array_equal(baseline.lam_away, expected[:, 1])


def test_baseline_covers_every_team_and_remaining_fixture():
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    assert len(baseline.teams) == 20
    assert len(baseline.lam_home) == len(remaining)
    assert baseline.home_team.max() < 20
    assert baseline.away_team.max() < 20


def test_played_results_are_carried_in_as_the_starting_table():
    """The as-of table must equal what the SQL says it is, or every simulated
    season starts from the wrong place."""
    fixtures, remaining, team_params, adapter = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    played = fixtures[(fixtures["season"] == 2026) & (fixtures["goals_for"].notnull())]
    for position, team in enumerate(baseline.teams):
        rows = played[played["team_name"] == team]
        wins = (rows["goals_for"] > rows["goals_against"]).sum()
        draws = (rows["goals_for"] == rows["goals_against"]).sum()

        assert baseline.points[position] == 3 * wins + draws
        assert baseline.wins[position] == wins
        assert baseline.goals_for[position] == rows["goals_for"].sum()


def test_a_team_absent_from_team_params_falls_back_to_one():
    """Mirrors the adapter's .empty guard for a team with no rows at a venue."""
    fixtures, remaining, team_params, _ = _setup()
    thinned = team_params[team_params["venue"] != "home"]
    baseline = build_baseline(fixtures, remaining, thinned, 2026)

    # every home attack term is now the 1.0 fallback, so no lambda is below 0.5
    assert (baseline.lam_home >= 0.5).all()
```

- [ ] **Step 2: Run and verify they fail**

Run: `docker-compose run --rm app pytest /tests/test_batch_simulation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brasileirao_simulator.domain.batch_simulation'`

- [ ] **Step 3: Implement `build_baseline`**

Create `src/brasileirao_simulator/domain/batch_simulation.py`:

```python
"""One as-of date, expressed as arrays a batch simulation can consume.

Team parameters are computed from the fixtures as they stand before any
simulation and are never re-read afterwards, so every remaining fixture's
lambda pair is a constant. Precomputing them here is what lets the Monte Carlo
draw a whole batch of seasons without touching a DataFrame.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


ADJUSTMENT_WEIGHT = 0.5

# Used when a team has no rows at a venue, mirroring the .empty guard in
# PoissonSameVenueAverageAdapter._calculate_adjusted_averages.
MISSING_TEAM_AVERAGE = 1.0


@dataclass(frozen=True)
class SeasonBaseline:
    """The fixed inputs of one as-of date.

    Arrays indexed by team follow `teams`; arrays indexed by fixture follow the
    remaining games in date order, which is the order the existing adapter
    simulates them in.
    """

    teams: list[str]
    points: np.ndarray
    wins: np.ndarray
    goals_for: np.ndarray
    goals_against: np.ndarray
    home_team: np.ndarray
    away_team: np.ndarray
    lam_home: np.ndarray
    lam_away: np.ndarray
    fixture_id: np.ndarray
    round_: np.ndarray


def build_baseline(
    fixtures: pd.DataFrame,
    remaining_games: pd.DataFrame,
    team_params: pd.DataFrame,
    season: int,
) -> SeasonBaseline:
    season_rows = fixtures[fixtures["season"] == season]
    teams = sorted(season_rows["team_name"].unique())
    position_of = {team: position for position, team in enumerate(teams)}

    points, wins, goals_for, goals_against = _played_table(season_rows, teams, position_of)
    averages = _averages_by_team_and_venue(team_params)

    games = remaining_games[remaining_games["season"] == season].sort_values(
        by=["fixture_date"]
    )
    home_team, away_team, lam_home, lam_away = _fixture_arrays(games, position_of, averages)

    return SeasonBaseline(
        teams=teams,
        points=points,
        wins=wins,
        goals_for=goals_for,
        goals_against=goals_against,
        home_team=home_team,
        away_team=away_team,
        lam_home=lam_home,
        lam_away=lam_away,
        fixture_id=games["fixture_id"].to_numpy(),
        round_=games["round_"].to_numpy(),
    )


def _played_table(season_rows, teams, position_of):
    played = season_rows[season_rows["goals_for"].notnull()]
    points = np.zeros(len(teams))
    wins = np.zeros(len(teams))
    goals_for = np.zeros(len(teams))
    goals_against = np.zeros(len(teams))

    for row in played.itertuples():
        position = position_of[row.team_name]
        goals_for[position] += row.goals_for
        goals_against[position] += row.goals_against
        if row.goals_for > row.goals_against:
            points[position] += 3
            wins[position] += 1
        elif row.goals_for == row.goals_against:
            points[position] += 1

    return points, wins, goals_for, goals_against


def _averages_by_team_and_venue(team_params):
    return {
        (row.team_name, row.venue): (row.goals_for_average, row.goals_against_average)
        for row in team_params.itertuples()
    }


def _fixture_arrays(games, position_of, averages):
    fallback = (MISSING_TEAM_AVERAGE, MISSING_TEAM_AVERAGE)
    home_team, away_team, lam_home, lam_away = [], [], [], []

    for row in games.itertuples():
        home_for, home_against = averages.get((row.team_name, "home"), fallback)
        away_for, away_against = averages.get((row.opponent_name, "away"), fallback)

        home_team.append(position_of[row.team_name])
        away_team.append(position_of[row.opponent_name])
        lam_home.append(ADJUSTMENT_WEIGHT * home_for + ADJUSTMENT_WEIGHT * away_against)
        lam_away.append(ADJUSTMENT_WEIGHT * away_for + ADJUSTMENT_WEIGHT * home_against)

    return (
        np.array(home_team),
        np.array(away_team),
        np.array(lam_home),
        np.array(lam_away),
    )
```

- [ ] **Step 4: Run and verify they pass**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: 39 passed (35 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/brasileirao_simulator/domain/batch_simulation.py tests/test_batch_simulation.py
git commit -m "feat: express one as-of date as batch simulation arrays"
```

---

### Task 3: The batch simulation and its ranking

**Files:**
- Modify: `src/brasileirao_simulator/domain/batch_simulation.py`
- Modify: `tests/test_batch_simulation.py`

**Interfaces:**
- Consumes: `SeasonBaseline` from Task 2.
- Produces:
  - `BatchOutcome` (frozen dataclass) with `rank: np.ndarray` shape `(iterations, n_teams)` holding 1-based final positions, and `home_goals` / `away_goals` each shape `(iterations, n_games)`.
  - `simulate_batch(baseline: SeasonBaseline, iterations: int, rng: np.random.Generator, vectorise_fixtures: bool = False) -> BatchOutcome`
  - `rank_tables(points, wins, goals_for, goals_against) -> np.ndarray`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_batch_simulation.py`:

```python
from brasileirao_simulator.domain.batch_simulation import (
    BatchOutcome,
    rank_tables,
    simulate_batch,
)


def test_rank_matches_the_sql_tiebreakers():
    """p desc, then w desc, then gd desc, then gf desc."""
    points = np.array([[10.0, 10.0, 10.0, 7.0]])
    wins = np.array([[3.0, 3.0, 2.0, 9.0]])
    goals_for = np.array([[8.0, 5.0, 99.0, 0.0]])
    goals_against = np.array([[3.0, 0.0, 0.0, 0.0]])

    rank = rank_tables(points, wins, goals_for, goals_against)

    # team 1: 10pts 3w gd+5 ; team 0: 10pts 3w gd+5 but fewer gf -> below team 1
    # team 2: 10pts 2w      -> below both on wins
    # team 3: 7pts          -> last
    assert list(rank[0]) == [2, 1, 3, 4]


def test_rank_is_a_permutation_for_every_iteration():
    rng = np.random.default_rng(0)
    points = rng.integers(0, 40, size=(50, 20)).astype(float)
    zeros = np.zeros((50, 20))

    rank = rank_tables(points, zeros, zeros, zeros)

    for row in rank:
        assert sorted(row) == list(range(1, 21))


def test_batch_shapes_follow_the_baseline():
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    outcome = simulate_batch(baseline, iterations=7, rng=np.random.default_rng(1))

    assert isinstance(outcome, BatchOutcome)
    assert outcome.rank.shape == (7, len(baseline.teams))
    assert outcome.home_goals.shape == (7, len(baseline.lam_home))


def test_both_strategies_agree_on_the_distribution():
    """The fixture loop and the fully vectorised draw are the same model; over
    enough iterations their champion distributions must agree."""
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    looped = simulate_batch(baseline, 4000, np.random.default_rng(2), vectorise_fixtures=False)
    vector = simulate_batch(baseline, 4000, np.random.default_rng(3), vectorise_fixtures=True)

    looped_share = (looped.rank == 1).mean(axis=0)
    vector_share = (vector.rank == 1).mean(axis=0)

    assert np.abs(looped_share - vector_share).max() < 0.04


def test_rank_tables_reproduces_standings_sql_exactly():
    """The strongest check available: same inputs, same ordering, row for row.

    This is exact rather than statistical - it removes ranking as a possible
    source of difference between the two paths, leaving only the random draws.
    """
    fixtures, remaining, _, adapter = _setup()
    simulated = adapter.simulate_fixtures(fixtures, remaining)
    sql_table = adapter.get_brasileirao_standings(simulated)

    teams = list(sql_table["team_name"])
    ours = rank_tables(
        sql_table["p"].to_numpy(dtype=float)[None, :],
        sql_table["w"].to_numpy(dtype=float)[None, :],
        sql_table["gf"].to_numpy(dtype=float)[None, :],
        sql_table["ga"].to_numpy(dtype=float)[None, :],
    )[0]

    by_our_rank = [team for _, team in sorted(zip(ours, teams))]
    assert by_our_rank == teams, "vectorised ranking disagrees with standings.sql"


def test_simulated_seasons_are_complete():
    """Every team must end on 38 games: baseline played + simulated remaining."""
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)
    played_per_team = np.bincount(
        np.concatenate([baseline.home_team, baseline.away_team]),
        minlength=len(baseline.teams),
    )
    season_rows = fixtures[(fixtures["season"] == 2026) & fixtures["goals_for"].notnull()]
    already = season_rows.groupby("team_name").size().reindex(baseline.teams).to_numpy()

    assert (played_per_team + already == 38).all()
```

- [ ] **Step 2: Run and verify they fail**

Run: `docker-compose run --rm app pytest /tests/test_batch_simulation.py -v`
Expected: FAIL — `ImportError: cannot import name 'simulate_batch'`

- [ ] **Step 3: Implement the batch simulation**

Append to `src/brasileirao_simulator/domain/batch_simulation.py`:

```python
@dataclass(frozen=True)
class BatchOutcome:
    """One batch of simulated seasons.

    rank holds 1-based final positions, one row per iteration; the goal arrays
    keep the per-fixture scorelines so per-match odds can be counted.
    """

    rank: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray


def simulate_batch(
    baseline: SeasonBaseline,
    iterations: int,
    rng: np.random.Generator,
    vectorise_fixtures: bool = False,
) -> BatchOutcome:
    """Simulate `iterations` complete seasons from a fixed baseline.

    With vectorise_fixtures the entire batch is one Poisson call, which is
    faster but forecloses ever varying a lambda as a simulated season unfolds.
    The default draws fixture by fixture, keeping that door open; both are the
    same model today.
    """
    shape = (iterations, len(baseline.lam_home))

    if vectorise_fixtures:
        home_goals = rng.poisson(baseline.lam_home, size=shape)
        away_goals = rng.poisson(baseline.lam_away, size=shape)
    else:
        home_goals = np.empty(shape, dtype=np.int64)
        away_goals = np.empty(shape, dtype=np.int64)
        for fixture in range(shape[1]):
            home_goals[:, fixture] = rng.poisson(baseline.lam_home[fixture], iterations)
            away_goals[:, fixture] = rng.poisson(baseline.lam_away[fixture], iterations)

    points, wins, goals_for, goals_against = _accumulate(baseline, home_goals, away_goals)

    return BatchOutcome(
        rank=rank_tables(points, wins, goals_for, goals_against),
        home_goals=home_goals,
        away_goals=away_goals,
    )


def _accumulate(baseline, home_goals, away_goals):
    iterations = home_goals.shape[0]
    rows = np.arange(iterations)[:, None]
    home = baseline.home_team[None, :]
    away = baseline.away_team[None, :]

    home_won = home_goals > away_goals
    away_won = away_goals > home_goals
    drawn = home_goals == away_goals

    points = np.tile(baseline.points, (iterations, 1))
    wins = np.tile(baseline.wins, (iterations, 1))
    goals_for = np.tile(baseline.goals_for, (iterations, 1))
    goals_against = np.tile(baseline.goals_against, (iterations, 1))

    np.add.at(points, (rows, home), np.where(home_won, 3, np.where(drawn, 1, 0)))
    np.add.at(points, (rows, away), np.where(away_won, 3, np.where(drawn, 1, 0)))
    np.add.at(wins, (rows, home), home_won.astype(np.int64))
    np.add.at(wins, (rows, away), away_won.astype(np.int64))
    np.add.at(goals_for, (rows, home), home_goals)
    np.add.at(goals_for, (rows, away), away_goals)
    np.add.at(goals_against, (rows, home), away_goals)
    np.add.at(goals_against, (rows, away), home_goals)

    return points, wins, goals_for, goals_against


def rank_tables(points, wins, goals_for, goals_against) -> np.ndarray:
    """Final positions, ordered as standings.sql orders them.

    lexsort takes its keys last-significant first, so the primary key goes last.
    Negated because lexsort is ascending and every key here ranks descending.
    ga is not a key: gd = gf - ga, so a tie on gf and gd forces a tie on ga.
    """
    goal_difference = goals_for - goals_against
    order = np.lexsort(
        (-goals_for, -goal_difference, -wins, -points),
        axis=1,
    )

    rank = np.empty_like(order)
    positions = np.arange(1, order.shape[1] + 1)
    np.put_along_axis(rank, order, np.tile(positions, (order.shape[0], 1)), axis=1)
    return rank
```

- [ ] **Step 4: Run and verify they pass**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: 45 passed (39 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add src/brasileirao_simulator/domain/batch_simulation.py tests/test_batch_simulation.py
git commit -m "feat: simulate a batch of seasons from fixed lambdas"
```

---

### Task 4: Port, two adapters, logger ingestion, runner dispatch

The wiring. These change together because a port with no adapter and a runner that cannot reach it are not independently reviewable.

**Files:**
- Create: `src/brasileirao_simulator/ports/batch_simulator_port.py`
- Create: `src/brasileirao_simulator/adapters/batch_poisson_adapter.py`
- Modify: `src/brasileirao_simulator/domain/result_logger.py`
- Modify: `src/brasileirao_simulator/domain/simulation_runner.py`
- Create: `tests/test_batch_adapter.py`

**Interfaces:**
- Consumes: `build_baseline`, `simulate_batch`, `BatchOutcome`, `SeasonBaseline`.
- Produces:
  - `BatchSimulatorPort` with `season: int` and `simulate_batch(fixtures, remaining_games, iterations) -> BatchOutcome`.
  - `IterationBatchAdapter(strategy: Optional[str], season: int)` — the default, loops fixtures.
  - `FullVectorAdapter(strategy: Optional[str], season: int)` — collapses that loop.
  - `ResultLogger.log_batch(outcome: BatchOutcome, teams: list[str]) -> None`
  - `SimulationRunner` accepting either port.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_adapter.py`:

```python
"""The batch adapters, and their agreement with the per-season path."""

import numpy as np

from brasileirao_simulator.adapters.batch_poisson_adapter import (
    FullVectorAdapter,
    IterationBatchAdapter,
)
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.domain.result_logger import ResultLogger
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


AS_OF = "2026-05-03"
BRASILEIRAO_TEAM_COUNT = 20


def _frames():
    tables = Tables(SeasonData(2026))
    return (
        tables.enriched_tidy_fixtures(blank_from_date=AS_OF),
        tables.remaining_games(blank_from_date=AS_OF),
    )


def test_both_adapters_expose_the_season_the_service_checks():
    """SimulationService rejects an adapter whose season differs from params."""
    assert IterationBatchAdapter("average", 2026).season == 2026
    assert FullVectorAdapter("average", 2026).season == 2026


def test_adapter_produces_a_full_batch():
    fixtures, remaining = _frames()
    outcome = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 20)

    assert outcome.rank.shape == (20, BRASILEIRAO_TEAM_COUNT)
    for row in outcome.rank:
        assert sorted(row) == list(range(1, BRASILEIRAO_TEAM_COUNT + 1))


def test_batch_agrees_with_the_per_season_adapter():
    """The reference implementation and the batch path are the same model, so
    their champion distributions must agree within sampling error."""
    fixtures, remaining = _frames()

    np.random.seed(5)
    reference = PoissonSameVenueAverageAdapter("average", 2026)
    counts = {}
    for _ in range(400):
        standings = reference.get_brasileirao_standings(
            reference.simulate_fixtures(fixtures, remaining)
        )
        champion = standings.iloc[0]["team_name"]
        counts[champion] = counts.get(champion, 0) + 1

    outcome = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 400)
    teams = sorted(fixtures[fixtures["season"] == 2026]["team_name"].unique())
    batch = {teams[i]: int((outcome.rank[:, i] == 1).sum()) for i in range(len(teams))}

    for team in set(counts) | set(batch):
        assert abs(counts.get(team, 0) - batch.get(team, 0)) / 400 < 0.08


def test_logger_counts_a_batch_the_same_way_it_counts_one_season():
    fixtures, remaining = _frames()
    outcome = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 30)
    teams = sorted(fixtures[fixtures["season"] == 2026]["team_name"].unique())

    logger = ResultLogger()
    logger.log_batch(outcome, teams)
    results = logger.get_results()

    assert sum(results["brasileirao_title"].values()) == 30
    assert sum(results["brasileirao_relegation"].values()) == 30 * 4
    for team in teams:
        assert sum(results["brasileirao_positions"][team].values()) == 30


def test_full_vector_adapter_matches_the_iteration_adapter():
    fixtures, remaining = _frames()
    looped = IterationBatchAdapter("average", 2026).simulate_batch(fixtures, remaining, 2000)
    vector = FullVectorAdapter("average", 2026).simulate_batch(fixtures, remaining, 2000)

    looped_share = (looped.rank == 1).mean(axis=0)
    vector_share = (vector.rank == 1).mean(axis=0)

    assert np.abs(looped_share - vector_share).max() < 0.05
```

- [ ] **Step 2: Run and verify they fail**

Run: `docker-compose run --rm app pytest /tests/test_batch_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brasileirao_simulator.adapters.batch_poisson_adapter'`

- [ ] **Step 3: Create the port**

Create `src/brasileirao_simulator/ports/batch_simulator_port.py`:

```python
from abc import ABC, abstractmethod

import pandas as pd

from brasileirao_simulator.domain.batch_simulation import BatchOutcome


class BatchSimulatorPort(ABC):
    """A simulator whose unit of work is a batch of seasons.

    FixtureSimulatorPort's unit is a single season, which cannot express drawing
    many at once; rather than distort that port, this one sits alongside it.
    """

    season: int

    @abstractmethod
    def simulate_batch(
        self,
        fixtures: pd.DataFrame,
        remaining_games: pd.DataFrame,
        iterations: int,
    ) -> BatchOutcome:
        pass
```

- [ ] **Step 4: Implement both adapters**

Create `src/brasileirao_simulator/adapters/batch_poisson_adapter.py`:

```python
"""Batch simulators built on the same-venue-average team parameters.

Both adapters run the identical model; they differ only in whether the Poisson
draw loops over fixtures. The looping one is the default because collapsing
that loop forecloses ever letting a lambda change as a simulated season
unfolds. The collapsed one is kept so the trade-off can be measured.
"""

from typing import Optional

import duckdb
import pandas as pd

from brasileirao_simulator.domain.batch_simulation import (
    BatchOutcome,
    build_baseline,
    simulate_batch,
)
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort
import numpy as np


class IterationBatchAdapter(BatchSimulatorPort):
    """Draws every iteration of one fixture at a time."""

    vectorise_fixtures = False

    def __init__(self, strategy: Optional[str], season: int) -> None:
        self.con = duckdb.connect()
        self.strategy: Optional[str] = strategy
        self.season: int = season
        self.queries: Queries = Queries(season)

    def simulate_batch(
        self,
        fixtures: pd.DataFrame,
        remaining_games: pd.DataFrame,
        iterations: int,
    ) -> BatchOutcome:
        # Team parameters read the unsimulated fixtures, so this runs once for
        # the whole batch rather than once per simulated season.
        self.con.register("new_fixtures", fixtures)
        team_params = self.con.sql(self.queries.team_params_same_venue_average()).df()

        baseline = build_baseline(fixtures, remaining_games, team_params, self.season)
        return simulate_batch(
            baseline,
            iterations,
            np.random.default_rng(),
            vectorise_fixtures=self.vectorise_fixtures,
        )


class FullVectorAdapter(IterationBatchAdapter):
    """Draws the entire batch in one call. Faster, and fixes lambda for good."""

    vectorise_fixtures = True
```

- [ ] **Step 5: Add batch ingestion to the logger**

In `src/brasileirao_simulator/domain/result_logger.py`, add this method after `log_brasileirao_positions`, leaving every existing method untouched:

```python
    def log_batch(self, outcome, teams: list) -> None:
        """Count a whole batch of simulated seasons.

        Increments exactly the counters the per-season log_* methods do, so the
        pickle a batch run writes is indistinguishable from one written a season
        at a time - which is what keeps existing results comparable.
        """
        for position, team in enumerate(teams):
            ranks = outcome.rank[:, position]
            self.brasileirao_title_positions[team] += int((ranks == 1).sum())
            self.brasileirao_relegation_positions[team] += int((ranks >= 17).sum())
            for rank in ranks:
                self.brasileirao_positions[team][int(rank)] += 1
```

- [ ] **Step 6: Teach the runner to dispatch**

In `src/brasileirao_simulator/domain/simulation_runner.py`, add the import:

```python
from brasileirao_simulator.ports.batch_simulator_port import BatchSimulatorPort
```

and replace the body of `run` with:

```python
    def run(self) -> None:
        remaining_iterations: int = self.iterations

        while remaining_iterations > 0:
            current_batch_size: int = min(remaining_iterations, self.batch_size)
            print(f"Running batch of size: {current_batch_size}; Remaining iterations: {remaining_iterations}")

            if isinstance(self.simulator, BatchSimulatorPort):
                self._run_batch(current_batch_size)
            else:
                self._run_one_at_a_time(current_batch_size)

            self.persistence.save_results(results=self.logger.get_results(), strategy=self.strategy, suffix=self.file_suffix)
            remaining_iterations -= current_batch_size

    def _run_batch(self, batch_size: int) -> None:
        outcome = self.simulator.simulate_batch(self.fixtures, self.remaining_games, batch_size)
        teams = sorted(self.fixtures[self.fixtures["season"] == self.simulator.season]["team_name"].unique())
        self.logger.log_batch(outcome, teams)

    def _run_one_at_a_time(self, batch_size: int) -> None:
        for _ in range(batch_size):
            simulated_fixtures = self.simulator.simulate_fixtures(self.fixtures, self.remaining_games)
            bras_standings = self.simulator.get_brasileirao_standings(simulated_fixtures)
            match_results = self.simulator.get_match_results(simulated_fixtures)

            self.logger.log_brasileirao_results(bras_standings)
            self.logger.log_brasileirao_relegation_points(bras_standings)
            self.logger.log_brasileirao_positions(bras_standings)
            self.logger.log_match_results(match_results)
```

Note the batch path does not populate `brasileirao_relegation_points` or `match_results`; Task 5 covers what that means for the exports.

- [ ] **Step 7: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: 50 passed (44 + 6 new).

- [ ] **Step 8: Commit**

```bash
git add src/brasileirao_simulator/ports/batch_simulator_port.py \
        src/brasileirao_simulator/adapters/batch_poisson_adapter.py \
        src/brasileirao_simulator/domain/result_logger.py \
        src/brasileirao_simulator/domain/simulation_runner.py \
        tests/test_batch_adapter.py
git commit -m "feat: add batch simulator port with two adapters"
```

---

### Task 5: Expose it on the entrypoints, and measure

**Files:**
- Modify: `src/brasileirao_simulator/entrypoints/current_probabilities.py`
- Modify: `src/brasileirao_simulator/entrypoints/backfill.py`
- Modify: `README.md`
- Create: `tests/test_batch_entrypoint.py`

**Interfaces:**
- Consumes: `IterationBatchAdapter`, `FullVectorAdapter`.
- Produces: a `--simulator {loop,batch,vector}` flag on both entrypoints, defaulting to `loop`; and `simulator_for(name: str, strategy: str, season: int)` in a shared place both entrypoints import.

- [ ] **Step 1: Write the failing test**

Create `tests/test_batch_entrypoint.py`:

```python
"""Choosing a simulator from the command line."""

import pytest

from brasileirao_simulator.adapters.batch_poisson_adapter import (
    FullVectorAdapter,
    IterationBatchAdapter,
)
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.entrypoints.simulators import simulator_for


def test_the_default_is_the_reference_implementation():
    assert isinstance(simulator_for("loop", "average", 2026), PoissonSameVenueAverageAdapter)


def test_batch_and_vector_select_their_adapters():
    assert isinstance(simulator_for("batch", "average", 2026), IterationBatchAdapter)
    assert isinstance(simulator_for("vector", "average", 2026), FullVectorAdapter)


def test_every_simulator_carries_the_season():
    """SimulationService raises if the adapter's season disagrees with params."""
    for name in ("loop", "batch", "vector"):
        assert simulator_for(name, "average", 2026).season == 2026


def test_an_unknown_name_names_the_valid_ones():
    with pytest.raises(ValueError) as excinfo:
        simulator_for("turbo", "average", 2026)

    assert "loop" in str(excinfo.value)
```

- [ ] **Step 2: Run and verify it fails**

Run: `docker-compose run --rm app pytest /tests/test_batch_entrypoint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brasileirao_simulator.entrypoints.simulators'`

- [ ] **Step 3: Add the factory**

Create `src/brasileirao_simulator/entrypoints/simulators.py`:

```python
"""Choosing a simulator by name.

loop is the original per-season implementation and stays the default: it is the
reference the batch paths are validated against.
"""

from brasileirao_simulator.adapters.batch_poisson_adapter import (
    FullVectorAdapter,
    IterationBatchAdapter,
)
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)


SIMULATORS = {
    "loop": PoissonSameVenueAverageAdapter,
    "batch": IterationBatchAdapter,
    "vector": FullVectorAdapter,
}


def simulator_for(name: str, strategy: str, season: int):
    if name not in SIMULATORS:
        raise ValueError(f"unknown simulator {name!r}; choose one of {sorted(SIMULATORS)}")
    return SIMULATORS[name](strategy, season)
```

- [ ] **Step 4: Wire the flag into both entrypoints**

In BOTH `current_probabilities.py` and `backfill.py`, replace the
`PoissonSameVenueAverageAdapter` import with:

```python
from brasileirao_simulator.entrypoints.simulators import simulator_for
```

In `current_probabilities.py`, change the signature and the adapter line:

```python
def current_probabilities(
    season: int, iterations: int = 100, date: str = None, simulator: str = "loop"
) -> None:
    params = SimulationParams(
        season=season,
        iterations=iterations,
        max_batch_size=25,
        ignore_results_after=date,
        load_results=False,
    )
    simulation_service = SimulationService(
        persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season),
        simulator_adapter=simulator_for(simulator, params.strategy, season),
        params=params,
    )
    simulation_service.run_simulation()
```

and in its `__main__` block add the argument and pass it:

```python
    parser.add_argument(
        "--simulator",
        choices=["loop", "batch", "vector"],
        default="loop",
        help="loop is the reference implementation; batch and vector are faster.",
    )
    args = parser.parse_args()

    current_probabilities(args.season, args.iterations, args.date, args.simulator)
```

In `backfill.py`, the same change to its `backfill` function:

```python
def backfill(season: int, date: str, iterations: int = 200, simulator: str = "loop") -> None:
    params = SimulationParams(
        season=season,
        iterations=iterations,
        max_batch_size=100,
        ignore_results_after=date,
        load_results=True,
    )
    simulation_service = SimulationService(
        persistence_adapter=PickleAdapter(RESULTS_DIRECTORY, season),
        simulator_adapter=simulator_for(simulator, params.strategy, season),
        params=params,
    )
    simulation_service.run_simulation(print_results=False)
```

with the identical `--simulator` argument added to its parser, and its loop
calling `backfill(args.season, date, args.iterations, args.simulator)`.

Change nothing else about either entrypoint. In particular leave
`pending_dates`, `--force` and the date-slicing alone.

- [ ] **Step 5: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: 54 passed (50 + 4 new).

- [ ] **Step 6: Measure all three on the same date**

Run:
```bash
docker-compose run --rm app python3 -c "
import time
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.simulators import simulator_for
t = Tables(SeasonData(2026))
f = t.enriched_tidy_fixtures(blank_from_date='2026-05-03')
r = t.remaining_games(blank_from_date='2026-05-03')
for name in ('loop', 'batch', 'vector'):
    s = simulator_for(name, 'average', 2026)
    t0 = time.time()
    if name == 'loop':
        for _ in range(100):
            s.get_brasileirao_standings(s.simulate_fixtures(f, r))
    else:
        s.simulate_batch(f, r, 100)
    print(f'{name:7s} 100 iterations: {time.time()-t0:7.2f}s')
"
```
Record the three numbers in the report. Expected shape: `batch` and `vector` both far below `loop`, with `vector` fastest.

- [ ] **Step 7: Update the README**

Add a short section documenting `--simulator`, stating that `loop` is the default and the reference implementation, that `batch` and `vector` are equivalent models, and that `vector` gives up the ability to vary a team's parameters within a simulated season. Include the three measured timings from Step 6.

- [ ] **Step 8: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/ tests/test_batch_entrypoint.py README.md
git commit -m "feat: choose a simulator with --simulator"
```

---

### Task 6: Close the two gaps the batch path leaves

`log_batch` populates title, relegation and positions, but not `brasileirao_relegation_points` or `match_results`. Both are exported, so a batch run would write CSVs with those sections empty.

**Files:**
- Modify: `src/brasileirao_simulator/domain/result_logger.py`
- Modify: `src/brasileirao_simulator/domain/batch_simulation.py`
- Modify: `tests/test_batch_adapter.py`

**Interfaces:**
- Consumes: `BatchOutcome` (now also carrying the final points table).
- Produces: `BatchOutcome.points: np.ndarray` shape `(iterations, n_teams)`; `SeasonBaseline` gains `home_name`/`away_name` (`list[str]`, per fixture) so match keys can be built; `log_batch` fills all five counters.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_batch_adapter.py`:

```python
def test_log_batch_fills_every_counter_the_exports_read():
    fixtures, remaining = _frames()
    adapter = IterationBatchAdapter("average", 2026)
    outcome = adapter.simulate_batch(fixtures, remaining, 25)
    teams = sorted(fixtures[fixtures["season"] == 2026]["team_name"].unique())

    logger = ResultLogger()
    logger.log_batch(outcome, teams)
    results = logger.get_results()

    assert results["brasileirao_relegation_points"], "relegation points never populated"
    assert results["match_results"], "match results never populated"

    a_match = next(iter(results["match_results"].values()))
    assert a_match["home"] + a_match["draw"] + a_match["away"] == 25
    assert "round_" in a_match
```

- [ ] **Step 2: Run and verify it fails**

Run: `docker-compose run --rm app pytest /tests/test_batch_adapter.py -v`
Expected: FAIL on the `brasileirao_relegation_points` assertion.

- [ ] **Step 3: Carry points and team names through**

In `batch_simulation.py`, extend `SeasonBaseline` with the two name lists —
add these fields after `round_`:

```python
    home_name: list
    away_name: list
```

Have `_fixture_arrays` collect them alongside the indices (append
`row.team_name` and `row.opponent_name` in the same loop) and return them, and
pass them into the `SeasonBaseline(...)` construction in `build_baseline`.

Extend `BatchOutcome` so the logger can reach the final points table and the
fixture metadata:

```python
@dataclass(frozen=True)
class BatchOutcome:
    """One batch of simulated seasons.

    rank holds 1-based final positions, one row per iteration; the goal arrays
    keep the per-fixture scorelines so per-match odds can be counted; points is
    the final points table, needed for the points-to-rank distribution; baseline
    carries the fixture metadata the counters are keyed by.
    """

    rank: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray
    points: np.ndarray
    baseline: SeasonBaseline
```

and return them from `simulate_batch`:

```python
    points, wins, goals_for, goals_against = _accumulate(baseline, home_goals, away_goals)

    return BatchOutcome(
        rank=rank_tables(points, wins, goals_for, goals_against),
        home_goals=home_goals,
        away_goals=away_goals,
        points=points,
        baseline=baseline,
    )
```

- [ ] **Step 4: Fill the remaining counters**

Extend `log_batch` in `result_logger.py`:

```python
        for position, team in enumerate(teams):
            for points, rank in zip(outcome.points[:, position], outcome.rank[:, position]):
                self.brasileirao_relegation_points[float(points)][int(rank)] += 1

        baseline = outcome.baseline
        for fixture in range(outcome.home_goals.shape[1]):
            key = f"{baseline.home_name[fixture]} x {baseline.away_name[fixture]}"
            home = outcome.home_goals[:, fixture]
            away = outcome.away_goals[:, fixture]
            self.match_results[key]["home"] += int((home > away).sum())
            self.match_results[key]["away"] += int((away > home).sum())
            self.match_results[key]["draw"] += int((home == away).sum())
            self.match_results[key]["round_"] = int(baseline.round_[fixture])
```

- [ ] **Step 5: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -v`
Expected: 55 passed.

- [ ] **Step 6: Prove a batch pickle is interchangeable with a looped one**

Run:
```bash
docker-compose run --rm app python3 -c "
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
import pickle, tempfile, os
existing = pickle.load(open('files/pkl/2026/average_results_2026-05-03.pkl','rb'))
print('existing pickle keys :', sorted(existing))
from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.domain.result_logger import ResultLogger
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
t = Tables(SeasonData(2026))
f = t.enriched_tidy_fixtures(blank_from_date='2026-05-03')
r = t.remaining_games(blank_from_date='2026-05-03')
a = IterationBatchAdapter('average', 2026)
lg = ResultLogger(); lg.log_batch(a.simulate_batch(f, r, 50), sorted(f[f.season==2026].team_name.unique()))
fresh = lg.get_results()
print('batch pickle keys    :', sorted(fresh))
print('same keys            :', sorted(existing) == sorted(fresh))
"
```
Expected: the two key sets are identical. If they are not, the batch path writes a pickle the exports cannot read — stop and report.

- [ ] **Step 7: Commit**

```bash
git add src/brasileirao_simulator/domain/ tests/test_batch_adapter.py
git commit -m "feat: fill relegation points and match odds from a batch"
```

---

### Task 7: Verify against the reference implementation end to end

**Files:** none modified — verification only.

- [ ] **Step 1: Confirm the existing results are untouched**

Run:
```bash
docker-compose run --rm app python3 -c "
import pickle, glob, collections
c = collections.Counter()
for p in glob.glob('files/pkl/2026/average_results_2026-*.pkl'):
    c[sum(pickle.load(open(p,'rb'))['brasileirao_title'].values())] += 1
print('2026 backfill iteration counts:', dict(c))
print('2025 pickles:', len(glob.glob('files/pkl/2025/*.pkl')))
"
```
Expected: `{200: 64}` and 108. Nothing in this plan should have altered them.

- [ ] **Step 2: Run a full backfill on the batch path into a scratch directory**

Run:
```bash
docker-compose run --rm app python3 -c "
import time, shutil, os
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.simulators import simulator_for
from brasileirao_simulator.service_layer.simulation_service import SimulationService
scratch = 'files/pkl_batch_check'
shutil.rmtree(scratch, ignore_errors=True)
dates = backfill_dates(2026)
t0 = time.time()
for d in dates:
    p = SimulationParams(season=2026, iterations=200, max_batch_size=200, ignore_results_after=d, load_results=False)
    SimulationService(PickleAdapter(scratch, 2026), simulator_for('batch','average',2026), p).run_simulation()
print(f'{len(dates)} dates x 200 iterations on the batch path: {time.time()-t0:.1f}s')
" 2>&1 | tail -2
```
Record the elapsed time. The looped equivalent took 77 minutes.

- [ ] **Step 3: Compare the two title series**

Run:
```bash
docker-compose run --rm app python3 -c "
import pickle, glob, os
worst = 0.0; worst_at = None
for p in sorted(glob.glob('files/pkl_batch_check/2026/average_results_2026-*.pkl')):
    d = os.path.basename(p)
    ref = pickle.load(open(f'files/pkl/2026/{d}','rb'))['brasileirao_title']
    new = pickle.load(open(p,'rb'))['brasileirao_title']
    for team in set(ref) | set(new):
        gap = abs(ref.get(team,0) - new.get(team,0)) / 200
        if gap > worst: worst, worst_at = gap, (d, team)
print(f'largest title-probability gap across all 64 dates: {worst:.1%} at {worst_at}')
print('within sampling error for n=200 (about 3.5pp per side):', worst < 0.10)
"
```
Expected: the largest gap is under 10 percentage points. Both paths draw 200 samples from the same distribution, so differences of a few points are expected; a large systematic gap means the models differ and must be investigated, not accepted.

- [ ] **Step 4: Clean up the scratch directory**

Run: `docker-compose run --rm app python3 -c "import shutil; shutil.rmtree('files/pkl_batch_check', ignore_errors=True)"`

- [ ] **Step 5: Confirm a clean tree**

Run: `git status --short`
Expected: only the pre-existing untracked `.DS_Store`, `all.csv`, `leagues.json`. No modified source, no scratch directory left behind.

---

## Notes for the executor

- **The reference implementation is not to be modified.** `PoissonSameVenueAverageAdapter`, `PoissonWeightedVenueAverageAdapter` and `FixtureSimulatorPort` are what the batch path is checked against. Changing them invalidates every equivalence test in this plan.
- **`np.lexsort` takes keys last-significant-first.** The primary sort key goes last in the tuple. Getting this backwards produces a plausible-looking table ranked by the wrong column.
- **Existing pickles must stay byte-comparable in structure.** Task 6 Step 6 is the check; if the key sets diverge, the exports silently lose a section.
- **Statistical assertions have a tolerance for a reason.** Do not tighten them until they fail, and do not loosen them to make a failure go away — a systematic gap between the two paths is a real defect.

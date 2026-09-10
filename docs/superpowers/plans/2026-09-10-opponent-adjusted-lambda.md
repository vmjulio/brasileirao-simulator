# Opponent-Adjusted Lambda Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct a club's scoring-rate estimate for the strength of the opponents it actually faced, behind a `$schedule_weight` knob that defaults to 0 and is bit-identical to today's model when off.

**Architecture:** Three new CTEs in `team_params_same_venue_average.sql` compute, per club and venue, the recency-weighted mean strength of the opponents in its lookback window and the league mean at the opposite venue. Their ratio, raised to `$schedule_weight`, multiplies the club's raw average. At weight 0 the multiplier is `x^0 = 1.0` exactly, so the whole feature is inert and removable.

**Tech Stack:** DuckDB SQL with `string.Template` substitution, Python 3, numpy, pandas, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-10-opponent-adjusted-lambda-design.md`

## Global Constraints

- Tests run in Docker only: `docker-compose run --rm app python3 -m pytest /tests -q`. Baseline is **168 passed, 13 deselected**. Neither number may drop.
- **Never write into `src/files/pkl/`** — the user's real results, gitignored and unrecoverable.
- Do NOT modify the reference adapters (`poisson_same_venue_average_adapter.py`, `batch_poisson_adapter.py`), `FixtureSimulatorPort`, or `standings.sql`.
- `DEFAULT_SCHEDULE_WEIGHT = 0.0`. No default anywhere else changes. This measures; it does not retune.
- `team_match_counts.sql` is out of scope and still carries an independent `rn <= 19`; the sweep must not be run with the `uncertain` adapter.
- Stage `git add` explicitly, file by file. Never `git add -A` — `.DS_Store`, `all.csv`, `leagues.json`, `__pycache__/`, and `src/files/pkl_*_backup*/` must stay untracked.
- Measured magnitudes this implementation must reproduce, taken on 2025 at `schedule_weight=1` before any code was written:

  | date | mean \|adj−1\| | max |
  |---|---:|---:|
  | 2025-03-31 | 4.9% | 18.9% |
  | 2025-07-13 | 6.4% | 35.8% |
  | 2025-09-27 | 4.7% | 13.9% |
  | 2025-12-07 | 4.7% | 16.2% |

---

### Task 1: Opponent-strength CTEs, exposed but not yet applied

Compute the adjustment factors and expose them for inspection, changing no team parameter. Splitting this from the application means the magnitude gate — the one that licenses reading a null result as a ceiling — gets its own review.

**Files:**
- Modify: `src/files/queries/team_params_same_venue_average.sql`
- Create: `src/files/queries/opponent_strength.sql`
- Modify: `src/brasileirao_simulator/domain/queries.py`
- Test: `tests/test_opponent_strength.py`

**Interfaces:**
- Consumes: `Queries(season, lookback=..., weight_recent=..., weight_mid=..., weight_base=..., prior_weight=...)` as it exists today.
- Produces: `Queries.opponent_strength()` returning SQL whose result frame has columns `team_name, venue, mean_opponent_defence, mean_opponent_attack, league_defence, league_attack, adjustment_for, adjustment_against`. Task 2 consumes the same CTEs inside the main query.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opponent_strength.py
import duckdb
import numpy as np
import pytest

from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables

# Measured on 2025 before any code existed. The implementation has to land on
# these, not merely produce numbers that differ from 1.
MEASURED = [
    ("2025-03-31", 0.049, 0.189),
    ("2025-07-13", 0.064, 0.358),
    ("2025-09-27", 0.047, 0.139),
    ("2025-12-07", 0.047, 0.162),
]


def factors(season, as_of_date):
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    frame = con.sql(Queries(season).opponent_strength()).df()
    con.close()
    return frame


@pytest.mark.parametrize("as_of_date,expected_mean,expected_max", MEASURED)
def test_adjustment_magnitudes_match_the_premeasured_table(as_of_date, expected_mean, expected_max):
    frame = factors(2025, as_of_date)
    deviation = (frame["adjustment_for"] - 1).abs()
    assert deviation.mean() == pytest.approx(expected_mean, abs=0.006)
    assert deviation.max() == pytest.approx(expected_max, abs=0.03)


def test_adjustment_is_not_inert_late_in_the_season():
    """The balanced round-robin argument says it should be; the 4/3/1 recency
    weights mean the effective opponent mix is never balanced."""
    deviation = (factors(2025, "2025-12-07")["adjustment_for"] - 1).abs()
    assert deviation.mean() > 0.02


def test_facing_weak_defences_lowers_the_attack_estimate():
    frame = factors(2025, "2025-09-27")
    weak = frame[frame["mean_opponent_defence"] > frame["league_defence"]]
    assert len(weak) > 0
    assert (weak["adjustment_for"] < 1).all()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_opponent_strength.py -q`
Expected: FAIL — `Queries` has no attribute `opponent_strength`.

- [ ] **Step 3: Write the SQL**

Create `src/files/queries/opponent_strength.sql`. It shares its first CTEs with the main query verbatim, so the two cannot drift on how the window is defined:

```sql
-- Adjustment factors for opponent strength, exposed for inspection and tests.
-- team_params_same_venue_average.sql applies the same logic inline; this file
-- exists so the factors can be measured on their own.
with base as (
    select team_name,
           venue,
           opponent_name,
           fixture_date,
           goals_for,
           goals_against,
           row_number() over (partition by team_name, venue order by fixture_date desc) as rn,
           case when row_number() over (partition by team_name, venue order by fixture_date desc) <= 1 then $weight_recent
                when row_number() over (partition by team_name, venue order by fixture_date desc) <= 5 then $weight_mid
                else $weight_base
           end as weight
    from new_fixtures
    where goals_for is not null
),

windowed as (
    select * from base where rn <= $lookback
),

raw_teams as (
    select team_name,
           venue,
           sum(goals_for * weight)::float/sum(weight) as goals_for_average,
           sum(goals_against * weight)::float/sum(weight) as goals_against_average
    from windowed
    group by 1,2
),

-- The opposite venue: a club's home matches were the opponent's away matches,
-- so its home attack is judged against opponents' away defensive rates.
opponent_strength as (
    select w.team_name,
           w.venue,
           sum(r.goals_against_average * w.weight)::float/sum(w.weight) as mean_opponent_defence,
           sum(r.goals_for_average * w.weight)::float/sum(w.weight) as mean_opponent_attack
    from windowed as w
        join raw_teams as r
          on r.team_name = w.opponent_name
         and r.venue = case when w.venue = 'home' then 'away' else 'home' end
    group by 1,2
),

league_venue as (
    select venue,
           avg(goals_for_average) as league_attack,
           avg(goals_against_average) as league_defence
    from raw_teams
    group by 1
)

select o.team_name,
       o.venue,
       o.mean_opponent_defence,
       o.mean_opponent_attack,
       l.league_defence,
       l.league_attack,
       coalesce(power(l.league_defence / nullif(o.mean_opponent_defence, 0), $schedule_weight), 1.0) as adjustment_for,
       coalesce(power(l.league_attack / nullif(o.mean_opponent_attack, 0), $schedule_weight), 1.0) as adjustment_against
from opponent_strength as o
    join league_venue as l on l.venue = case when o.venue = 'home' then 'away' else 'home' end
order by o.team_name, o.venue
```

- [ ] **Step 4: Add the parameter and the accessor**

In `src/brasileirao_simulator/domain/queries.py`, beside the existing `DEFAULT_PRIOR_WEIGHT`:

```python
# How hard team_params_same_venue_average.sql corrects a club's rate for the
# strength of the opponents in its window. 0 is no correction at all - the
# multiplier is x**0 == 1.0 exactly, so the query returns today's numbers bit
# for bit - and 1 is the full one-round adjustment. See
# docs/superpowers/specs/2026-09-10-opponent-adjusted-lambda-design.md.
DEFAULT_SCHEDULE_WEIGHT = 0.0
```

Add `schedule_weight: float = DEFAULT_SCHEDULE_WEIGHT` to `Queries.__init__`, store it, include it in the substitution mapping alongside `prior_weight`, and add:

```python
def opponent_strength(self) -> str:
    return self.read_sql("opponent_strength.sql")
```

**The test file must be created with `schedule_weight=1`** — pass it explicitly in `factors()`: `Queries(season, schedule_weight=1.0)`. Update the helper accordingly.

- [ ] **Step 5: Run the tests**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_opponent_strength.py -q`
Expected: PASS, 6 tests (4 parametrised + 2).

- [ ] **Step 6: Run the full suite**

Run: `docker-compose run --rm app python3 -m pytest /tests -q`
Expected: 174 passed, 13 deselected. Team parameters are untouched so far, so every existing test must still pass unchanged. If any existing test moved, stop — nothing in this task should affect them.

- [ ] **Step 7: Commit**

```bash
git add src/files/queries/opponent_strength.sql \
        src/brasileirao_simulator/domain/queries.py \
        tests/test_opponent_strength.py
git commit -m "Expose opponent-strength adjustment factors, not yet applied

Pinned to magnitudes measured on 2025 before the code existed: a typical
club's factor sits about 5% from 1 and the extremes 15-36%, at every stage
of the season. The balanced round-robin would make this inert by December;
the 4/3/1 recency weights mean the effective opponent mix never balances."
```

---

### Task 2: Apply the adjustment behind `$schedule_weight`

**Files:**
- Modify: `src/files/queries/team_params_same_venue_average.sql`
- Test: `tests/test_schedule_weight.py`

**Interfaces:**
- Consumes: `DEFAULT_SCHEDULE_WEIGHT` and the `schedule_weight` substitution from Task 1.
- Produces: `Queries(season).team_params_same_venue_average()` unchanged in output at the default; adjusted at any other weight. Task 3 sweeps it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schedule_weight.py
import duckdb
import numpy as np

from brasileirao_simulator.domain.queries import DEFAULT_SCHEDULE_WEIGHT, Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables

AS_OF = "2025-09-27"
NUMERIC = ["goals_for_average", "goals_against_average", "data_points"]


def params(schedule_weight):
    tables = Tables(SeasonData(2025))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    query = Queries(2025, schedule_weight=schedule_weight)
    frame = con.sql(query.team_params_same_venue_average()).df()
    con.close()
    return frame.sort_values(["team_name", "venue"]).reset_index(drop=True)


def test_default_is_zero():
    assert DEFAULT_SCHEDULE_WEIGHT == 0.0


def test_default_reproduces_today_exactly():
    """Not allclose. x**0 is exactly 1.0 and raw * 1.0 is exact, so the off
    state must be bit-identical or the multiplier is not being applied as
    specified."""
    baseline = params(0.0)
    explicit = params(DEFAULT_SCHEDULE_WEIGHT)
    for column in NUMERIC:
        assert np.array_equal(baseline[column].values, explicit[column].values)


def test_full_weight_moves_team_parameters():
    baseline = params(0.0)
    adjusted = params(1.0)
    deviation = np.abs(adjusted["goals_for_average"].values / baseline["goals_for_average"].values - 1)
    assert deviation.mean() > 0.02
    assert deviation.max() > 0.10


def test_partial_weight_sits_between():
    baseline = params(0.0)["goals_for_average"].values
    half = params(0.5)["goals_for_average"].values
    full = params(1.0)["goals_for_average"].values
    moved = np.abs(full - baseline) > 1e-9
    assert moved.sum() > 0
    assert (np.abs(half - baseline)[moved] < np.abs(full - baseline)[moved]).all()


def test_row_count_is_unchanged():
    assert len(params(0.0)) == len(params(1.0))
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_schedule_weight.py -q`
Expected: FAIL on `test_full_weight_moves_team_parameters` — the weight is accepted but nothing uses it yet.

- [ ] **Step 3: Wire the CTEs into the main query**

In `team_params_same_venue_average.sql`: add `opponent_name` to `base`'s select list, introduce `windowed` between `base` and `teams_`, change `teams_` to read `from windowed` and drop its `where rn <= $lookback`, then add `opponent_strength`, `league_venue` and `adjusted` (identical to `opponent_strength.sql`'s bodies), and point `teams_coalesce` at `adjusted` instead of `teams_`:

```sql
adjusted as (
    select t.team_name,
           t.venue,
           t.goals_for_average
             * coalesce(power(l.league_defence / nullif(o.mean_opponent_defence, 0), $schedule_weight), 1.0)
             as goals_for_average,
           t.goals_against_average
             * coalesce(power(l.league_attack / nullif(o.mean_opponent_attack, 0), $schedule_weight), 1.0)
             as goals_against_average,
           t.data_points
    from teams_ as t
        left join opponent_strength as o
               on o.team_name = t.team_name and o.venue = t.venue
        left join league_venue as l
               on l.venue = case when t.venue = 'home' then 'away' else 'home' end
),
```

Left joins, so a club with no matched opponents keeps its raw average via the `coalesce`. `championship_` stays as it is — it is a league aggregate and needs no correction.

Add a header comment stating that `$schedule_weight` defaults to 0, that `x**0 == 1.0` makes the query bit-identical to its unadjusted form, and pointing at the spec.

- [ ] **Step 4: Run the tests**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_schedule_weight.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Deliberate break — prove the equivalence gate bites**

Change `DEFAULT_SCHEDULE_WEIGHT` to `1.0`, run
`docker-compose run --rm app python3 -m pytest /tests/test_schedule_weight.py -q`.
Expected: FAIL on `test_default_is_zero` and `test_default_reproduces_today_exactly`.
Then revert, re-run to confirm PASS, and confirm `git diff src/brasileirao_simulator/domain/queries.py` is empty.

Record both outcomes in the task report. A gate that was never seen to fail is not a gate.

- [ ] **Step 6: Run the full suite**

Run: `docker-compose run --rm app python3 -m pytest /tests -q`
Expected: 179 passed, 13 deselected. Every pre-existing test must still pass — at the default this query returns exactly what it returned before.

- [ ] **Step 7: Commit**

```bash
git add src/files/queries/team_params_same_venue_average.sql tests/test_schedule_weight.py
git commit -m "Apply opponent-strength adjustment behind schedule_weight

Defaults to 0, where the multiplier is x**0 == 1.0 exactly and every team
parameter is bit-identical to before, so the feature ships inert. Removing
it later is deleting CTEs that multiplied by one."
```

---

### Task 3: Sweep the knob

**Files:**
- Modify: `src/brasileirao_simulator/entrypoints/variant_sweep.py`
- Test: `tests/test_variant_sweep.py`

**Interfaces:**
- Consumes: `Queries(..., schedule_weight=...)` from Task 2.
- Produces: `analytic_forecasts_for_date(season, date, lookback, adjustment_weight, weights, prior_weight, schedule_weight, tables=...)` and `--param schedule_weight` on the CLI.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_variant_sweep.py
def test_schedule_weight_changes_forecasts():
    from brasileirao_simulator.entrypoints.variant_sweep import analytic_forecasts_for_date

    baseline = analytic_forecasts_for_date(2025, "2025-09-27")
    adjusted = analytic_forecasts_for_date(2025, "2025-09-27", schedule_weight=1.0)
    assert set(baseline) == set(adjusted)
    differences = [
        abs(baseline[key][0] - adjusted[key][0]) for key in baseline
    ]
    assert max(differences) > 0.01


def test_schedule_weight_default_is_inert():
    from brasileirao_simulator.entrypoints.variant_sweep import analytic_forecasts_for_date

    baseline = analytic_forecasts_for_date(2025, "2025-09-27")
    explicit = analytic_forecasts_for_date(2025, "2025-09-27", schedule_weight=0.0)
    assert baseline == explicit
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_variant_sweep.py -q -k schedule`
Expected: FAIL — `analytic_forecasts_for_date` takes no `schedule_weight`.

- [ ] **Step 3: Thread the parameter through**

Add `schedule_weight: float = DEFAULT_SCHEDULE_WEIGHT` as a keyword argument to `analytic_forecasts_for_date`, `score_variant_matches` and `run_multi_season_sweep`, passing it into `Queries(...)`. Follow exactly the pattern `prior_weight` already uses — same argument position, same docstring treatment, same held-fixed CLI flag.

Then in `__main__`: add `"schedule_weight"` to `--param`'s `choices`, add a `--schedule-weight` flag for holding it fixed when another parameter is swept, and extend the `if args.param == ...` chain to parse floats for it.

- [ ] **Step 4: Run the tests**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_variant_sweep.py -q`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Smoke-test the CLI on one season**

Run:
```bash
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/variant_sweep.py \
  --param schedule_weight --values 0,1 --baseline 0 --seasons 2025
```
Expected: a two-row table. The `0` row must show `diff_vs_baseline` of exactly 0.

- [ ] **Step 6: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/variant_sweep.py tests/test_variant_sweep.py
git commit -m "Sweep schedule_weight"
```

---

### Task 4: Run the sweep and report

**Files:**
- Create: `src/files/exports/schedule_weight_sweep_multiseason.csv`
- Create: `src/files/exports/schedule_weight_sweep_pooled.csv`
- Create: `src/files/exports/schedule_weight_by_stage.csv`
- Create: `.superpowers/sdd/schedule-weight-report.md`

**Interfaces:**
- Consumes: the CLI from Task 3.

- [ ] **Step 1: Run the whole-season sweep**

```bash
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/variant_sweep.py \
  --param schedule_weight --values 0,0.25,0.5,0.75,1 --baseline 0 \
  --seasons 2016,2017,2018,2019,2020,2021,2022,2023,2024,2025 \
  --out files/exports/schedule_weight_sweep_multiseason.csv \
  --pooled-out files/exports/schedule_weight_sweep_pooled.csv
```

Run it in the background with an until-loop wait; it takes roughly 12 minutes at ~0.13s per date. Do not end your turn saying you will wait — that ends the turn without waiting.

- [ ] **Step 2: Produce the stage-of-season split**

Write a short script that reuses `score_variant_matches`, splits each season's scored matches into date-ordered thirds, and reports mean Brier and RPS per third per value. Write it to `files/exports/schedule_weight_by_stage.csv`.

This is a diagnostic, not the headline: the measured factors say no dilution is expected, so all three thirds should show a similar picture. Thirds disagreeing sharply is a signal to re-examine the implementation, not a finding.

- [ ] **Step 3: Judge it**

Apply the eight-of-ten-seasons rule: a value that beats the default in fewer than eight of ten seasons is noise, whatever the pooled mean says. Report per season and pooled, with paired bootstrap intervals, and RPS alongside Brier.

**A flat result is a real result.** Do not hunt for a winning value. If the sweep is flat while Task 1's magnitude test passes, that is the strongest ceiling evidence in the project: rates that move 5% without moving the score mean the remaining error is not in the rates.

- [ ] **Step 4: Write the report**

`.superpowers/sdd/schedule-weight-report.md`, covering: the sweep tables, how many of ten seasons each value beats the default in, the stage split, the deliberate-break results from Task 2, whether the optimum sits below 1.0 (which would indicate the one-round approximation over-corrects and argue for the jointly-fitted model), and a plain statement of what the result means for the ceiling question.

- [ ] **Step 5: Commit**

```bash
git add src/files/exports/schedule_weight_sweep_multiseason.csv \
        src/files/exports/schedule_weight_sweep_pooled.csv \
        src/files/exports/schedule_weight_by_stage.csv
git commit -m "Sweep opponent-strength adjustment across ten seasons"
```

---

## Self-review

**Spec coverage.** The `$schedule_weight` parameter and its CTEs are Tasks 1–2; the sweep is Task 3; the stage-of-season split and the eight-of-ten rule are Task 4. The exact-equivalence gate, the deliberate break, the magnitude gate and the direction check all have named tests. Out-of-scope items (iterating to a fixed point, the Dixon-Coles model, `team_match_counts.sql`, the `uncertain` adapter) appear in no task.

**Type consistency.** `schedule_weight` is a float everywhere, defaulting to `DEFAULT_SCHEDULE_WEIGHT` from `queries.py`, in the same argument position `prior_weight` occupies today. `opponent_strength.sql` and the inline CTEs in `team_params_same_venue_average.sql` share identical bodies, which Task 2 Step 3 states explicitly.

**Known duplication, accepted.** The window and opponent CTEs exist in both SQL files. Factoring them into one place would mean a shared fragment or a view, which buys less than it costs for two files that must stay identical by inspection anyway — and the magnitude test in Task 1 would catch a drift immediately, since it asserts against numbers measured independently of both.

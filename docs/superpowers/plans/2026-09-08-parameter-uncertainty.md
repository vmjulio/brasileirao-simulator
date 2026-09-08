# Parameter Uncertainty (C2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each simulated season draw its own team strengths from a distribution reflecting how much evidence backs them, without changing any team's expected strength.

**Architecture:** `build_baseline` gains the four per-team rates it currently collapses into per-fixture λ, plus each team's real match count per venue (from a new query, because the existing one masks it). A new module draws those rates from a Gamma whose mean is the current estimate, producing `(iterations, n_teams)` arrays; a second function combines them into `(iterations, n_games)` λ using the unchanged 50/50 blend. `simulate_batch` accepts 2-D λ. A third adapter wires it up. The per-season reference path and `IterationBatchAdapter` are untouched.

**Tech Stack:** Python 3.11, numpy 2.1.2, pandas 2.2.3, duckdb 1.1.1, pytest 8.3.3.

**Spec:** `docs/superpowers/specs/2026-09-08-parameter-uncertainty-design.md`

## Global Constraints

- **Tests run in Docker only.** `docker-compose run --rm app pytest /tests -q`. `tests/pytest.ini` sets `addopts = -m "not slow"`, so report BOTH counts: fast and full (`-m ""`). Baseline **56 + 3 deselected** fast, **59** full. Neither may drop.
- **`WORKDIR` is `/src`**; paths in `settings.py` stay relative.
- **Season is `int` in Python, `str` in SQL.** A mismatch gives an empty DataFrame with no exception.
- **Do NOT modify:** `poisson_same_venue_average_adapter.py`, `poisson_weighted_venue_average_adapter.py`, `FixtureSimulatorPort`, `standings.sql`, or `team_params_same_venue_average.sql`. They are the reference implementation, and the batch path's equivalence guarantee rests on them.
- **Do NOT modify** `IterationBatchAdapter`'s behaviour, `backfill.py`'s `backfill_dates`/`pending_dates`/`--force`, or anything under `src/files/pkl/` (the user's real results: 108 for 2025, 65 for 2026, gitignored and unrecoverable from git).
- **The mean must be preserved.** C2's expected λ must equal C1's exactly. Any change to a point estimate is a defect, not an improvement.
- `league_id = 71`, the lookback windows, head-to-head tiebreaking: still out of scope.
- **Every new test needs a deliberate-break check**: break what it guards, confirm that specific test fails, revert, confirm `git diff` clean. Report what you saw. Run break checks against a single test file.

---

### Task 1: Expose each team's real match count

The existing parameter query ends with `greatest(t.data_points, r.data_points) as data_points` where `r.data_points` is always 19 — so it reports 19 for every team regardless of evidence. C2 needs the true count, via a new query rather than a change to that file.

**Files:**
- Create: `src/files/queries/team_match_counts.sql`
- Modify: `src/brasileirao_simulator/domain/queries.py`
- Create: `tests/test_team_match_counts.py`

**Interfaces:**
- Produces: `Queries(season).team_match_counts() -> str`, and a query returning columns `team_name`, `venue`, `match_count`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_match_counts.py`:

```python
"""How many real matches back each team's parameters.

team_params_same_venue_average.sql reports data_points as
`greatest(t.data_points, 19)`, which is always 19 - so it cannot tell a
promoted side with 12 matches from an established one with 19. Parameter
uncertainty needs that distinction, hence a separate query.
"""

import duckdb

from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables


PROMOTED_2026 = {"Atletico Paranaense", "Chapecoense-sc", "Coritiba", "Remo"}


def _counts(season: int, as_of: str = None):
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    return con.sql(Queries(season).team_match_counts()).df()


def test_every_team_has_a_row_per_venue():
    counts = _counts(2026)

    assert len(counts) == 40
    assert set(counts["venue"]) == {"home", "away"}


def test_promoted_sides_have_fewer_matches_than_established_ones():
    """The distinction the existing query cannot express."""
    counts = _counts(2026)
    promoted = counts[counts["team_name"].isin(PROMOTED_2026)]["match_count"]
    established = counts[~counts["team_name"].isin(PROMOTED_2026)]["match_count"]

    assert promoted.max() < 19, "promoted sides should not have a full window"
    assert (established == 19).all(), "established sides span into the previous season"


def test_the_count_never_exceeds_the_window():
    """The lookback is 19 per venue; more matches do not widen it."""
    counts = _counts(2026)

    assert counts["match_count"].max() <= 19


def test_counts_shrink_at_an_earlier_as_of_date():
    """Fewer matches played means less evidence, which is the whole point."""
    early = _counts(2026, as_of="2026-03-01")["match_count"].sum()
    late = _counts(2026, as_of="2026-09-05")["match_count"].sum()

    assert early < late
```

- [ ] **Step 2: Run it and verify it fails**

Run: `docker-compose run --rm app pytest /tests/test_team_match_counts.py -q`
Expected: FAIL — `AttributeError: 'Queries' object has no attribute 'team_match_counts'`

- [ ] **Step 3: Write the query**

Create `src/files/queries/team_match_counts.sql`. It mirrors the `base`/`rn <= 19` window of `team_params_same_venue_average.sql` exactly, but reports the true count instead of blending toward a prior:

```sql
-- How many real matches back each team's per-venue parameters.
--
-- team_params_same_venue_average.sql reports `greatest(count, 19)`, which is
-- always 19 and therefore cannot distinguish a promoted side's 12 matches from
-- an established side's 19. Parameter uncertainty needs that number, so this
-- query reproduces the same window without the shrinkage blend.
with base as (
    select team_name,
           venue,
           row_number() over (partition by team_name, venue order by fixture_date desc) as rn
    from new_fixtures
    where goals_for is not null
)

select team_name,
       venue,
       count(*) as match_count
from base
where rn <= 19
group by 1, 2
```

- [ ] **Step 4: Add the accessor**

In `src/brasileirao_simulator/domain/queries.py`, alongside the other query methods:

```python
    def team_match_counts(self) -> str:
        return self.read_sql("team_match_counts.sql")
```

- [ ] **Step 5: Run and verify it passes**

Run: `docker-compose run --rm app pytest /tests -q`
Expected: 60 passed + 3 deselected (56 + 4 new).

- [ ] **Step 6: Deliberate-break check**

Change `where rn <= 19` to `where rn <= 5`, confirm `test_promoted_sides_have_fewer_matches_than_established_ones` fails (established sides would report 5, not 19), revert, confirm `git diff` clean on the SQL file. Report what you saw.

- [ ] **Step 7: Commit**

```bash
git add src/files/queries/team_match_counts.sql src/brasileirao_simulator/domain/queries.py tests/test_team_match_counts.py
git commit -m "feat: expose each team's real match count per venue"
```

---

### Task 2: Carry per-team rates and match counts on the baseline

`build_baseline` currently combines the four rates into per-fixture λ and discards the components. C2 needs them separately so they can be redrawn per iteration.

**Files:**
- Modify: `src/brasileirao_simulator/domain/batch_simulation.py`
- Modify: `tests/test_batch_simulation.py`

**Interfaces:**
- Consumes: `Queries(season).team_match_counts()` from Task 1.
- Produces: `SeasonBaseline` gains six arrays of shape `(n_teams,)` — `home_attack`, `home_defence`, `away_attack`, `away_defence`, `home_match_count`, `away_match_count` — and `build_baseline` gains a `match_counts: pd.DataFrame = None` parameter (optional, so `IterationBatchAdapter` keeps working unchanged).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_batch_simulation.py`:

```python
def test_baseline_carries_the_four_rates_per_team():
    """C2 redraws these per iteration, so they must survive as components and
    not only as the combined per-fixture lambda."""
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    for rates in (baseline.home_attack, baseline.home_defence,
                  baseline.away_attack, baseline.away_defence):
        assert rates.shape == (len(baseline.teams),)
        assert (rates > 0).all(), "a zero rate would make the Gamma undefined"


def test_the_rates_reproduce_the_combined_lambda():
    """The components must recombine into exactly the lambda C1 uses, or C2
    would silently change the model rather than only its spread."""
    fixtures, remaining, team_params, _ = _setup()
    b = build_baseline(fixtures, remaining, team_params, 2026)

    recombined_home = (
        ADJUSTMENT_WEIGHT * b.home_attack[b.home_team]
        + ADJUSTMENT_WEIGHT * b.away_defence[b.away_team]
    )
    recombined_away = (
        ADJUSTMENT_WEIGHT * b.away_attack[b.away_team]
        + ADJUSTMENT_WEIGHT * b.home_defence[b.home_team]
    )

    assert np.array_equal(recombined_home, b.lam_home)
    assert np.array_equal(recombined_away, b.lam_away)


def test_match_counts_default_to_the_full_window_when_absent():
    """Without a match_counts frame the baseline behaves as before, so
    IterationBatchAdapter is unaffected."""
    fixtures, remaining, team_params, _ = _setup()
    baseline = build_baseline(fixtures, remaining, team_params, 2026)

    assert (baseline.home_match_count == 19).all()
    assert (baseline.away_match_count == 19).all()
```

Add a fourth test that passes a real `match_counts` frame (fetch it the way Task 1's test does) and asserts the promoted sides come through below 19 while established ones are 19.

- [ ] **Step 2: Run and verify they fail**

Run: `docker-compose run --rm app pytest /tests/test_batch_simulation.py -q`
Expected: FAIL — `AttributeError: 'SeasonBaseline' object has no attribute 'home_attack'`

- [ ] **Step 3: Extend `SeasonBaseline`**

Add six fields after `played_round_`:

```python
    home_attack: np.ndarray
    home_defence: np.ndarray
    away_attack: np.ndarray
    away_defence: np.ndarray
    home_match_count: np.ndarray
    away_match_count: np.ndarray
```

- [ ] **Step 4: Build them in `build_baseline`**

Add a `match_counts: pd.DataFrame = None` parameter, and a helper that turns the averages dict into per-team arrays in `teams` order:

```python
def _team_rate_arrays(teams, averages):
    """The four rates per team, in `teams` order.

    A team missing from team_params falls back to MISSING_TEAM_AVERAGE, the same
    guard _fixture_arrays applies, so the components stay consistent with the
    combined lambda.
    """
    fallback = (MISSING_TEAM_AVERAGE, MISSING_TEAM_AVERAGE)
    home = [averages.get((team, "home"), fallback) for team in teams]
    away = [averages.get((team, "away"), fallback) for team in teams]

    return (
        np.array([h[0] for h in home], dtype=float),   # home_attack
        np.array([h[1] for h in home], dtype=float),   # home_defence
        np.array([a[0] for a in away], dtype=float),   # away_attack
        np.array([a[1] for a in away], dtype=float),   # away_defence
    )


def _match_count_arrays(teams, match_counts):
    """Real matches behind each team's parameters, per venue.

    Defaults to the full window when no frame is supplied, which keeps
    IterationBatchAdapter's behaviour identical.
    """
    full_window = 19
    if match_counts is None:
        return (
            np.full(len(teams), full_window, dtype=np.int64),
            np.full(len(teams), full_window, dtype=np.int64),
        )

    lookup = {
        (row.team_name, row.venue): row.match_count
        for row in match_counts.itertuples()
    }
    return (
        np.array([lookup.get((t, "home"), 0) for t in teams], dtype=np.int64),
        np.array([lookup.get((t, "away"), 0) for t in teams], dtype=np.int64),
    )
```

Call both from `build_baseline` and pass the results into the `SeasonBaseline(...)` construction.

- [ ] **Step 5: Run the full suite**

Run: `docker-compose run --rm app pytest /tests -q` then `-m ""`.
Expected: 64 + 3 deselected fast, 67 full. Existing tests unaffected — the new parameter is optional.

- [ ] **Step 6: Deliberate-break check**

Swap `home_defence` and `away_defence` in the `SeasonBaseline(...)` construction, confirm `test_the_rates_reproduce_the_combined_lambda` fails, revert, confirm clean. That test is the one guaranteeing C2 cannot silently change the model.

- [ ] **Step 7: Commit**

```bash
git add src/brasileirao_simulator/domain/batch_simulation.py tests/test_batch_simulation.py
git commit -m "feat: carry per-team rates and match counts on the baseline"
```

---

### Task 3: The Gamma draw

**Files:**
- Create: `src/brasileirao_simulator/domain/parameter_uncertainty.py`
- Create: `tests/test_parameter_uncertainty.py`

**Interfaces:**
- Consumes: `SeasonBaseline` from Task 2.
- Produces:
  - `TeamRateDraws` (frozen dataclass) with `home_attack`, `home_defence`, `away_attack`, `away_defence`, each `(iterations, n_teams)`.
  - `draw_team_rates(baseline, iterations, rng, n_eff_scale: float = 1.0) -> TeamRateDraws`
  - `fixture_lambdas(baseline, draws) -> tuple[np.ndarray, np.ndarray]`, each `(iterations, n_games)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parameter_uncertainty.py`:

```python
"""Drawing team strengths per simulated season.

The mean of every draw must equal C1's fixed estimate, so C1 and C2 differ in
spread alone. Spread comes from how many real matches back each estimate.
"""

import numpy as np

from brasileirao_simulator.domain.batch_simulation import ADJUSTMENT_WEIGHT, build_baseline
from brasileirao_simulator.domain.parameter_uncertainty import (
    draw_team_rates,
    fixture_lambdas,
)


AS_OF = "2026-05-03"
LOTS = 40_000


def _baseline_with_counts():
    """Baseline carrying real per-venue match counts (12-13 promoted, 19 else)."""
    import duckdb

    from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
        PoissonSameVenueAverageAdapter,
    )
    from brasileirao_simulator.domain.queries import Queries
    from brasileirao_simulator.domain.season_data import SeasonData
    from brasileirao_simulator.domain.tables import Tables

    tables = Tables(SeasonData(2026))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    remaining = tables.remaining_games(blank_from_date=AS_OF)
    team_params = PoissonSameVenueAverageAdapter("average", 2026).get_team_params(
        fixtures.copy()
    )

    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    match_counts = con.sql(Queries(2026).team_match_counts()).df()

    return build_baseline(fixtures, remaining, team_params, 2026, match_counts=match_counts)


def test_draws_are_centred_on_the_fixed_estimate():
    """Over many draws the mean must converge on C1's lambda - otherwise C2
    changes the model, not just its uncertainty."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, LOTS, np.random.default_rng(1))

    assert np.allclose(draws.home_attack.mean(axis=0), baseline.home_attack, rtol=0.02)
    assert np.allclose(draws.away_defence.mean(axis=0), baseline.away_defence, rtol=0.02)


def test_teams_with_less_evidence_are_drawn_more_widely():
    """The whole point: a parameter a third borrowed from a prior should not be
    as confident as one backed by 19 matches."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, LOTS, np.random.default_rng(2))

    spread = draws.home_attack.std(axis=0) / baseline.home_attack
    thin = baseline.home_match_count < 19
    assert thin.any(), "2026 should have promoted sides with a partial window"
    assert spread[thin].mean() > spread[~thin].mean()


def test_a_huge_n_eff_collapses_onto_the_fixed_estimate():
    """As evidence grows the draw degenerates to C1, which is the exact sense in
    which C2 contains C1."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, 500, np.random.default_rng(3), n_eff_scale=100_000)

    assert np.allclose(draws.home_attack, baseline.home_attack, rtol=1e-3)


def test_lambdas_use_the_same_blend_as_the_fixed_path():
    """Same 50/50 formula, different inputs - that is the entire change."""
    baseline = _baseline_with_counts()
    draws = draw_team_rates(baseline, 200, np.random.default_rng(4))
    lam_home, lam_away = fixture_lambdas(baseline, draws)

    assert lam_home.shape == (200, len(baseline.lam_home))
    expected_first = (
        ADJUSTMENT_WEIGHT * draws.home_attack[0, baseline.home_team[0]]
        + ADJUSTMENT_WEIGHT * draws.away_defence[0, baseline.away_team[0]]
    )
    assert lam_home[0, 0] == expected_first


def test_a_team_with_no_matches_is_not_drawn():
    """n_eff of zero makes the Gamma undefined; such a rate stays fixed."""
    baseline = _baseline_with_counts()
    counts = baseline.home_match_count.copy()
    counts[0] = 0
    stripped = replace(baseline, home_match_count=counts)   # dataclasses.replace

    draws = draw_team_rates(stripped, 100, np.random.default_rng(5))

    assert (draws.home_attack[:, 0] == stripped.home_attack[0]).all()
```

Fill in `_baseline_with_counts` following Task 2's test, and import `replace` from `dataclasses`.

- [ ] **Step 2: Run and verify they fail**

Expected: `ModuleNotFoundError: No module named '...parameter_uncertainty'`

- [ ] **Step 3: Implement**

Create `src/brasileirao_simulator/domain/parameter_uncertainty.py`:

```python
"""Drawing each simulated season its own team strengths.

The fixed model reuses one estimate of every team's scoring rate across all
iterations, so it cannot express that the estimate might be wrong - which is why
a side whose parameters are partly borrowed from a newcomer prior is treated as
confidently as one backed by nineteen matches.

Goals are Poisson, and Gamma is its conjugate, so a rate's uncertainty is Gamma.
Parameterised here so its MEAN is exactly the fixed estimate: only the spread is
new, and no team becomes systematically stronger or weaker.
"""

from dataclasses import dataclass

import numpy as np

from brasileirao_simulator.domain.batch_simulation import ADJUSTMENT_WEIGHT, SeasonBaseline


@dataclass(frozen=True)
class TeamRateDraws:
    """One drawn strength per team per iteration, shape (iterations, n_teams).

    Constant across a team's matches within an iteration: this models "we may be
    underrating them", not "they got hot".
    """

    home_attack: np.ndarray
    home_defence: np.ndarray
    away_attack: np.ndarray
    away_defence: np.ndarray


def draw_team_rates(
    baseline: SeasonBaseline,
    iterations: int,
    rng: np.random.Generator,
    n_eff_scale: float = 1.0,
) -> TeamRateDraws:
    """Sample each team's four rates once per iteration.

    n_eff_scale multiplies the evidence count, so a large value collapses every
    draw onto the fixed estimate - which is how C2 is shown to contain C1.
    """
    return TeamRateDraws(
        home_attack=_draw(baseline.home_attack, baseline.home_match_count, iterations, rng, n_eff_scale),
        home_defence=_draw(baseline.home_defence, baseline.home_match_count, iterations, rng, n_eff_scale),
        away_attack=_draw(baseline.away_attack, baseline.away_match_count, iterations, rng, n_eff_scale),
        away_defence=_draw(baseline.away_defence, baseline.away_match_count, iterations, rng, n_eff_scale),
    )


def _draw(rates, match_count, iterations, rng, n_eff_scale):
    """Gamma(shape=n_eff, scale=rate/n_eff): mean `rate`, variance rate^2/n_eff.

    A rate with no evidence behind it, or a rate of zero, has no distribution to
    draw from and is repeated unchanged.
    """
    n_eff = match_count * n_eff_scale
    drawable = (n_eff > 0) & (rates > 0)

    drawn = np.tile(rates, (iterations, 1))
    if drawable.any():
        shape = n_eff[drawable]
        scale = rates[drawable] / shape
        drawn[:, drawable] = rng.gamma(shape, scale, size=(iterations, drawable.sum()))

    return drawn


def fixture_lambdas(baseline: SeasonBaseline, draws: TeamRateDraws):
    """Per-iteration expected goals per fixture, shape (iterations, n_games).

    The same 50/50 attack-and-defence blend the fixed path uses; only the inputs
    now vary by iteration.
    """
    lam_home = (
        ADJUSTMENT_WEIGHT * draws.home_attack[:, baseline.home_team]
        + ADJUSTMENT_WEIGHT * draws.away_defence[:, baseline.away_team]
    )
    lam_away = (
        ADJUSTMENT_WEIGHT * draws.away_attack[:, baseline.away_team]
        + ADJUSTMENT_WEIGHT * draws.home_defence[:, baseline.home_team]
    )
    return lam_home, lam_away
```

- [ ] **Step 4: Run and verify they pass**

Run both suites. Expected: 69 + 3 deselected fast, 72 full.

- [ ] **Step 5: Deliberate-break check**

Change `scale = rates[drawable] / shape` to `rates[drawable]`, confirm `test_draws_are_centred_on_the_fixed_estimate` fails (the mean becomes `rate × n_eff`), revert, confirm clean. This is the check that the mean really is preserved.

- [ ] **Step 6: Commit**

```bash
git add src/brasileirao_simulator/domain/parameter_uncertainty.py tests/test_parameter_uncertainty.py
git commit -m "feat: draw team rates from a mean-preserving Gamma"
```

---

### Task 4: Accept per-iteration λ, and wire up the adapter

**Files:**
- Modify: `src/brasileirao_simulator/domain/batch_simulation.py`
- Create: `src/brasileirao_simulator/adapters/uncertain_params_adapter.py`
- Modify: `src/brasileirao_simulator/entrypoints/simulators.py`
- Modify: both entrypoints, `README.md`
- Create: `tests/test_uncertain_adapter.py`

**Interfaces:**
- Produces:
  - `simulate_batch(baseline, iterations, rng, vectorise_fixtures=False, lam_home=None, lam_away=None)` — the λ arguments override the baseline's and may be `(n_games,)` or `(iterations, n_games)`.
  - `UncertainParamsAdapter(strategy, season, rng=None, n_eff_scale=1.0)` implementing `BatchSimulatorPort`.
  - `--simulator {loop,batch,uncertain}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_uncertain_adapter.py` covering: the adapter produces a valid `(iterations, 20)` permutation rank table; it exposes `.season`; `simulator_for("uncertain", ...)` returns it; and — the important one — **at a large `n_eff_scale` its title distribution matches `IterationBatchAdapter`'s** (mark that one `@pytest.mark.slow`), since that is C2 degenerating to C1.

Add one test that `simulate_batch` accepts a 2-D λ of shape `(iterations, n_games)` and that passing a 1-D λ still behaves as before.

- [ ] **Step 2: Run and verify they fail**

- [ ] **Step 3: Let `simulate_batch` take λ overrides**

Add the two optional parameters, defaulting to the baseline's arrays, and index them per fixture as `lam[:, fixture]` when 2-D and `lam[fixture]` when 1-D. `rng.poisson` accepts either a scalar or a length-`iterations` array, so the per-fixture branch needs only the indexing to differ. Reject a 2-D λ when `vectorise_fixtures=True` with a clear error, or support it — state which you did and why in your report.

- [ ] **Step 4: Write the adapter**

`UncertainParamsAdapter` runs both queries (`team_params_same_venue_average` and `team_match_counts`), builds the baseline with counts, draws rates, computes λ, and calls `simulate_batch` with the overrides. Its docstring should say what it models and what it does not (form drift).

- [ ] **Step 5: Register it**

Add `"uncertain": UncertainParamsAdapter` to `SIMULATORS`, add it to both entrypoints' `--simulator` `choices`, and document it in the README next to the existing table — including that it is the same model with uncertainty added, and that `loop` remains the default.

- [ ] **Step 6: Run both suites, then the deliberate-break check**

Break the adapter so it passes `baseline.lam_home` instead of the drawn λ; confirm the large-`n_eff_scale` equivalence test still passes (it should — that is the degenerate case) but the spread test in `test_parameter_uncertainty.py` catches nothing. Note in your report which test DOES catch this, and if none does, say so — that is a coverage gap worth reporting rather than papering over.

- [ ] **Step 7: Commit**

---

### Task 5: Calibration backtest against completed 2025

The point of C2 is better forecasts, and 2025's true outcome is known. This measures whether it is actually better rather than merely different.

**Files:**
- Create: `src/brasileirao_simulator/entrypoints/calibration_backtest.py`
- Create: `tests/test_calibration.py`

**Interfaces:**
- Produces: `calibration_curve(forecasts, outcomes, bins=10) -> pd.DataFrame` and `brier_score(forecasts, outcomes) -> float`, plus an entrypoint taking `--season` and `--simulator`.

- [ ] **Step 1: Write the failing tests for the metrics**

A perfectly calibrated set of forecasts must produce a calibration curve on the diagonal and a Brier score matching a hand-computed value; a systematically overconfident set must show up as off-diagonal. Use synthetic forecasts so the metric is tested independently of the simulator.

- [ ] **Step 2-4: Implement the metrics and the entrypoint, verify**

The entrypoint replays a completed season date by date with a chosen simulator, extracts each team's title and relegation probability at each date, compares against what actually happened in that season's final table, and reports the calibration curve plus Brier score.

- [ ] **Step 5: Run the comparison and record it**

Run for 2025 with `loop` (baseline), `batch`, and `uncertain` at `n_eff_scale` of 1.0, and — by scaling — the equivalent of `n_eff = 19` for everyone. Record every Brier score and calibration curve in the report.

**State the expected direction before running:** Atlético-PR-like cases (a strong team with a partial window) should gain probability under C2, and Chapecoense-like cases should barely move. If `uncertain` scores WORSE than `batch` on Brier, say so plainly — that is a real result and it means the uncertainty is miscalibrated, not that the experiment failed.

- [ ] **Step 6: Commit**

---

### Task 6: Verify and report

**Files:** none modified.

- [ ] **Step 1:** Confirm `src/files/pkl/` still holds 108 (2025) and 65 (2026) pickles, unmodified.
- [ ] **Step 2:** Run a 2026 comparison at the current as-of date: `loop`, `batch`, `uncertain`, 2000 iterations each. Report Atlético-PR's title probability under each, and Chapecoense's relegation probability under each.
- [ ] **Step 3:** Confirm both suites pass and `git status` shows no modified tracked files.
- [ ] **Step 4:** Write a short summary of what changed and whether the backtest supports keeping C2 as an option.

---

## Notes for the executor

- **The mean is the contract.** If `test_draws_are_centred_on_the_fixed_estimate` or `test_the_rates_reproduce_the_combined_lambda` fails, C2 is changing the model rather than its uncertainty. Fix the code, never the assertion.
- **`rng.gamma(shape, scale)` — mind the parameterisation.** numpy takes *scale*, not *rate*. `Gamma(shape=k, scale=λ/k)` has mean λ. Using `1/scale` gives mean `λ/k²`, which would look plausible and be badly wrong.
- **A worse Brier score for C2 is a publishable result**, not a failure to hide. Report it.

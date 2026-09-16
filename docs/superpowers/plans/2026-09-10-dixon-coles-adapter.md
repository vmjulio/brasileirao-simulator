# Dixon-Coles Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Dixon-Coles simulator alongside the existing ones and measure its match-outcome Brier and RPS against the current model on 2025.

**Architecture:** A new domain module fits club attack and defence ratings jointly by maximum likelihood, so opponent strength cancels by construction rather than by correction. A new adapter exposes it under the name `dixon_coles`, registered beside `loop`, `batch` and `uncertain`. Nothing existing is modified except the simulator registry.

**Tech Stack:** Python 3, numpy, pandas, DuckDB, pytest, Docker Compose. **No new dependencies** — see Task 1 on why the fit needs no optimiser library.

**Spec:** `docs/superpowers/specs/2026-09-10-opponent-adjusted-lambda-design.md` (the Problem section motivates this; Dixon-Coles is named there as the principled alternative to the `$schedule_weight` patch, which this plan supersedes).

## Global Constraints

- Tests run in Docker only: `docker-compose run --rm app python3 -m pytest /tests -q`. Baseline **168 passed, 13 deselected**. Neither may drop.
- **Never write into `src/files/pkl/`.** Scratch simulation output goes to a temp directory.
- Do NOT modify the reference adapters, `FixtureSimulatorPort`, `standings.sql`, or `team_params_same_venue_average.sql`. This is purely additive: the only edit to existing code is adding one entry to `SIMULATORS` in `entrypoints/simulators.py`.
- **No new pip dependencies.** `requirements.txt` is duckdb, numpy, pandas, dateutil, pytz, six, tzdata, pytest. Adding scipy would mean rebuilding the image; the fit below does not need it.
- Stage `git add` explicitly, file by file. Never `git add -A`.
- This measures. Do not change any default or promote `dixon_coles` over `batch`, whatever the result.

---

### Task 1: Fit Dixon-Coles ratings

**Files:**
- Create: `src/brasileirao_simulator/domain/dixon_coles.py`
- Test: `tests/test_dixon_coles.py`

**The model.** For a match between home club `i` and away club `j`:

```
lambda_home = attack[i] * defence[j] * home_advantage
lambda_away = attack[j] * defence[i]
```

Goals are Poisson, with Dixon and Coles' correction to the four low scorelines, which independent Poissons under-predict:

```
tau(0,0) = 1 - lambda_home*lambda_away*rho
tau(1,0) = 1 + lambda_away*rho
tau(0,1) = 1 + lambda_home*rho
tau(1,1) = 1 - rho
tau(x,y) = 1               otherwise
```

**Why no optimiser library.** Without the `rho` correction, the maximum-likelihood attack and defence ratings satisfy a fixed point that can be iterated directly:

```
attack[i]  = (weighted goals scored by i) / (weighted sum over i's matches of defence[opponent] * venue_factor)
defence[i] = (weighted goals conceded by i) / (weighted sum over i's matches of attack[opponent] * venue_factor)
```

Alternate those two updates to convergence, rescaling so `mean(attack) == 1` each round to fix the model's one degeneracy (multiplying every attack by c and dividing every defence by c leaves all lambdas unchanged). `home_advantage` updates the same way, as total home goals over expected. `rho` is then a single scalar found by scanning the log-likelihood over a grid and refining — a 1-D problem that needs no gradient.

**Time decay.** Each match is weighted `exp(-xi * days_before_as_of)`. Default `xi = 0.0065`, Dixon and Coles' own value (half-life ~107 days). It is untuned here and is the obvious first thing to sweep later.

**Interfaces:**
- Consumes: a fixtures DataFrame with `team_name, opponent_name, venue, goals_for, goals_against, fixture_date` — the shape `Tables.enriched_tidy_fixtures` already returns.
- Produces:
  ```python
  @dataclass
  class Ratings:
      attack: dict     # team -> float, mean 1.0
      defence: dict    # team -> float
      home_advantage: float
      rho: float
      iterations: int
      converged: bool

  def fit(fixtures, as_of_date, xi=DEFAULT_XI, max_iterations=200, tolerance=1e-9) -> Ratings
  def lambdas(ratings, home_team, away_team) -> tuple[float, float]
  def outcome_probs(lam_home, lam_away, rho, max_goals=15) -> tuple[float, float, float]
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dixon_coles.py
import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.domain import dixon_coles


def synthetic(attack, defence, home_advantage, seed=0):
    """A league generated from known ratings, played twice against everyone."""
    rng = np.random.default_rng(seed)
    teams = list(attack)
    rows = []
    for home in teams:
        for away in teams:
            if home == away:
                continue
            for repeat in range(12):   # many repeats so the fit can recover the truth
                lam_h = attack[home] * defence[away] * home_advantage
                lam_a = attack[away] * defence[home]
                gh, ga = rng.poisson(lam_h), rng.poisson(lam_a)
                rows += [
                    {"team_name": home, "opponent_name": away, "venue": "home",
                     "goals_for": gh, "goals_against": ga, "fixture_date": "2025-01-01"},
                    {"team_name": away, "opponent_name": home, "venue": "away",
                     "goals_for": ga, "goals_against": gh, "fixture_date": "2025-01-01"},
                ]
    return pd.DataFrame(rows)


def test_recovers_known_ratings():
    """The whole point of the model: fitted ratings must match the ones the
    data was generated from, which is what 'opponent strength cancels by
    construction' means in practice."""
    attack = {"A": 1.6, "B": 1.0, "C": 0.6, "D": 1.2}
    defence = {"A": 0.7, "B": 1.0, "C": 1.4, "D": 0.9}
    fixtures = synthetic(attack, defence, home_advantage=1.3)

    ratings = dixon_coles.fit(fixtures, as_of_date="2025-06-01", xi=0.0)

    assert ratings.converged
    scale = np.mean(list(attack.values()))
    for team in attack:
        assert ratings.attack[team] == pytest.approx(attack[team] / scale, rel=0.10)
        assert ratings.defence[team] == pytest.approx(defence[team] * scale, rel=0.12)
    assert ratings.home_advantage == pytest.approx(1.3, rel=0.08)


def test_a_club_that_played_only_weak_defences_is_not_credited_for_it():
    """The failure mode this model exists to fix. Two clubs with identical
    attack ratings, one fed on weak defences, must come out equal - a raw
    goals-per-game average would not."""
    attack = {"Strong": 1.5, "Equal1": 1.0, "Equal2": 1.0, "Weak": 0.6}
    defence = {"Strong": 0.7, "Equal1": 1.0, "Equal2": 1.0, "Weak": 1.5}
    fixtures = synthetic(attack, defence, home_advantage=1.25, seed=3)

    ratings = dixon_coles.fit(fixtures, as_of_date="2025-06-01", xi=0.0)
    assert ratings.attack["Equal1"] == pytest.approx(ratings.attack["Equal2"], rel=0.12)


def test_attack_ratings_are_normalised():
    attack = {"A": 1.6, "B": 1.0, "C": 0.6}
    defence = {"A": 0.8, "B": 1.0, "C": 1.3}
    ratings = dixon_coles.fit(synthetic(attack, defence, 1.2), "2025-06-01", xi=0.0)
    assert np.mean(list(ratings.attack.values())) == pytest.approx(1.0, abs=1e-6)


def test_outcome_probs_sum_to_one():
    home, draw, away = dixon_coles.outcome_probs(1.6, 1.1, rho=-0.05)
    assert home + draw + away == pytest.approx(1.0, abs=1e-6)


def test_rho_lifts_the_draw_probability():
    """Dixon and Coles' correction exists because independent Poissons
    under-predict low-scoring draws."""
    _, draw_plain, _ = dixon_coles.outcome_probs(1.4, 1.2, rho=0.0)
    _, draw_corrected, _ = dixon_coles.outcome_probs(1.4, 1.2, rho=-0.08)
    assert draw_corrected > draw_plain


def test_time_decay_favours_recent_matches():
    """A club that improved sharply must rate higher under decay than without."""
    rows = []
    for repeat in range(20):
        rows += [{"team_name": "Riser", "opponent_name": "Foil", "venue": "home",
                  "goals_for": 0, "goals_against": 2, "fixture_date": "2025-01-10"},
                 {"team_name": "Foil", "opponent_name": "Riser", "venue": "away",
                  "goals_for": 2, "goals_against": 0, "fixture_date": "2025-01-10"},
                 {"team_name": "Riser", "opponent_name": "Foil", "venue": "home",
                  "goals_for": 4, "goals_against": 0, "fixture_date": "2025-06-10"},
                 {"team_name": "Foil", "opponent_name": "Riser", "venue": "away",
                  "goals_for": 0, "goals_against": 4, "fixture_date": "2025-06-10"}]
    fixtures = pd.DataFrame(rows)
    flat = dixon_coles.fit(fixtures, "2025-06-20", xi=0.0)
    decayed = dixon_coles.fit(fixtures, "2025-06-20", xi=0.02)
    assert decayed.attack["Riser"] > flat.attack["Riser"]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_dixon_coles.py -q`
Expected: FAIL — no module `dixon_coles`.

- [ ] **Step 3: Implement the module**

Write `fit` as the alternating fixed point described above, `lambdas` as the two products, and `outcome_probs` by summing the corrected joint Poisson pmf over a `max_goals` grid (truncation at 15 leaves residual ~1e-7, the same bound `variant_sweep`'s analytic path already uses).

Fit `rho` after attack/defence/home_advantage have converged, by evaluating the weighted log-likelihood over `np.linspace(-0.2, 0.2, 41)` and then refining around the best point. Clamp so no `tau` goes non-positive.

Document in the module docstring: what the model is, why the fixed point replaces an optimiser, that `xi` is untuned, and the normalisation that resolves the attack/defence degeneracy.

- [ ] **Step 4: Run the tests**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_dixon_coles.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Deliberate break**

Delete the rescaling line that enforces `mean(attack) == 1`. Confirm `test_attack_ratings_are_normalised` fails and `test_recovers_known_ratings` fails or drifts. Restore, confirm PASS, confirm `git diff` clean. Record both outcomes.

- [ ] **Step 6: Commit**

```bash
git add src/brasileirao_simulator/domain/dixon_coles.py tests/test_dixon_coles.py
git commit -m "Fit Dixon-Coles attack and defence ratings jointly"
```

---

### Task 2: Expose it as an adapter

**Files:**
- Create: `src/brasileirao_simulator/adapters/dixon_coles_adapter.py`
- Modify: `src/brasileirao_simulator/entrypoints/simulators.py`
- Test: `tests/test_dixon_coles_adapter.py`

**Interfaces:**
- Consumes: `dixon_coles.fit` from Task 1.
- Produces: `DixonColesAdapter(strategy, season)` satisfying the same port `IterationBatchAdapter` does, and `simulator_for("dixon_coles", ...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dixon_coles_adapter.py
from brasileirao_simulator.entrypoints.simulators import SIMULATORS, simulator_for


def test_dixon_coles_is_registered():
    assert "dixon_coles" in SIMULATORS


def test_existing_simulators_are_untouched():
    """Additive: adding a simulator must not disturb the three that exist."""
    assert {"loop", "batch", "uncertain"} <= set(SIMULATORS)


def test_adapter_produces_a_lambda_per_fixture():
    adapter = simulator_for("dixon_coles", "poisson_same_venue_average", 2025)
    assert adapter is not None
```

Extend with a test that the adapter's lambdas for a real 2025 date are finite, positive, and differ from `IterationBatchAdapter`'s for at least one fixture.

- [ ] **Step 2: Run to confirm it fails**

Run: `docker-compose run --rm app python3 -m pytest /tests/test_dixon_coles_adapter.py -q`
Expected: FAIL — `"dixon_coles"` not in `SIMULATORS`.

- [ ] **Step 3: Implement the adapter and register it**

Mirror `IterationBatchAdapter`'s interface. Add `"dixon_coles": DixonColesAdapter` to `SIMULATORS` and one paragraph to the module docstring saying what it is and that it is a measurement candidate, not the default.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `docker-compose run --rm app python3 -m pytest /tests -q`
Expected: **≥ 178 passed, 13 deselected**. No existing test may change.

- [ ] **Step 5: Commit**

```bash
git add src/brasileirao_simulator/adapters/dixon_coles_adapter.py \
        src/brasileirao_simulator/entrypoints/simulators.py \
        tests/test_dixon_coles_adapter.py
git commit -m "Register a dixon_coles simulator alongside the existing three"
```

---

### Task 3: Score it against the current model

**Files:**
- Create: `src/brasileirao_simulator/entrypoints/dixon_coles_backtest.py`
- Create: `src/files/exports/dixon_coles_vs_current.csv`
- Create: `.superpowers/sdd/dixon-coles-report.md`

**Method.** Match-outcome probabilities need **no Monte Carlo**: `dixon_coles.outcome_probs` is exact, and `variant_sweep.analytic_forecasts_for_date` already gives the current model's exact probabilities. Simulating would add sampling noise to a comparison that may turn on 0.002 RPS. Score both at **horizon 0** — the last forecast strictly before each match — reusing `assign_horizon0`, `played_matches` and `base_rate_probs` so both arms are scored identically.

- [ ] **Step 1: Write the entrypoint**

For each as-of date of 2025: fit Dixon-Coles on the fixtures visible at that date, produce outcome probabilities for every remaining fixture, and collect them the way `score_variant_matches` does. Report for both arms and the base-rate reference: mean Brier (0-2), mean RPS, log loss, and skill against the reference. Add a paired bootstrap on the per-match RPS difference, clustered by match, seeded for reproducibility.

- [ ] **Step 2: Run it on 2025**

```bash
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/dixon_coles_backtest.py \
  --season 2025 --out files/exports/dixon_coles_vs_current.csv
```

Run in the background with an until-loop wait. Roughly 110 fits plus 110 analytic passes — expect a few minutes.

- [ ] **Step 3: Sanity-check before believing it**

Three checks, all of which must pass before the result is reported:

1. **Same matches.** Both arms must score an identical set of match keys, and the count must match the current model's usual ~374 for 2025. Report any dropped.
2. **Draw rate.** Dixon-Coles' mean draw probability should sit closer to the observed ~28% than the current model's ~24%. If `rho` is doing its job this is the most visible effect.
3. **Ratings are sane.** Print the top and bottom five attack ratings at the final date. If they do not resemble the actual table, the fit is wrong regardless of the Brier score.

- [ ] **Step 4: Optional — season-level simulation**

Only if the match-level result is interesting: run `backfill.py --season 2025 --iterations 5000 --simulator dixon_coles` into a **scratch results directory**, never `src/files/pkl/`, to produce title and relegation numbers for comparison.

- [ ] **Step 5: Report**

`.superpowers/sdd/dixon-coles-report.md`: both arms' Brier, RPS and log loss with the paired interval; the three sanity checks; the top and bottom attack ratings; and a plain statement of whether this beats the current model, is indistinguishable from it, or is worse.

**A loss or a tie is a real result.** Dixon-Coles is the textbook approach and the current model is a marginal-average heuristic; if they tie, that is strong evidence for the ceiling, and it must be reported as plainly as a win. Do not tune `xi` or `rho` to chase a better number — note that they are untuned and leave sweeping them as follow-up work.

- [ ] **Step 6: Commit**

```bash
git add src/brasileirao_simulator/entrypoints/dixon_coles_backtest.py \
        src/files/exports/dixon_coles_vs_current.csv
git commit -m "Score Dixon-Coles against the current model on 2025"
```

---

## Self-review

**Spec coverage.** The model, the estimator and its normalisation are Task 1; the adapter and registry are Task 2; the comparison is Task 3. The "recovers known ratings" and "weak defences earn no credit" tests are the ones that verify the actual claim — that opponent strength cancels by construction.

**Type consistency.** `fit` returns `Ratings` everywhere; `lambdas` and `outcome_probs` take plain floats; the adapter converts to the arrays the port expects. `xi` is a float throughout, defaulting to `DEFAULT_XI` in `dixon_coles.py`.

**Deliberate scope exclusions.** Sweeping `xi` or `rho`, applying Dixon-Coles to other seasons, and `$schedule_weight` (superseded by this) are all follow-up. So is promoting the adapter to default, which stays out regardless of result.

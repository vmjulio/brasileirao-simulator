# Parameter uncertainty (C2)

**Date:** 2026-09-08
**Status:** Implemented, and **not adopted as the default** — the backtest did
not support it. See "Result" below before reading the rationale, which was
written in advance and is partly refuted by what followed.

## Result (2026-09-08, after implementation)

**C2 did not beat the fixed-λ model.** Brier scores against completed 2025, 110
dates at 20,000 iterations per date, lower is better:

| variant | title | relegation | combined |
|---|---:|---:|---:|
| fixed λ (batch) | **0.01981** | 0.09282 | 0.05631 |
| uncertain, n_eff = real matches | 0.02040 | **0.09184** | **0.05612** |
| uncertain, n_eff = 19 for all | 0.01998 | 0.09254 | 0.05626 |

Title forecasts got **worse** under uncertainty, in both configurations.
Relegation improved slightly. The combined difference (0.0002) is negligible.

**The failure mode this document predicted actually occurred.** The section below
states: *"If Chapecoense's relegation drops materially, the uncertainty is too
wide."* 2025's analogue is Sport Recife — promoted, finished last, genuinely
relegated — and their relegation probability **dropped** under widened
uncertainty, moving away from the truth.

That refutes this document's argument that "the data protects you from the bad
ones". The reason is structural and was not anticipated: when a probability sits
near 1, symmetric parameter uncertainty can only pull it *down*. For a team that
really is doomed, widening invents escape routes that do not exist. Any future
attempt should address that boundary asymmetry rather than tune `n_eff`.

Mirassol — 2025's analogue of the strong promoted side this design was motivated
by — did receive the predicted early-season boost. But it never won, so the
mechanism working exactly as designed was still a pure Brier cost.

**The test is underpowered, and this is the more important caveat.** A season has
one champion. Scoring 110 dates × 20 teams looks like 2,200 observations, but all
of them resolve against a single realised outcome, so the effective sample for
title calibration is close to n=1. The observed differences are of the same order
as Monte Carlo noise, though their consistent direction across both
configurations argues they are not purely noise.

**Status of the code:** built, tested, and statistically verified (the Gamma
parameterisation was checked by derivation: mean exactly λ, variance λ²/n_eff).
Available as `--simulator uncertain`, **not** the default. Kept so the question
can be revisited with better evidence rather than re-argued from first
principles.

**What would actually settle it:** scoring individual *match* outcomes rather
than the title. Each season has ~380 genuinely distinct results instead of one
champion, and match outcomes are what the model predicts directly. Mixing Poisson
over a Gamma λ yields a negative binomial, so C2 should measurably shift
scoreline probabilities — an effect the aggregated title metric cannot resolve.
A second completed season would also help: 2024 needs only a `2023/fixtures.csv`
as previous-year data plus a generated `dates.json`.

---

## Original design rationale (written before the result above)

## Problem

Every simulated season uses one fixed set of team strengths. A team's rate is
estimated once from its last 19 matches per venue, and all 200 simulated seasons
reuse that single number as if it were known exactly.

It is not known exactly, and the model's output shows the cost. At 2026-09-05,
with 131 matches still to play, title probability collapses onto two clubs:

| team | title odds | position |
|---|---:|---|
| Flamengo | 50.5% | 1st, 51 pts |
| Palmeiras | 48.0% | 2nd, 52 pts |
| Atlético Paranaense | 1.0% | 3rd, 45 pts |
| everyone else | ~0.5% combined | |

Atlético-PR sit 7 points off the lead with 13 rounds remaining and are given a
1% chance. The simulation cannot express "our estimate of this team might be
wrong", so a side whose parameters are partly borrowed from a stereotype is
treated with the same confidence as one backed by 19 matches of evidence.

That is precisely their situation. Their home attacking rate is built from 13
real matches blended with a hardcoded newcomer prior:

```
own 1.750 + prior 1.026  ->  1.521      (prior weight 32%)
```

Roughly a third of the number driving their title odds is a constant asserting
they are a typical promoted team, and 13 matches say they are not.

## Goal

Let each simulated season use its own draw of team strengths, reflecting how
much evidence actually backs each one, without changing any team's expected
strength.

## Approach

Draw each team's rate per iteration from a Gamma distribution whose mean is
exactly the current estimate:

```
rate ~ Gamma(shape = n_eff, scale = λ / n_eff)
      mean     = λ
      variance = λ² / n_eff
```

Four draws per team per iteration — home attack, home defence, away attack, away
defence — combined into per-fixture expected goals with the **unchanged**
formula:

```
home λ = 0.5 × (home team's home attack) + 0.5 × (away team's away defence)
away λ = 0.5 × (away team's away attack) + 0.5 × (home team's home defence)
```

Gamma is the conjugate prior for a Poisson rate, so this is the natural
distribution for "how uncertain is this goal rate", and it is closed-form and
cheap to sample.

**A team's drawn strength is constant across all its matches within one
simulated season.** That is what makes this *parameter uncertainty* ("we might
be underrating them") rather than *form drift* ("they got hot"). The two are
different mechanisms; form drift is deliberately out of scope here.

Note that parameter uncertainty is compatible with both the looping and the
fully-vectorised batch strategies, since λ still does not change within a
season. Only form drift requires the fixture loop.

### Why the mean is preserved

Setting the Gamma mean to C1's existing estimate means C1 and C2 differ in
exactly one respect: spread. No team becomes systematically stronger or weaker,
existing point estimates stay meaningful, and a C1-versus-C2 comparison isolates
the effect of uncertainty alone rather than confounding it with a re-estimation.

This also keeps the pessimistic newcomer prior doing its job. The observation
that promoted teams are usually bad is real and is encoded in the prior's
**mean**, which is untouched. What changes is only the claimed **confidence**.
The current model has this both ways: it borrows a stereotype for the estimate,
then treats the result as being as certain as 19 matches of evidence.

Widening does not rescue genuinely bad promoted teams, because their own results
dominate. Chapecoense are at 100% relegation off 25 real matches, and their own
home defence (2.217) is *worse* than the shrunk figure (1.861) — the prior
already flatters them. Symmetric widening around 1.861 barely moves a team that
far gone. Atlético-PR, whose own rate is better than their shrunk one, gain the
paths their results warrant.

### n_eff

`n_eff` is the effective number of matches behind an estimate, and it sets the
spread. Default: **the team's real match count at that venue** — 19 for
established sides, 12–13 for the four promoted ones this season.

The alternative, `n_eff = 19` for everyone, would leave a promoted team's
borrowed prior masquerading as hard evidence, which is the problem this design
exists to fix. The prior is two hardcoded constants that
`team_params_same_venue_average.sql` itself flags as provisional:

```sql
-- this can have the actual data of the championship, not some hard coded valued
```

Treating those as worth 7 matches of certainty overstates what they are.

`n_eff` is exposed as a tunable, because this is an empirical question and the
backtest below settles it with evidence rather than argument.

## Design

### A blocker: the real match count is not currently available

`team_params_same_venue_average.sql` ends with:

```sql
greatest(t.data_points, r.data_points) as data_points
```

and `r.data_points` is always 19, so the query **always reports 19** regardless
of how many matches a team actually has. (This masking is why an earlier reading
of this codebase wrongly concluded no shrinkage was active anywhere.)

C2 therefore needs the raw count. It gets a **new, separate query** rather than
a modification of the existing one: that file feeds the reference adapter, and
the equivalence guarantee for the whole batch path rests on it being untouched.

New `files/queries/team_match_counts.sql` returning `team_name`, `venue`,
`match_count` — the same `rn <= 19` window, without the shrinkage blend.

### Components

**`domain/parameter_uncertainty.py`**

```python
def draw_team_rates(
    baseline: SeasonBaseline,
    match_counts: dict[tuple[str, str], int],
    iterations: int,
    rng: np.random.Generator,
    n_eff_cap: int = 19,
) -> TeamRateDraws: ...
```

Returns arrays of shape `(iterations, n_teams)` for each of the four rates.
A rate whose λ is 0 draws 0 (Gamma is undefined at a zero mean).

**`SeasonBaseline`** gains the per-team rates it currently discards. Today it
stores only the combined per-fixture λ; C2 needs the four components per team so
they can be redrawn. This is additive — C1's `lam_home` / `lam_away` stay.

**`adapters/uncertain_params_adapter.py`** — `UncertainParamsAdapter`, a third
adapter beside `IterationBatchAdapter`. Reuses `build_baseline` for the played
table, the fixture list and the team index, and overrides only λ construction.

**`simulate_batch`** accepts λ arrays of shape `(iterations, n_games)` as well as
`(n_games,)`. The per-fixture draw already loops fixtures and draws all
iterations at once, so a per-iteration λ column slots in directly.

**Entrypoints** gain `uncertain` in `--simulator {loop,batch,uncertain}`.
`loop` remains the default; nothing about existing behaviour changes.

### Independence of attack and defence

Attack and defence for the same team are estimated from the same matches and are
therefore correlated in reality. This design draws them independently, which is
a simplification that slightly understates joint uncertainty. It is called out
rather than hidden, and can be revisited if the backtest suggests it matters.

## Validation

**Plumbing — exact.** As `n_eff` grows the Gamma collapses onto its mean, so C2
at a very large `n_eff` must reproduce C1's distribution to within sampling
error. This tests the wiring independently of whether the modelling is good.

**Modelling — empirical.** 2025 is complete, so the true outcome is known.
Replay it and measure calibration: of all events forecast at roughly 20%, did
roughly 20% occur? Compare:

- C1 (fixed λ) as the baseline — "is C2 better" is meaningless without it
- C2 at `n_eff` = real match count
- C2 at `n_eff` = 19 for everyone
- C2 at one or two intermediate settings

Report a calibration curve and a proper scoring rule (Brier score) per variant.
A full 64-date season replay costs ~11 seconds, so this is cheap.

**Expected direction, stated in advance so the result can contradict it:**
Atlético-PR's title odds rise materially from 1%; Chapecoense's relegation odds
stay near 100%. If Chapecoense's relegation drops materially, the uncertainty is
too wide and the backtest should show worse calibration.

## Scope

In scope: the Gamma draw, the match-count query, `SeasonBaseline`'s per-team
rates, the new adapter, the `--simulator` choice, and the 2025 calibration
backtest including a C1 baseline.

Out of scope, deliberately:

- **Form drift within a season (C3).** A different mechanism, needing the
  fixture loop and a damping scheme to avoid runaway feedback. Possible future
  sibling; this design does not preclude it.
- **Changing any point estimate.** The mean stays exactly C1's, or the
  comparison is confounded.
- **Correlated attack/defence draws.** See above.
- **`league_id`, the lookback windows, head-to-head tiebreaking.** Still
  deferred.
- **Modifying `team_params_same_venue_average.sql` or the reference adapters.**
  They are what everything is validated against.

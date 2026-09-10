# Findings

What this model can and cannot do, and the evidence behind each claim. Every
number here was measured in this repository and can be reproduced from the
entrypoint named beside it. The same comparisons, charted per season with their
intervals, are in `docs/reports/model_ledger.html` (rebuild with
`python docs/reports/build_model_ledger.py` after any export changes).

## The headline

**The simulator beats a base-rate reference by about 2.2% on match-outcome Brier.
Nothing built on Série A data alone improves on that. Feeding a joint model every
competition a club plays does: the same Dixon-Coles fit gains 0.0044 RPS when fed
Série B, Copa do Brasil and continental matches, in 6 of 6 seasons, and lands about
0.0024 ahead of the incumbent - roughly where the public forecaster sits.**

Four never-fitted constants and a Gamma-Poisson uncertainty variant fail to beat
the incumbent, and Dixon-Coles on league-only data loses to it 3-7. For a while
that read as a ceiling on the problem. It was a ceiling on the *data regime*:
every one of those experiments saw only Brasileirão fixtures. Section 3b is the
result that changed the reading, and section 2 is the outside forecaster whose
published recipe - twelve months, eight competitions, fitted jointly - it
corroborates.

Concretely, on 2025 (374 matches, horizon 0):

| | Brier (0-2) | RPS | log loss |
|---|---:|---:|---:|
| model | 0.6069 | 0.2079 | 1.0144 |
| base rate | 0.6248 | 0.2166 | 1.0394 |

The base rate here means "home wins 47% of the time in this league" and nothing
about which clubs are playing. Everything the model knows about the twenty clubs
buys **0.0087 RPS** over that.

## Everything that has been tried

Worth separating, because the list looks longer than it is: most of the
"simulators" in this repo are the **same model** with different execution
strategies, and only three genuinely different models have ever been scored.

**Three distinct models:**

| model | what it is | result |
|---|---|---|
| **fixed lambda** (incumbent) | `λ = 0.5 × attacker's own venue average + 0.5 × defender's venue average conceded`, over 19 matches per venue, recency-weighted 4/3/1 | nothing has beaten it |
| **parameter uncertainty** (`uncertain`, "C2") | same, but each simulated season draws its lambdas from a Gamma around the estimate | **lost** to fixed lambda |
| **Dixon-Coles** (`dixon_coles`) | attack and defence ratings solved jointly by maximum likelihood, plus time decay and a low-score correction - Série A data only | **lost**, better in 3 of 10 seasons |
| **Dixon-Coles, all competitions** (`dixon_coles_all`) | the identical fit, fed from `MatchStore`: Série A + Série B + Copa do Brasil (round of 16 on) + Libertadores/Sudamericana (group stage on) | **beats league-only Dixon-Coles 6 of 6**; vs the incumbent −0.0024, interval touching zero |

**Three execution strategies for the fixed-lambda model** - identical numbers, different speed:

| adapter | how | cost per date @ 5,000 |
|---|---|---:|
| `loop` | one simulated season at a time, in Python | 1,337 s |
| `batch` | vectorised across iterations | 1.9 s |
| `vector` (`FullVectorAdapter`) | vectorised across fixtures too | no faster than `batch`; not registered |

**Hyperparameter variants within the fixed-lambda family**, all swept across ten
seasons, none beating its default: lookback window (8/12/19/26), attack-defence
blend (0.3-0.7), recency weights (flat to 16/6/1), newcomer prior strength
(0/0.5/1/2).

**External comparison:** chancedegol.com.br - **they are ahead**, better in 4 of
5 seasons over 1,739 matches (pooled +0.0022, CI [-0.0012, +0.0056]).

**Specced but never built:** `$schedule_weight`, a cheap one-round opponent
correction. Superseded by building Dixon-Coles, which does the same job properly
and lost anyway.

So: **on Série A data alone, fixed lambda has not lost to anything we built**, and
has only genuinely beaten one rival (parameter uncertainty); against league-only
Dixon-Coles the pooled interval is [-0.00035, +0.00498], touching zero. **Given
every competition, Dixon-Coles moves ahead of it** - by 0.0024, with an interval
that just touches zero on six seasons - and roughly draws level with chancedegol,
who are 1-4 up on the incumbent. The accurate summary is *the incumbent is the
best of the league-only models, and the league-only models are the wrong family*.

## How things are measured

- **Horizon 0.** Every match is scored using the last forecast made *strictly
  before* kick-off, a median of one day out. Scoring a match against a forecast
  that already knew the result is the easiest way to fool yourself here.
- **The eight-of-ten-seasons rule.** A variant must beat the default in at least
  8 of the 10 completed seasons to count. This is the single most useful test in
  the project: it separated the real lookback finding (0 of 10, replicated
  everywhere) from a fake one (6 of 10, noise). Pooled means alone are not
  enough, and neither is one season.
- **RPS alongside Brier.** Home/draw/away is *ordered* - a draw sits between a
  home win and an away win - so RPS charges less for a near miss. Brier treats
  the three as unrelated labels. RPS is the standard for 1X2 forecasts.
- **Brier convention.** Ours is the original 1950 form, summed over three
  outcomes, range 0-2. Published figures around 0.2-0.3 are usually the halved
  or divided-by-three variants; check the divisor before comparing.

## 1. The four inherited constants are all at or near their optimum

Swept across 2016-2025 with `entrypoints/variant_sweep.py`. None was ever fitted;
all four were inherited values.

| constant | default | verdict |
|---|---|---|
| lookback window | 19 matches per venue | at optimum - 8 loses **10/10** seasons (pooled +0.0070, CI [0.0044, 0.0096]); 26 wins only 6/10 with the interval straddling zero |
| attack/defence blend | 0.5 | at optimum - nothing beats it in more than 3/10; only 0.3 shows a real penalty |
| recency weights | 4/3/1 | on the flat side of a cliff (see below) |
| newcomer prior | strength 1.0 | flat - 0.5 wins 6/10 on a negligible interval |

**The recency-weight result refuted its own prediction.** The expectation was
that flat weighting would be measurably worse, since recent form carries
information. It isn't:

| profile | last-5 share | diff vs 4/3/1 | 95% CI | beats in |
|---|---:|---:|---|---:|
| flat 1/1/1 | 26% | +0.0007 | [-0.0011, 0.0026] | 4/10 |
| mild 2/2/1 | 37% | -0.0003 | [-0.0012, 0.0006] | 5/10 |
| **current 4/3/1** | **53%** | - | - | - |
| steep 8/4/1 | 62% | +0.0016 | [0.0006, 0.0026] | 2/10 |
| very steep 16/6/1 | 73% | +0.0050 | [0.0029, 0.0072] | **0/10** |

Weighting recent form *harder* is real, replicated damage. Weighting it *less*
costs nothing measurable. Recent form carries **less** information than 4/3/1
assumes; the default survives because it sits on the flat side of a cliff, not
because it is right.

Same shape as the lookback result: the model is hurt by *discarding* history and
never by keeping more.

The newcomer prior touches 8 of 40 team-venues per season - the promoted clubs at
each venue, 81 across the decade - so its null is a tested null, not an untested
one.

## 2. An independent public forecaster is slightly ahead

chancedegol.com.br publishes the probabilities it gave for every played match
alongside the result, so both models score on identical matches. Their
probabilities sum to 1.000, so this is model against model with no bookmaker
overround to strip (`entrypoints/parse_chancedegol.py`,
`entrypoints/benchmark_chancedegol.py`). Seasons before 2025 came from the
Internet Archive; the site keeps only the current and previous year.

| season | matches | our RPS | their RPS | base rate | diff | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| 2022 | 376 | 0.2109 | **0.2048** | 0.2229 | +0.0061 | [-0.0012, +0.0134] |
| 2023 | 373 | 0.2200 | **0.2183** | 0.2238 | +0.0017 | [-0.0065, +0.0099] |
| 2024 | 376 | 0.2143 | **0.2121** | 0.2211 | +0.0021 | [-0.0036, +0.0078] |
| 2025 | 373 | **0.2079** | 0.2088 | 0.2166 | -0.0009 | [-0.0087, +0.0068] |
| 2026 | 241 | 0.2098 | **0.2080** | 0.2159 | +0.0018 | [-0.0069, +0.0110] |

**Pooled over 1,739 matches: +0.00220, 95% CI [-0.00121, +0.00561]. They are
better in 4 of 5 seasons, and win 52.5% of individual matches.** Positive means
we are worse.

**This supersedes an earlier reading.** On 2025 and 2026 alone it looked like a
tie with the sign flipping between seasons. Adding three archived seasons turns
that into a consistent small deficit: no single interval excludes zero, but the
direction is 4-1 against us and the pooled point estimate is the same 0.002 that
separates most things in this project.

So the honest statement is **not** parity - it is that a competent public
forecaster is slightly and consistently ahead of us, by about 0.002 RPS. Both of
us sit roughly 0.008-0.012 above a base rate, so the gap between the two models
is a quarter of the gap between either model and knowing nothing.

Caveats that cut in our favour, and are not resolved: their forecast timing is
unpublished, so if they publish closer to kick-off than our horizon 0 - with
confirmed lineups - part of the gap is information rather than model. Their 2022
page also lists one fixture twice with different probabilities (Santos x
Coritiba); both copies are dropped rather than picking one arbitrarily.

## 3. Dixon-Coles does not beat the marginal-average model

The textbook approach: solve for every club's attack and defence rating jointly
by maximum likelihood, so opponent strength cancels by construction rather than
by correction. Implemented in `domain/dixon_coles.py`, fitted by an alternating
fixed point (no optimiser library needed), with time decay and the low-score
correction. Scored by `entrypoints/dixon_coles_backtest.py`.

| season | matches | DC RPS | current RPS | diff | DC wins |
|---|---:|---:|---:|---:|---:|
| 2016 | 375 | 0.2101 | 0.2075 | +0.0026 | 184/375 |
| 2017 | 378 | 0.2393 | 0.2258 | **+0.0134** | 156/378 |
| 2018 | 377 | **0.1908** | 0.1935 | -0.0027 | 222/377 |
| 2019 | 376 | **0.2003** | 0.2026 | -0.0023 | 226/376 |
| 2020 | 377 | 0.2189 | 0.2160 | +0.0029 | 202/377 |
| 2021 | 377 | 0.2141 | 0.2120 | +0.0022 | 204/377 |
| 2022 | 377 | 0.2141 | 0.2109 | +0.0032 | 207/377 |
| 2023 | 373 | 0.2243 | 0.2200 | +0.0043 | 196/373 |
| 2024 | 376 | 0.2165 | 0.2143 | +0.0023 | 198/376 |
| 2025 | 374 | **0.2051** | 0.2079 | -0.0028 | 206/374 |

**Pooled: +0.00231, 95% CI [-0.00035, +0.00498]. Dixon-Coles better in 3 of 10
seasons.** Positive means Dixon-Coles is worse. It fails the eight-of-ten bar
decisively, and in the *opposite* direction - the pooled interval sits almost
entirely on the current model's side.

Three things worth keeping from this:

**2025 alone was misleading.** Scored on 2025 first, Dixon-Coles came out ahead
by 0.0028 with a CI of [-0.0106, +0.0054]. Run across ten seasons, that edge
vanishes. A single season is a coin flip; this is what the eight-of-ten rule is
for.

**Dixon-Coles wins more matches and loses the average.** In seven of ten seasons
it takes the majority of individual matches yet loses on mean RPS. Its errors are
heavy-tailed: narrow frequent wins, rare heavy losses. 2017 alone (+0.0134) is
five times any of its winning margins.

**The premise for expecting it to win was false.** The argument was that
independent Poissons under-predict low-scoring draws, and that Dixon and Coles'
rho correction would fix a ~4-point draw shortfall. There is no shortfall: 2025's
actual rates are home 50.3 / draw 26.1 / away 23.7, and the current model's mean
draw probability is 26.2%. The fitted rho came out slightly *positive* (+0.039),
pushing draws down. The mechanism had nothing to fix.

(The 47.0 / 28.5 / 24.5 figure that motivated the expectation was 2026's
partial-season rate, misattributed to 2025.)

The fit itself is sound - top five attack ratings at the end of 2025 were
Flamengo 1.64, Palmeiras 1.49, Mirassol 1.36, Botafogo 1.32, Vasco 1.18, which is
the real top of that table, with home advantage 1.535.

`xi` (time decay) and `rho` are untuned. Sweeping them is the obvious follow-up,
but a 3-of-10 result is not a tuning problem.

**It was a data problem.** See 3b.

## 3b. The same Dixon-Coles fit, fed every competition, wins 6 of 6

The decision gate of the multi-competition work
(`entrypoints/dixon_coles_backtest.py --data-vs-league`, exports
`dixon_coles_all_vs_league{,_pooled}.csv`). Arm A is the section-3 model on Série A
fixtures; arm B is the identical fit fed from `MatchStore` - Série A, Série B,
Copa do Brasil from the round of 16, Libertadores and Sudamericana from the group
stage - with 90-minute scores and `team_id` identity. Identical matches, horizon 0,
2019 as burn-in.

| season | matches | A (league) | B (all) | B − A | 95% CI |
|---|---:|---:|---:|---:|---|
| 2020 | 377 | 0.2189 | 0.2162 | −0.0027 | [−0.0061, +0.0004] |
| 2021 | 377 | 0.2141 | 0.2103 | −0.0038 | [−0.0085, +0.0007] |
| 2022 | 377 | 0.2141 | 0.2073 | −0.0068 | [−0.0136, −0.0006] |
| 2023 | 373 | 0.2243 | 0.2188 | −0.0055 | [−0.0111, −0.0006] |
| 2024 | 376 | 0.2165 | 0.2109 | −0.0057 | [−0.0112, −0.0007] |
| 2025 | 374 | 0.2051 | 0.2033 | −0.0019 | [−0.0055, +0.0015] |

**Pooled over 2,254 matches: B − A = −0.00439, 95% CI [−0.00640, −0.00244]. B
better in 6 of 6 seasons.** Against the incumbent, B is −0.00238 with CI
[−0.00515, +0.00047]; A is +0.0020. Arm A reproduces section 3's numbers to 1e-9,
so this is the same harness.

What it says: Dixon-Coles on league data was **starved, not wrong**. The
estimator was never the missing ingredient; the matches were. This is the first
result in the project to move RPS by more than 0.004 with a replicated,
interval-clear signal, and it corroborates chancedegol's published method from
the inside.

Three caveats, all real:

- **Six seasons.** The eight-of-ten rule cannot apply. Provisional by the
  project's own standard, even at 6 of 6.
- **B has not decisively beaten the incumbent.** −0.0024 with the interval
  touching zero. The gain is about the size of the gap to chancedegol, so B
  reaches them rather than passing them.
- **The fit does not converge on the all-competitions graph** (207 clubs;
  `converged=False` at 200 and 5,000 iterations). On the six full seasons Série A
  ratings drift under 9e-4 relative between a 200- and a 2,000-iteration cap, so
  the comparison above is clean. On the live 2026 season the drift is **0.43** -
  arm B's lambdas there depend on the cap. 2026 (241 matches, B − A −0.0045, CI
  [−0.0108, +0.0010]; B − incumbent −0.0041) is partial *and* not clean, and is
  quoted only with that beside it. Thin-data cup opponents are the likely
  cause; the fix belongs in `dixon_coles.py`.
- **The store had no 2026 Série A matches until 2026-09-10.** `MatchStore`
  reads only the `competitions/` tree and nothing wrote the current season's
  71 shard, so the first 2026 row (B − A −0.0010) was scored with arm B blind
  to the league itself. `refresh_competitions` now mirrors the season file
  into that shard; the 2026 figures above are from the re-score with it
  present. The six full seasons were unaffected (their 71 shards existed).

And one thing not yet separated: coverage is lopsided - ~380 Série B matches a
season against ~30 Copa do Brasil and 16-141 Sudamericana. Promoted clubs
arriving with a real rating instead of the hardcoded newcomer prior may be doing
much of the work. A Série-B-only ablation would say; it has not been run.

## 4. Parameter uncertainty (the `uncertain` adapter) does not help either

Drawing each simulated season's lambda from a Gamma centred on the point estimate
- modelling that the rates themselves are uncertain - loses to fixed lambda.

**Why**, established later: it adds *variance* around the mean without moving the
mean. If the remaining error were systematic bias, symmetric noise could not
correct it; it only blunts sharpness, which a proper scoring rule punishes. It
was aimed at the wrong error.

## 5. A retracted finding, and the trap behind it

An earlier result claimed the model was overconfident about relegation: clubs
given ~40% went down only ~29% of the time, across 192 forecasts.

**Retracted.** Those 192 forecasts came from **14 clubs**, one of which (Santos)
contributed 55 of them. Per club the rate is 4 of 14 - noise. Worse, there is
selection bias baked in: a club that lingers in a relegation band for months is
disproportionately one that survived, because clubs which actually go down stop
being borderline.

**The rule this produced:** when scoring repeated forecasts of the same entity,
the sample size is the number of *entities*, not the number of forecasts. The
dashboard's calibration panel now reports both counts and computes observed rates
per club, with Wilson intervals.

## 6. Relegation risk by points has no league-wide answer

The cut fell on **36** points in 2019 and on **43** in 2017. Whether a total is
safe depends entirely on how bunched the bottom of *that* table is, so pooling
across seasons answers a question nobody asks.

| framing | P(relegation) at 43 points |
|---|---:|
| pooled over all seasons and dates | 37.5% |
| pooled over 2026's dates | 48.6% |
| **2026 as of 2026-09-05** | **21.5%** |

Two structural facts drive it:

- **A stranded club takes a place out of contention.** Chapecoense at 99.4% in
  2026 means three places are contested, not four, so a given total goes further.
  Across nine seasons, the weaker the club finishing last, the lower the survival
  threshold: r = 0.64, but 95% CI [-0.03, 0.92] with n=9 - suggestive, not
  established, and 2021 (Chapecoense's 15 points) does not fit cleanly.
- **The curve's shape barely changes; its position does.** Risk always falls from
  75% to 25% across about 2.5 points. The 50% threshold ranges from 39.8 to 44.0.

Across ten seasons the most any relegated club managed was 43 and the fewest any
survivor needed was 39. A finished season has no probabilities left in it, so
those are a factual range, not a rate.

## 7. Schedule imbalance is real and constant, not just early-season

Adjustment factors for opponent strength, measured on 2025 at full strength:

| date | mean \|adj-1\| | max |
|---|---:|---:|
| 31 Mar | 4.9% | 18.9% |
| 13 Jul | 6.4% | 35.8% |
| 27 Sep | 4.7% | 13.9% |
| 7 Dec | 4.7% | 16.2% |

A balanced double round-robin ought to make this inert by December. It does not,
because the window is **recency-weighted 4/3/1**: the most recent match counts
four times, so the effective opponent mix is never balanced even when the raw set
is. A club coming off three matches against the bottom of the table carries an
inflated rate on the final day.

So a club's raw rate can be wrong by 5% typically and 15-36% at the extremes, from
opponent strength alone. Dixon-Coles corrects exactly this by construction - and
on league data alone still does not win. That looked like ceiling evidence until
3b: with every competition in the fit, the same correction is worth 0.0044 RPS.
The correction needed matches to work on.

The `$schedule_weight` design (a cheap one-round correction, spec at
`docs/superpowers/specs/2026-09-10-opponent-adjusted-lambda-design.md`) was
superseded by building Dixon-Coles directly and remains unimplemented.

## 8. Performance

| path | cost per as-of date |
|---|---:|
| `loop` @ 5,000 iterations | **1,337 s** |
| `batch` @ 5,000 | 1.9 s |
| `batch` @ 20,000 | 7.3-8.4 s (~2,400 season-simulations/sec) |
| analytic outcome probabilities (no simulation) | 0.13 s |

`batch` is ~700x `loop`. The analytic path exists because match-outcome
probabilities have a closed form: the Poisson maths itself is 0.36 ms, and the
remaining ~120 ms is DuckDB and DataFrame overhead. Use it for anything that only
needs match probabilities - it is exact, so it also removes Monte Carlo noise from
comparisons that turn on 0.002 RPS.

Two notes for anyone optimising further:

- **`FullVectorAdapter` (vectorising across fixtures as well as iterations)
  measured no faster than `batch`** and is deliberately not offered in the
  registry. The Poisson RNG dominates; the call structure does not matter *at
  large batch sizes*.
- **`backfill.py` hardcodes `max_batch_size=100`**, so a 20,000-iteration date is
  200 batch calls, each drawing only 100 samples per fixture. The sweep
  entrypoints use one shot per date. That asymmetry looks accidental and is the
  largest untested speed lever in the project.

## 9. Archive integrity

The forecast archive covers 2016-2026 at **20,000 iterations on every date**
(`entrypoints/topup_iterations.py` computes each date's shortfall, so
already-complete seasons cost nothing).

Getting there surfaced problems worth remembering:

- **2025 had 79 of 108 dates under 1,000 iterations**, some as low as **10** - a
  date simulated 10 times can only express probabilities in tenths. It had been
  built up incrementally during the season at whatever budget was passed at the
  time. A single per-season iteration figure hid this; counts are now recorded
  per date.
- **2024 covered 64 of 110 dates**, starting in July, and every one of its pickles
  predated `brasileirao_relegation_points`, so none could be resumed. Rebuilt.
- **An orphan pickle for 2024-12-02**, a day with zero fixtures, was being read as
  a forecast. The exporter now drives off the season's own date list rather than
  the pickle directory.
- **2016 has one cancelled match** - Chapecoense's last, after the LaMia crash -
  so "is this season finished?" must test fixture *status*, not the presence of
  scores. Testing for scores wrongly dropped a settled season from calibration.
- Older pickles carry a `bolao` field the current `ResultLogger` no longer writes.
  Rebuilding a season destroys it. Backups live in
  `src/files/pkl_{2024,2025}_backup_pre20k/` (gitignored, local only).

## Where the remaining value is

Not in the four constants, not in modelling uncertainty about the rates, and not
in a better estimator *on the same data* - those are closed. It is in **which
matches the estimator sees**. Section 3b is the evidence: the identical fit gains
0.0044 RPS from Série B, Copa do Brasil and continental matches, 6 of 6 seasons.
chancedegol's published method (twelve months, eight competitions, fitted jointly)
said the same thing from the outside; their edge is reproducible from public data,
and the "model or timing?" question is now mostly answered - model, via data.

Open, in order of what they would settle:

1. **Which competitions carry the gain.** A Série-B-only ablation of arm B tells
   whether the effect is breadth or simply that promoted clubs stop being a
   hardcoded guess. It decides what an Elo's division seeding is worth.
2. **Convergence on the all-competitions graph.** Clean on full seasons, not on
   the live one (drift 0.43 on 2026). Damped updates or a per-club minimum before a
   rating counts, in `dixon_coles.py`, before arm B is used for live forecasts.
3. **Elo on the same `MatchStore`** - E4 on the board - measured against
   `dixon_coles_all` on equal data, so the estimator question is finally asked
   with the data question already settled.
4. **Timing.** Still unverified whether chancedegol publishes closer to kick-off
   than our horizon 0; capture their upcoming-round forecasts at a known timestamp.

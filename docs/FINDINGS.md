# Findings

What this model can and cannot do, and the evidence behind each claim. Every
number here was measured in this repository and can be reproduced from the
entrypoint named beside it. The same comparisons, charted per season with their
intervals, are in `docs/reports/model_ledger.html` (rebuild with
`python docs/reports/build_model_ledger.py` after any export changes).

## The headline

**The simulator beats a base-rate reference by about 2.2% on match-outcome Brier.
Nothing built on Série A data alone improves on that. Feeding a model every
competition a club plays does: Dixon-Coles gains 0.0044 RPS from the extra data
in 6 of 6 seasons, and an Elo rating replayed over the same matches beats the
incumbent by 0.0036 RPS in 9 of 10 seasons (2016-2025) with an interval clear of
zero - the first model in the project to pass the eight-of-ten rule, on
defaults that a 24-level sweep could not improve. That same Elo is better than
the public forecaster of section 2 in 8 of 10 seasons, where the incumbent was
better in 2.**

Four never-fitted constants and a Gamma-Poisson uncertainty variant fail to beat
the incumbent, and Dixon-Coles on league-only data loses to it 3-7. For a while
that read as a ceiling on the problem. It was a ceiling on the *data regime*:
every one of those experiments saw only Brasileirão fixtures. Section 3b is the
result that changed the reading, and section 2 is the outside forecaster whose
published recipe - twelve months, eight competitions, fitted jointly - it
corroborates. Section 3d is that forecaster re-scored against the new model, and
is the clearest evidence the gain is real: the outside benchmark moves by roughly
what the internal comparison said it should.

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
| **Elo, all competitions** (`elo`) | one rating per club replayed chronologically over `MatchStore` (K 20, home advantage 85, division seeds), turned into two lambdas by a fitted goal-difference map and opponent-adjusted total-goals parameters | **beats the incumbent 9 of 10** (2016–2025), −0.0036 RPS, CI [−0.0054, −0.0018] — passes the eight-of-ten rule (3f); vs Dixon-Coles-all −0.0014 on 2020–2025, interval touching zero |

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

**External comparison:** chancedegol.com.br - **ahead of the incumbent**, better
in 8 of 10 seasons over 3,752 matches (2016-2025, pooled +0.0019, CI [-0.0003,
+0.0042]). **Not ahead of Elo:** on 2020-2025, arm C is better in 5 of 6 (pooled
-0.0017, CI [-0.0039, +0.0005]) where the incumbent was better in 1 of 6.

**Specced but never built:** `$schedule_weight`, a cheap one-round opponent
correction. Superseded by building Dixon-Coles, which does the same job properly
and lost anyway.

So: **on Série A data alone, fixed lambda has not lost to anything we built**, and
has only genuinely beaten one rival (parameter uncertainty); against league-only
Dixon-Coles the pooled interval is [-0.00035, +0.00498], touching zero. **Given
every competition, Dixon-Coles moves ahead of it** - by 0.0024, with an interval
that just touches zero on six seasons - **and Elo moves further ahead still**, by
0.0038 with the interval clear, and past chancedegol in the process. The accurate
summary is *the incumbent is the best of the league-only models, and the
league-only models are the wrong family*.

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

## 2. An independent public forecaster is slightly ahead of the incumbent

> **Superseded as a statement about the project, not about the incumbent.**
> Everything below still holds for the incumbent, which is what it measures. But
> the model that beat it — arm C, Elo on all competitions — closes this gap and
> edges ahead of chancedegol on the same matches. See 3d.

chancedegol.com.br publishes the probabilities it gave for every played match
alongside the result, so both models score on identical matches. Their
probabilities sum to 1.000, so this is model against model with no bookmaker
overround to strip (`entrypoints/parse_chancedegol.py`,
`entrypoints/benchmark_chancedegol.py`). Seasons before 2025 came from the
Internet Archive; the site keeps only the current and previous year. 2015 and
2014 pages were also archived and 2015 parses, but the incumbent cannot score
2015 (it needs a 2014 season folder), so the table starts in 2016. The re-fetched
2022 page parses byte-identical to the copy already committed.

| season | matches | our RPS | their RPS | base rate | diff | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| 2016 | 374 | 0.2076 | **0.2056** | 0.2110 | +0.0019 | [-0.0051, +0.0090] |
| 2017 | 378 | **0.2258** | 0.2298 | 0.2265 | -0.0039 | [-0.0119, +0.0041] |
| 2018 | 376 | 0.1937 | **0.1889** | 0.1976 | +0.0048 | [-0.0020, +0.0114] |
| 2019 | 372 | 0.2021 | **0.1978** | 0.2212 | +0.0044 | [-0.0033, +0.0122] |
| 2020 | 377 | 0.2160 | **0.2144** | 0.2206 | +0.0015 | [-0.0057, +0.0086] |
| 2021 | 377 | 0.2120 | **0.2104** | 0.2171 | +0.0016 | [-0.0056, +0.0087] |
| 2022 | 376 | 0.2109 | **0.2048** | 0.2229 | +0.0061 | [-0.0012, +0.0134] |
| 2023 | 373 | 0.2200 | **0.2183** | 0.2238 | +0.0017 | [-0.0065, +0.0099] |
| 2024 | 376 | 0.2143 | **0.2121** | 0.2211 | +0.0021 | [-0.0036, +0.0078] |
| 2025 | 373 | **0.2079** | 0.2088 | 0.2166 | -0.0009 | [-0.0087, +0.0068] |
| 2026 | 241 | 0.2098 | **0.2080** | 0.2159 | +0.0018 | [-0.0069, +0.0110] |

**Pooled over the ten full seasons, 3,752 matches: +0.00193, 95% CI [-0.00035,
+0.00424]. They are better in 8 of 10 seasons.** Positive means we are worse.
2026 (partial) is excluded from the pool; it reads +0.0018.

**This supersedes two earlier readings.** On 2025 and 2026 alone it looked like
a tie. On 2022-2026 it was a 4-1 deficit with the pool at +0.0022. Ten seasons
make it the longest comparison in the project and the first where the
eight-of-ten count can be read at all: chancedegol clears it against the
incumbent. The pooled interval still touches zero, just, so this is a consistent
edge, not a statistically clear one.

So the honest statement is **not** parity - it is that a competent public
forecaster is slightly and consistently ahead of us, by about 0.002 RPS. Both of
us sit roughly 0.008-0.012 above a base rate, so the gap between the two models
is a quarter of the gap between either model and knowing nothing.

Caveats that cut in our favour, and are not resolved: their forecast timing is
unpublished, so if they publish closer to kick-off than our horizon 0 - with
confirmed lineups - part of the gap is information rather than model. Their 2022
page lists one fixture twice with different probabilities (Santos x Coritiba),
and their 2016 page does the same for Fluminense x Atlético-MG; duplicated
fixtures are dropped rather than picking one copy arbitrarily.

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

## 3c. Elo on the same data beats the incumbent, 6 of 6

The four-arm backtest (`entrypoints/dixon_coles_backtest.py --four-arms`,
exports `four_arms{,_pooled}.csv`, report
`docs/superpowers/four-arm-backtest-report.md`). Arm C is an Elo rating per club,
replayed in date order over every admitted match of every competition, seeded by
division on first appearance (Série A 1500, Série B 1400, cup-only 1300, foreign
1450), K = 20 scaled by a goal-margin ladder (1 / 1.75 / 2.5), home advantage 85
in the expectation only. Two lambdas come from a decomposition: a linear map from
Elo gap to expected goal difference, fitted once on 2019 (slope 0.0040 goals per
Elo point), and per-club opponent-adjusted contributions to total goals over the
last 365 days. Outcome probabilities are independent Poisson. Same 2,254 matches
as 3b; arms A, B and the incumbent reproduce 3b's numbers exactly.

| season | matches | A | B | C (Elo) | incumbent | C − incumbent | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| 2020 | 377 | 0.2189 | 0.2162 | 0.2120 | 0.2160 | −0.0039 | [−0.0098, +0.0020] |
| 2021 | 377 | 0.2141 | 0.2103 | 0.2104 | 0.2120 | −0.0016 | [−0.0087, +0.0057] |
| 2022 | 377 | 0.2141 | 0.2073 | 0.2066 | 0.2109 | −0.0042 | [−0.0113, +0.0030] |
| 2023 | 373 | 0.2243 | 0.2188 | 0.2170 | 0.2200 | −0.0030 | [−0.0098, +0.0038] |
| 2024 | 376 | 0.2165 | 0.2109 | 0.2095 | 0.2143 | −0.0048 | [−0.0104, +0.0008] |
| 2025 | 374 | 0.2051 | 0.2033 | 0.2026 | 0.2079 | −0.0053 | [−0.0110, +0.0004] |

**Pooled: C − incumbent = −0.00381, 95% CI [−0.00635, −0.00114], C better in 6
of 6 seasons.** C − B = −0.00143, CI [−0.00302, +0.00015], 5 of 6 (2021 a dead
heat). C − A = −0.00582, CI clear. 2026 partial (241 matches): C 0.2047 against
the incumbent's 0.2098. Zero lambda fallbacks, zero dropped matches.

What it says: with the data question settled by 3b, the estimator question gets
its first clean answer. Elo and Dixon-Coles on equal data land within noise of
each other, as stated in advance in the design spec, and both beat the
league-only family. Elo is the first model in this project to beat the incumbent
with an interval clear of zero, and it does so on defaults nobody has tuned.

Caveats:

- **Six seasons.** Provisional by the eight-of-ten rule, like 3b. *Resolved for
  Elo in 3f: ten seasons, 9 of 10.*
- **Untuned.** K, home advantage, seeds, the margin ladder, the totals window and
  the per-competition weight (currently equal) have never been swept. That is
  `elo-sweeps`, and a flat result there is a finding too. *Resolved in 3g: flat;
  the defaults stand.*
- **Same coverage lopsidedness as 3b.** Série B dominates the extra data; the
  Série-B-only ablation still has not been run.

The chancedegol question this left open is settled in 3d.

## 3d. Against chancedegol, Elo erases the deficit and takes a narrow lead

Section 2 measured the incumbent against chancedegol and found us slightly and
consistently behind. That comparison is now re-run with arm C in our seat, on
chancedegol's own matches and their own published probabilities
(`entrypoints/benchmark_chancedegol.py --model elo-fixed-2019`, the line then
fixed on 2019). Nothing about their side
changes; only which of our models is scored. Elo starts in 2020: its
goal-difference line is fitted on 2019, so scoring 2019 or earlier would use the
future.

| season | matches | Elo RPS | their RPS | diff | 95% CI | incumbent's diff (§2) |
|---|---:|---:|---:|---:|---|---:|
| 2020 | 377 | **0.2120** | 0.2144 | −0.0024 | [−0.0087, +0.0038] | +0.0015 |
| 2021 | 377 | 0.2104 | 0.2104 | −0.0000 | [−0.0057, +0.0057] | +0.0016 |
| 2022 | 376 | 0.2069 | **0.2048** | +0.0021 | [−0.0034, +0.0075] | +0.0061 |
| 2023 | 373 | **0.2170** | 0.2183 | −0.0013 | [−0.0064, +0.0035] | +0.0017 |
| 2024 | 376 | **0.2095** | 0.2121 | −0.0026 | [−0.0071, +0.0019] | +0.0021 |
| 2025 | 373 | **0.2027** | 0.2088 | −0.0061 | [−0.0113, −0.0008] | −0.0009 |

Negative means we are better. On the 2,252 matches of the six full seasons,
scored identically for both of our models:

| our model | pooled diff vs chancedegol | 95% CI | better in |
|---|---:|---|---:|
| incumbent | +0.00203 | [−0.00094, +0.00499] | 1 of 6 |
| **arm C (Elo)** | **−0.00172** | **[−0.00390, +0.00046]** | **5 of 6** |

(First run, on 2022–2025 only, 1,498 matches: incumbent +0.00233, Elo −0.00203,
3 of 4. Adding 2020–2021 moved nothing material.)

2026, partial and excluded from the pool: Elo 0.2047 against their 0.2080
(−0.0033), where the incumbent was +0.0018.

What it says: the deficit is gone. The sign reverses, the season count goes from
1-5 against us to 5-1 for us (2021 a dead heat), and the swing is about 0.0038
RPS — the same as the
0.0038 Elo gained on the incumbent in 3c, which is what you would expect if the
two comparisons are measuring the same improvement against different opponents.

What it does **not** say is that we are now ahead. Elo's interval still touches
zero (+0.00046), so the honest reading is parity-or-slightly-better, not a lead.
The claim that survives is the negative one: **a competent public forecaster is
no longer ahead of us**, which is a retraction of section 2's conclusion rather
than a victory over it.

Caveats: six seasons, so the eight-of-ten rule cannot apply here either. The
timing caveat from section 2 is unchanged and still cuts in their favour — their
publication time is unpublished, and if it is closer to kick-off than our horizon
0, part of what is left is information rather than model. 2025 is the only season
whose interval excludes zero, and it is also the season where the incumbent was
already level; one season is not a pattern.

The incumbent's own numbers were re-derived in the same run as a gate, and
reproduce the committed `benchmark.json` to 1.7e-18.

## 3e. Blending our models does not beat Elo alone

*Exploratory (2026-09-10): scored from the four-arm per-match forecasts with
scratch scripts, not yet an entrypoint, so these numbers name no committed
export.*

Equal-weight averages of forecast probabilities, scored against Elo alone on the
same 2,254 matches, 2020–2025. Weights fixed in advance; nothing fitted on the
matches being scored.

| blend | blend − Elo | 95% CI | better than Elo in |
|---|---:|---|---:|
| Elo + Dixon-Coles all | +0.00011 | [−0.00068, +0.00090] | 3 of 6 |
| Elo + incumbent | +0.00027 | [−0.00105, +0.00160] | 1 of 6 |
| Elo + DC all + incumbent | −0.00007 | [−0.00115, +0.00103] | 3 of 6 |
| all four | +0.00057 | [−0.00071, +0.00188] | 2 of 6 |
| Elo + DC all, weight tuned leave-one-season-out | −0.00004 | [−0.00038, +0.00031] | — |
| Elo + chancedegol (diagnostic, 2,252) | −0.00020 | [−0.00129, +0.00088] | — |

The tuned blend puts 70–85% of its weight on Elo in every fold. The reason
nothing helps is that the models miss together: per-match RPS errors correlate
0.955 between Elo and Dixon-Coles-all, 0.856 between Elo and the incumbent, and
0.907 between Elo and chancedegol. Same data, same Poisson ending, same verdict
on almost every fixture; the one model that disagrees more (the incumbent)
disagrees by being worse.

**No kind of match belongs to another model either.** Sliced twelve ways
(season phase, promoted club, favourite strength, away favourite, rest, a
continental match just before), each slice found on 2020–2022 and confirmed on
2023–2025: the incumbent beats Elo in no slice, and Dixon-Coles-all edges it in
both halves in only two (rounds 11–28, both sides rested 96 h+), neither
confirmed. Elo's lead over Dixon-Coles concentrates in rounds 1–10 (−0.0044, CI
clear), rounds 29–38 (−0.0042, CI clear) and congested weeks where either side
rested under 72 h (−0.0037, CI clear) — the stretches where carrying a rating
across the break and counting cup and continental results matter most.

What it says: a blend of these models, averaged or switched by match type, is
not worth a second pipeline. A member that would earn a place has to see
information ours don't — closing odds, team news. Rest between matches is the
first such input to test (E10, `rest-hours-probe`); a first read correlates
0.025 with Elo's home-win residual, so the prior is small.

## 3f. Ten seasons: Elo passes the eight-of-ten rule

Every Elo result above was on six seasons, because the line that turns a rating
gap into goals was fitted once on 2019 and scoring 2019 or earlier would use the
future. Fitting it on the season before each scored season instead
(`entrypoints/elo_backtest.py --ten-seasons`, report
`docs/superpowers/elo-ten-seasons-report.md`) makes 2016–2025 scorable.

The scheme change on its own changes nothing: on 2020–2025 the previous-season
line minus the fixed-2019 line is −0.00027 [−0.00087, +0.00032]. The harness
reproduces the committed four-arm Elo and incumbent columns to 1e-9.

| season | Elo − incumbent | 95% CI |
|---|---:|---|
| 2016 | −0.0024 | [−0.0066, +0.0018] |
| 2017 | +0.0004 | [−0.0053, +0.0059] |
| 2018 | −0.0035 | [−0.0075, +0.0007] |
| 2019 | −0.0059 | [−0.0117, −0.0000] |
| 2020 | −0.0039 | [−0.0097, +0.0020] |
| 2021 | −0.0030 | [−0.0087, +0.0029] |
| 2022 | −0.0042 | [−0.0108, +0.0025] |
| 2023 | −0.0021 | [−0.0095, +0.0055] |
| 2024 | −0.0049 | [−0.0101, +0.0003] |
| 2025 | −0.0063 | [−0.0118, −0.0009] |

**Pooled over 3,760 matches: −0.00358, 95% CI [−0.00542, −0.00180]; Elo better
in 9 of 10 seasons.** Both halves hold on their own: 2016–2020 −0.00306
[−0.00532, −0.00077], 2021–2025 −0.00411 [−0.00682, −0.00137]. This is the bar
every earlier variant in this document failed — eight of ten seasons *and* an
interval clear of zero — and Elo clears both, on settings nobody has tuned.

Against chancedegol on the same seasons (`benchmark_chancedegol.py --model
elo`): **−0.0016 [−0.0034, +0.0001], Elo better in 8 of 10.** The
incumbent against the same forecaster: +0.0019, better in 2 of 10.

**Adopted.** Since `elo-line-previous-season` the adapter fits the line on the
previous season by default, so the model behind these numbers is the one that
runs; the 2019 fit stays available (`burn_in_season=2019`, `--model
elo-fixed-2019`) to reproduce the earlier exports. On 2026 so far the choice is
worth −0.0003 [−0.0016, +0.0009], nothing: the fitted line barely moves from
year to year (0.52–0.69 goals at a typical gap since 2018), and 2019 happens to
sit mid-range. The case for the switch is that the tested model should be the
running one, and that a fixed year is an assumption that ages.

Caveat: before 2019 the store has no Libertadores or Sudamericana, and before
2016 no Copa do Brasil, so 2016–2019 Elo is built from less data than the arm
measured on 2020–2025. It wins those seasons anyway (3 of 4 against the
incumbent).

## 3g. Elo's settings are already at or near their optimum

Every Elo setting was inherited, never fitted. `elo-sweeps` moved each one away
from the default, one at a time, on 2016–2025 (3,760 matches) with the harness
of 3f (`entrypoints/elo_backtest.py --sweep`, report
`docs/superpowers/elo-sweeps-report.md`). Three settings were added for it, each
bit-identical to today at its default: a K multiplier per competition, a
between-season shrink toward the next season's division seed, and the
total-goals window. The pass rule was fixed in the ticket before anything ran:
better than the default in at least 8 of 10 seasons *and* a pooled interval
clear of zero.

**No level passes. Seven are clearly worse.**

| setting | levels tried | verdict |
|---|---|---|
| K (update size) | 10, 15, **20**, 25, 30, 40 | larger is clearly worse (25: +0.0003, 30: +0.0006, 40: +0.0011, all CIs clear); 10 and 15 edge the default (−0.0002, 6 of 10) but not in 2021–2025 |
| home advantage | 50, 70, **85**, 100, 120 | flat to ±0.00002 |
| margin ladder | flat, mild, **1/1.75/2.5**, steep | flat; ignoring the margin costs +0.0003, interval crossing zero |
| seed gap between divisions | 50, **100**, 150 | flat |
| between-season shrink | **0**, 0.1, 0.2, 0.33 | clearly worse at every level (+0.0002 to +0.0006) |
| continental / Copa / Série B weight | 0.5, **1**, 1.5 | flat |
| total-goals window | 180, **365**, 730 days | 180 clearly worse (+0.0005); 730 edges the default (−0.0001, 6 of 10), no interval |

(Default in bold. "Clearly worse" means the pooled interval sits above zero.)

What it says, beyond "the defaults were fine":

- **Nothing replicates across the halves.** Almost every effect is larger in
  2016–2020 than in 2021–2025 and shrinks toward zero in the later half. Before
  2019 the ratings are built from less data and are more sensitive to how they
  are built; with every competition in the store, Elo is robust to its settings.
  The small edges of K 15 and a 730-day totals window are early-half effects.
- **Ratings should carry over the break untouched.** Shrinking them toward a
  division seed between seasons is worse at every strength tried. Together with
  3e's finding that Elo's lead over Dixon-Coles is concentrated in the first ten
  rounds, the off-season carry-over looks like part of *why* Elo works.
- **Home advantage does not matter here because the goal-difference line absorbs
  it.** It shifts every home match's rating gap by the same amount, and the line
  is refitted each season with an intercept, so a different constant lands in
  the intercept. It survives only inside the rating update, where it is
  second-order.
- **Competition weights do not matter either,** within 0.5–1.5. That bears on
  adding the state championships: their reserve-squad matches may not need
  down-weighting to be harmless, though whether they *help* is a separate test.

With 24 levels tested, nothing passing is what "no real improvement in the
grid" looks like. The Elo result of 3f is not an artefact of lucky defaults, and
the room left inside Elo's own settings is at most ~0.0002 RPS — an order of
magnitude below what it gained over the incumbent.

## 3h. Rest and travel add nothing to Elo; region nearly does

`rest-hours-probe` and `travel-distance-probe` (`entrypoints/match_context_probe.py`,
export `match_context_probe.json`), with the method and pass rule committed to
the board before either ran. Elo's per-match forecasts for Série A 2016–2025
(3,760 matches, every one with rest and travel) are adjusted by a one-parameter
tilt between home and away per feature, fitted on 2016–2020 and scored on
2021–2025. Pass: lower 2021–2025 RPS than Elo alone, paired 95% interval clear
of zero, better in at least 4 of 5 seasons.

| feature | 2021–2025 RPS vs Elo | 95% CI | better in | verdict |
|---|---:|---|---:|---|
| rest difference (days) | +0.00005 | [−0.00036, +0.00048] | 2/5 | flat |
| short-rest flags (< 72 h) | +0.00005 | [−0.00092, +0.00102] | 4/5 | flat |
| travel per 1,000 km | −0.00002 | [−0.00024, +0.00020] | 3/5 | flat |
| travel on top of region terms | +0.00002 | [−0.00012, +0.00017] | 2/5 | flat |
| region terms (home and away macro-region) | −0.00110 | [−0.00219, +0.00001] | 4/5 | **flat, by 0.00001** |

Travel has no trend at all: the home side beats Elo's forecast by −0.009 to
+0.021 across distance bands, highest for trips over 2,000 km but lowest for
1,000–2,000. Rest runs the wrong way where it moves - home sides with two or
more days *less* rest beat Elo by +0.022 - which is the strength confound: they
are mostly the continental clubs.

Region is the one near miss, and it has a shape: **Sudeste clubs do better than
Elo expects against every other region, home or away.** Centro-Oeste hosts
Sudeste: Elo gives the home side 36%, it wins 26%. Nordeste hosts Sudeste: 40%
against 35%. Sudeste hosts Nordeste: 55% against 59%. Elo knows the Sudeste
clubs are stronger; it compresses the gap. Northern clubs were absent from
Série A in 2016–2020, so the North term was never fitted.

**Not a random-grouping artefact.** Eight region terms could simply be fitting
clubs rather than regions, so the same test was rerun 400 times with clubs
shuffled between regions at random (each region keeping its number of clubs).
No random map did as well as the real one: random maps typically *hurt*
2021–2025 RPS (median +0.00042, because eight extra terms fitted on noise
generalise badly), and the best of the 400 reached −0.00106 against the real
map's −0.00110. So the geography carries information beyond "some clubs are
misrated". That is not double-counting strength: Elo does rate Sudeste clubs
higher, and the residuals say it does not rate them high enough *relative to
the other regions* when they meet. (Scratch script, not an entrypoint.)

It did not pass, and the hypothesis now in view - that Elo under-separates
regions, perhaps because richer clubs are persistently stronger than a
self-correcting rating lets them stay - was formed by looking at these same
seasons. Testing it again on them would not be a test. The honest routes are a
single pre-registered term (Sudeste against the rest) judged on seasons not
yet looked at - 2026 onward - or a stature variable that explains *why*
(budgets, seasons in Série A) rather than a map.

## 3i. Region and rotation, every season held out: both flat, both pointing somewhere

`region-loso-probe` and `rotation-probe` (`match_context_probe.py --loso`,
export `match_context_loso.json`), rules committed before running (`f5fb81c`).
Same tilt as 3h, but each season is scored by a fit on all the others.

| test | seasons | RPS vs Elo | 95% CI | better in | verdict |
|---|---|---:|---|---:|---|
| region, eight terms | 2016–2025 | −0.00050 | [−0.00138, +0.00038] | 6/10 | flat |
| region, Sudeste against the rest | 2016–2025 | −0.00055 | [−0.00116, +0.00004] | **9/10** | flat (interval) |
| rotation: next-match hours + continental flags | 2019–2025 | +0.00001 | [−0.00064, +0.00068] | 5/7 | flat |
| rotation diagnostic: continental flags only | 2019–2025 | −0.00010 | [−0.00070, +0.00050] | 5/7 | flat |
| rotation diagnostic: next-match hours only | 2019–2025 | −0.00014 | [−0.00063, +0.00035] | 4/7 | flat |

**Region.** Held out season by season, the eight-term version weakens to 6 of
10 - the 3h near miss owed something to how the seasons were split. The
single Sudeste term is the steadier signal: better in 9 of 10 seasons, and its
coefficient barely moves across the ten fits (0.091–0.117), worth about +3.7
points of home-win probability when a Sudeste club hosts one from another
region (and −3.7 the other way round). But the pooled gain is 0.0005 RPS and
its interval still reaches zero, and the form was suggested by the data.
Effects this small cannot be confirmed on Série A alone at any reasonable
horizon (the power table puts 0.0005 in the tens of thousands of matches), so
whether to carry it is a judgement, not a test result.

**Rotation.** The fitted effect points exactly where the hypothesis says:
a home side with a continental match within 96 hours wins about 7 points less
often than Elo expects, and an away side in the same position lets the home
side win about 6 points more often. It applies to few matches, though - 159
home and 180 away flags in 2,630 matches - so the pooled test is diluted and
wide, and it does not pass. A leak check also qualifies it: in about a quarter
of flagged matches the club played another match after the forecast was made,
so whether that continental fixture existed may not have been known yet. A
leak can only inflate an effect, and there is none to inflate at the pooled
level; a sharper test would score flagged matches only, with fixtures as
scheduled on the forecast date.

## 3j. A Sudeste term inside Elo passes; rotation is real-looking but too rare to prove

Both rules were committed before running (`77fa1fd`).

**`elo-sudeste-gap`.** A new Elo setting, `EloLambdaParams.sudeste_gap`: that
many Elo points added to the Sudeste side's rating in the forecast gap when a
Sudeste club meets a Série A club from another region (ratings untouched; off
by default, bit-identical). Scored on the ten-season harness like every
sweep level (`elo_backtest.py --sudeste`, report
`docs/superpowers/elo-sudeste-gap-report.md`):

| points | vs Elo | 95% CI | better in | 2016–20 / 2021–25 |
|---:|---:|---|---:|---|
| **25** | **−0.00048** | **[−0.00087, −0.00010]** | **9/10** | −0.00047 / −0.00049 |
| 50 | −0.00050 | [−0.00127, +0.00026] | 7/10 | −0.00051 / −0.00049 |
| 75 | −0.00007 | [−0.00122, +0.00107] | 6/10 | −0.00013 / −0.00001 |
| 100 | +0.00079 | [−0.00073, +0.00232] | 4/10 | +0.00067 / +0.00092 |

25 points passes the sweep rule - the first Elo setting to do so, where none
of 3g's 24 did - and its two halves agree to 0.00002. Bigger is worse, so the
useful size is around 25–50. Three qualifications: the form was suggested by
the data (3h), four levels were tried and the best is reported, and the gain
is small - about a seventh of what Elo gained over the incumbent. It is a
provisional pass: adopting it is a decision, and seasons not yet examined
(2026 onward) are the only clean confirmation, though at this size even they
will not settle it quickly.

**`rotation-flagged-probe`.** The rotation flags scored only on the matches
they touch, with a leak proxy (a flag counts only if the club's previous match
was already played when the forecast was made; 73 of 302 flags dropped):

- 229 matches, 2019–2025: −0.0037 RPS [−0.0114, +0.0038], better in 5 of 7 -
  flat.
- Home side with a continental match within 96 h: home win **−7.2 points**
  [−14.2, +0.5]. Away side with one: home win **+5.8 points** [−1.2, +12.8].

Both point where the rotation hypothesis says and both intervals only just
reach zero.

The leak proxy turned out to be unnecessary: continental fixtures are always
known at least a week ahead (the user's domain knowledge), so a club - and the
forecast - always knows about a continental match due after a league match.
With every flag kept (302 matches): −0.0009 RPS [−0.0062, +0.0045], better in
5 of 7, flat; home side flagged, home win −4.9 points [−11.8, +1.7]; away
side flagged, +4.8 [−0.9, +10.9]. Same direction, a little weaker - the 73
matches the proxy had dropped carried less of the effect - and still short of
the sample it needs. The obstacle is size: 229 matches, where an effect of this RPS
size needs roughly 1,500–2,500 (the power table). More continental seasons
in the store - Libertadores and Sudamericana before 2019 - would roughly
double the flagged matches; that, not a sharper test, is what could settle
it.

## 3k. Rotation over ten seasons: the same direction every way it is cut, still flat

`rotation-ten-seasons` (`match_context_probe.py --rotation-ten`), rule fixed
before running: flagged matches only, every flag kept, leave one season out,
pass = pooled interval clear of zero and better in 8 of 10. The calendar adds
Libertadores and Sudamericana 2014–2018 rebuilt from Wikipedia
(`build_wikipedia_continental`, validated on 2019 against the API: every
match at the same minute with the same score, no wrong club id), and every
continental round - the Elo store starts both cups at the group stage, which
before 2021 had hidden every Sudamericana match ahead of the last 16.

| calendar | flagged | vs Elo | 95% CI | better in | home side flagged | away side flagged |
|---|---:|---:|---|---:|---|---|
| Libertadores only before 2019 | 345 | −0.00153 | [−0.00666, +0.00371] | 6/10 | −3.4 pts [−9.6, +3.3] | +6.7 pts [+1.4, +12.4] |
| + Sudamericana, every round | 406 | −0.00111 | [−0.00528, +0.00315] | 6/10 | −3.6 pts [−9.5, +2.3] | +5.5 pts [−0.2, +11.0] |

"pts" is the fitted change in home-win probability: a flagged home side wins
less, and a flagged away side lets the home side win more - both where
rotation says. Descriptively, in points per match against Elo's expected
points, the club with a continental match within 96 hours is below Elo in all
four cells (home/away × Libertadores/Sudamericana: −0.03, −0.19, −0.18, −0.16,
standard errors 0.10–0.13), while the same clubs' other matches sit at +0.05.

**Verdict: flat.** The direction is consistent, the size is about a sixth of
a point per match, and it is worth about 0.001 RPS on the ~400 matches it
touches - under the noise the test has to clear. A shorter window does not
help: fixtures put almost every flag at 72–96 hours (weekend league, midweek
cup), so under 72 hours there are ~20 matches. The window stays at 96 hours,
as registered.

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
matches the estimator sees**. Sections 3b and 3c are the evidence: Dixon-Coles
gains 0.0044 RPS from Série B, Copa do Brasil and continental matches, and Elo
replayed over the same matches beats the incumbent with an interval clear of
zero - 6 of 6 seasons on 2020-2025, and 9 of 10 on 2016-2025 (3f). chancedegol's published method (twelve
months, eight competitions, fitted jointly) said the same thing from the outside;
their edge is reproducible from public data, and the "model or timing?" question
is now mostly answered - model, via data. Section 3d closes the loop: scored
against chancedegol directly, Elo turns their 1-3 lead into a 3-1 deficit, and
their edge is gone.

Open, in order of what they would settle:

1. ~~**Elo's parameters.**~~ Swept in 3g: flat. No level passes, seven are
   clearly worse, and the defaults stand. What remains inside Elo is at most
   ~0.0002 RPS.
2. **Which competitions carry the gain.** A Série-B-only ablation tells whether
   the effect is breadth or simply that promoted clubs stop being a hardcoded
   guess. Cheap now that both arms exist.
3. **Convergence on the all-competitions graph.** Clean on full seasons, not on
   the live one (drift 0.43 on 2026). Damped updates or a per-club minimum before a
   rating counts, in `dixon_coles.py`, before arm B is used for live forecasts.
   Elo has no such problem - replay is exact.
4. **Switching production.** The dashboard still runs the incumbent. With 3c and
   3d in hand the question is no longer whether but when; the answer should wait
   for 1, and a season of live forecasts logged beside the incumbent's.
5. **Timing.** Still unverified whether chancedegol publishes closer to kick-off
   than our horizon 0; capture their upcoming-round forecasts at a known
   timestamp. This matters more now than it did: with the model gap closed, an
   unmeasured timing edge is the largest remaining explanation for 3d's residual.
6. **Match context.** Blends of our own models are closed (3e); the next inputs
   have to be new information. Rest between matches is ticketed as a probe first
   (E10, `rest-hours-probe`), with a pre-registered gate before any build.

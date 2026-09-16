# Opponent-adjusted lambda

**Date:** 2026-09-10
**Status:** Draft

## Problem

`team_params_same_venue_average.sql` estimates a club's scoring rate from its own
last 19 matches at a venue, recency-weighted 4/3/1. Nothing in that estimate
conditions on **who** those goals were scored against. A club fed on weak
defences carries an inflated attack figure and the model reads it as strength.

The forward half of the problem is already handled: λ blends the home club's
attack with the *away club's* defence, fixture by fixture, so the difficulty of
matches still to be played is priced in match by match. Averaging the ratings of
opponents still to be faced would double-count it. The bias sits entirely in
**estimating a club's own strength from matches already played**, so the fix
belongs on the backward-looking window.

This also explains why the `uncertain` variant (C2) lost. It drew each simulated
season's λ from a Gamma centred on the point estimate — adding *variance* around
the mean without moving the mean. Symmetric noise cannot correct a systematic
bias; it only blunts sharpness, which a proper scoring rule punishes. C2 was
aimed at the wrong error.

Every constant swept so far — window, recency weights, attack/defence blend,
newcomer prior — lives inside the same marginal-average family, and none was
beaten by more than noise across ten seasons. That is evidence the *family* is at
its ceiling, not that football is unpredictable. Opponent adjustment is the one
direction those sweeps did not test.

## The size of the effect, measured before building anything

The obvious first objection is that Brasileirão is a balanced double round-robin:
over a full season every club plays every other exactly once at home and once
away, so the schedule balances out and the adjustment should be inert by the end.

**That argument is wrong, and the data says so.** Adjustment factors at full
strength on 2025, sampled across the season:

| date | mean \|adj−1\| | max | p90 |
|---|---:|---:|---:|
| 31 Mar (opening) | 4.9% | 18.9% | 10.2% |
| 13 Jul (mid) | 6.4% | 35.8% | 12.3% |
| 27 Sep | 4.7% | 13.9% | 10.2% |
| 7 Dec (final) | 4.7% | 16.2% | 10.6% |

A typical club's rate moves about 5% and the extremes move 15–36%, roughly
constantly from March to December.

The reason the balance argument fails: the window is **recency-weighted 4/3/1**.
The most recent match counts four times and the next four count three times each,
so the effective opponent mix is never balanced even once the raw set is. A club
coming off three matches against the bottom of the table carries an inflated rate
on the final day just as it does in March.

That reframes what this feature is. It is not only a schedule-imbalance
correction; it is a correction for **recency-weighted opponent difficulty** — the
"soft run inflates your rating" problem, which is exactly the failure mode a
recency-weighted average is most exposed to. The two effects cannot be separated
in this design, and there is no reason to want to.

Consequence for measurement: no stage-of-season dilution is expected, so the
sweep can be judged on whole seasons. The split is still worth reporting, but as
a diagnostic rather than as the headline.

## Design

### The adjustment

For a club's attack at a venue, weight the opponents it actually faced in the
window by the same recency weights, and take the mean of their defensive rate at
the opposite venue:

```
mean_opponent_defence = weighted mean over the window of
                        (opponent's goals_against_average at the opposite venue)

league_defence        = mean goals_against_average across all clubs
                        at that opposite venue

adjusted_goals_for = raw_goals_for
                     * (league_defence / mean_opponent_defence) ^ $schedule_weight
```

Symmetrically for defence, using opponents' attacking rates.

Faced weak defences (`mean_opponent_defence` above league average) and the ratio
falls below 1, deflating the attack estimate. Faced strong ones and it rises.

### Why the exponent, and why it is removable

`$schedule_weight` is the whole knob:

- **0** — the multiplier is `x^0 = 1.0` exactly, for every club, in IEEE
  arithmetic. `raw * 1.0 == raw` bit for bit. **This is the default**, so the
  feature ships inert and every existing forecast, pickle and export is
  unaffected.
- **1** — full adjustment.
- Anything between — partial.

Removing the feature later is deleting CTEs that were multiplying by one. That is
the "additive" property this design is built around: nothing existing is
rewritten, and the off state is not merely *close* to today's numbers but
identical to them.

### One round, not iterated

The opponents' rates used in the adjustment are their **raw** window averages,
not themselves adjusted. Adjusting them too would require iterating to a fixed
point.

That is deliberate. One round keeps the query a single pass with no recursive
CTE, and the second-order correction is small relative to the first. If
`$schedule_weight` shows a real gain, the honest next step is not to iterate this
approximation but to replace it with a jointly-fitted attack/defence model
(Dixon-Coles style, `λ = exp(attack_home + defence_away + home_advantage)`),
where schedule strength cancels by construction. This design is the cheap test of
whether that larger rewrite is worth attempting.

### Where it sits in the query

Between `teams_` (raw window averages) and `teams_coalesce`. Adjust the club's
own estimate first, then let the existing newcomer-prior blend shrink thin-window
clubs toward the league prior. The prior is already a league-average value and
needs no schedule adjustment of its own.

`new_fixtures` already carries `opponent_name`, so no dataset or upstream query
changes.

### Guards

`mean_opponent_defence` can be null or zero for a club with no window at that
venue. The ratio is written
`coalesce(power(league / nullif(mean_opponent, 0), $schedule_weight), 1.0)` so
those rows fall through unadjusted rather than erroring or producing infinities.
At the default weight of 0 this guard never changes an outcome.

## Validation

- **Equivalence, exact:** `Queries(season)` with no `schedule_weight` must
  produce a `team_params` frame numerically identical to today's, asserted with
  `np.array_equal` on the float columns — not `allclose`. The SQL text will
  differ (new CTEs exist), so unlike the lookback and recency-weight gates this
  one is on output, not on rendered text.
- **Deliberate break:** change the default to 1.0, confirm the gate fails,
  revert, confirm `git diff` is clean. A gate that does not bite is not a gate.
- **The knob bites, at a known magnitude:** at `schedule_weight=1` on 2025, mean
  `|adj-1|` must land near 5% and the maximum near 15-35% at every stage of the
  season, matching the table above. This is a stronger gate than "the numbers
  differ": it pins the implementation to a measurement taken before any code was
  written, and it is what licenses reading a null result as a ceiling.
- **Direction is correct:** a club whose window opponents have weaker-than-average
  defences must have its attack estimate revised *down*, not up. Assert this on a
  hand-checked club rather than trusting the sign of the exponent.
- The 168-test suite stays green.

## Measurement

`entrypoints/variant_sweep.py --param schedule_weight --values 0,0.25,0.5,0.75,1`
across 2016–2025, scored at horizon 0, paired bootstrap against the default,
reported per season and pooled.

**The eight-of-ten-seasons rule applies.** A variant that beats the default in
fewer than eight of ten seasons is noise, whatever the pooled mean says. That
test is what separated the real lookback finding (0 of 10) from the fake one
(6 of 10).

**Also report by stage of season**, as a diagnostic. Split each
season's matches into thirds by date and report the sweep separately for each.
The measured factors say no dilution is expected, so this is a check on that
expectation rather than the headline test.

RPS alongside Brier, since RPS is the metric the external benchmark uses.

## Stated in advance

1. Adjustment factors are materially different from 1.0 at every stage of the
   season — this is already measured above, not a prediction, and the
   implementation must reproduce those magnitudes or it is wrong.
2. Genuinely uncertain on the score. A 5% typical shift in λ is large enough to
   move a Brier or RPS number, so unlike the four constant sweeps I do not
   expect a null on prior grounds. I would put it near even, and if it does
   gain I would expect 0.002–0.005 RPS.
3. If it gains, the gain will be larger at `schedule_weight` well below 1. The
   one-round approximation over-corrects, because opponents' rates are
   themselves unadjusted and so carry the same bias in the opposite direction.

Prediction 3 is the useful one for design: a best value near 0.3–0.5 would be
evidence the mechanism is real but the approximation is crude, which is the
strongest argument for the jointly-fitted model. A best value at 1.0 would
suggest the approximation is fine as it stands.

## What each outcome means

- **Gain, peaking below 1.0** → the mechanism is real and this approximation is
  leaving some of it on the table. Justifies the jointly-fitted model.
- **Gain, peaking at 1.0** → take it, and stop; the cheap version captured it.
- **Null, with the knob proven to bite at 5%** → the strongest ceiling evidence
  yet. Rates that move 5% without moving the score means the error is not in the
  rates at all, and no refinement of this model family will help. That closes the
  whole line of enquiry, which is worth more than another 0.001.

Note that the ceiling reading only holds *because* the knob is known to move λ
materially. A null from a knob that did nothing would say nothing at all, which
is why the "knob bites" check below is not a formality.

## Scope

In scope: the `$schedule_weight` parameter, the SQL CTEs behind it, its sweep,
and the stage-of-season split.

Out of scope: iterating the adjustment to a fixed point; the jointly-fitted
Dixon-Coles model; `team_match_counts.sql` and the `uncertain` adapter, which
still need reconciling before any sweep runs against them; changing any default.
This measures. It does not retune.

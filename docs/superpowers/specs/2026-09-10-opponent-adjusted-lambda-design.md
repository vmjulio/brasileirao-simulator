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

## The size of the effect, before building anything

Brasileirão is a balanced double round-robin: over a full season every club plays
every other exactly once at home and once away. **A completed season has no
schedule imbalance at all**, and the adjustment is mathematically inert there.

Imbalance exists only in two places:

1. **Mid-season**, where a club has played some subset of opponents. This is
   real and can be large in the opening third.
2. **Across the season boundary**, where the 19-match window reaches into the
   previous season, whose opponent set differs by promotion and relegation.

This has a direct consequence for measurement: a metric averaged over a whole
season will dilute a real effect, because the back half of every season
contributes near-zero adjustment. **The headline test must therefore be split by
stage of season**, or a genuine early-season gain will be averaged into
invisibility. This is the single most likely way to get a false null here.

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
- **The knob bites:** at `schedule_weight=1` on real data, team parameters must
  differ from the default — and must differ *more* in the opening third of a
  season than in the closing third, which is the direct test of the balanced
  round-robin argument above. If that ordering does not hold, the implementation
  is wrong regardless of what the Brier score says.
- **Inert on a balanced sample:** computed over a full completed season with no
  cross-boundary window, adjustment factors must sit within floating-point noise
  of 1.0.
- The 168-test suite stays green.

## Measurement

`entrypoints/variant_sweep.py --param schedule_weight --values 0,0.25,0.5,0.75,1`
across 2016–2025, scored at horizon 0, paired bootstrap against the default,
reported per season and pooled.

**The eight-of-ten-seasons rule applies.** A variant that beats the default in
fewer than eight of ten seasons is noise, whatever the pooled mean says. That
test is what separated the real lookback finding (0 of 10) from the fake one
(6 of 10).

**Additionally, and non-optionally: score by stage of season.** Split each
season's matches into thirds by date and report the sweep separately for each.
The prediction below lives or dies on that split, and a single season-wide number
cannot test it.

RPS alongside Brier, since RPS is the metric the external benchmark uses.

## Stated in advance

1. Adjustment factors will be materially different from 1.0 in the opening third
   of a season and within noise of 1.0 in the closing third. If this fails, the
   implementation is wrong.
2. Pooled over whole seasons, `schedule_weight` will not beat the default in
   eight of ten seasons. The dilution argument predicts a null here.
3. On first-third matches only, some positive weight will beat 0 — but I expect
   under 0.005 RPS, and I would not be surprised by a flat result there too.

Predictions 2 and 3 disagreeing is the point: it is what distinguishes "the
adjustment does nothing" from "the adjustment does something the season-wide
metric cannot see".

## What each outcome means

- **Gain on early-season matches, null overall** → the mechanism is real and the
  marginal-average family is leaving signal in exactly the place theory says it
  should. Justifies the jointly-fitted model.
- **Null everywhere, with the knob proven to bite** → schedule strength is not
  where the remaining error is, and the ceiling evidence gets stronger. Closes a
  line of enquiry, which is worth knowing.
- **Gain everywhere including the back half** → suspicious. A balanced
  round-robin should not permit it. Investigate the implementation before
  believing it.

## Scope

In scope: the `$schedule_weight` parameter, the SQL CTEs behind it, its sweep,
and the stage-of-season split.

Out of scope: iterating the adjustment to a fixed point; the jointly-fitted
Dixon-Coles model; `team_match_counts.sql` and the `uncertain` adapter, which
still need reconciling before any sweep runs against them; changing any default.
This measures. It does not retune.

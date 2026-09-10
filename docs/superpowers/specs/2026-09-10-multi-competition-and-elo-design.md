# Multi-competition data and an Elo rating

**Date:** 2026-09-10
**Status:** Draft

## Problem

chancedegol beats us by ~0.0022 RPS, consistently, in 4 of 5 seasons. Their
published method (`chancedegol.com.br/introducao.htm`) says why: they rate
Brazilian clubs from **twelve months across eight competitions** - Série A, B, C,
D, Copa do Brasil, Copa do Nordeste, Copa Verde and the state championships -
plus twenty-four months of continental football, all fitted jointly with venue,
match age and opponent strength.

We use one competition.

Two facts make this the right thing to chase rather than a guess:

- **A blend of the two forecasts beats ours significantly** (-0.00307 RPS, CI
  [-0.00477, -0.00137]) and its optimum wants **40% of our forecast**, not 0%. We
  are not a noisier copy of them; we hold signal they lack. That is what you
  expect when the difference is which matches go in.
- **The joint fit alone buys nothing.** Dixon-Coles is mechanically their rating
  system and, on league-only data, it lost 3-10. So the estimator is not the
  differentiator. The data is.

They use no lineups, no injuries, no xG and no congestion model - purely
scorelines, venue, recency and opponent strength. The gap is reproducible from
public data.

## The central constraint

**Other competitions inform the estimate; only Brasileirão fixtures are
simulated.** These are two different roles for match data, and today they are
served by one relation:

| role | today | after |
|---|---|---|
| estimate club strength | `new_fixtures` (league 71 only) | all competitions |
| decide what to simulate | `new_fixtures` | league 71, current season - **unchanged** |
| standings, positions, relegation | `new_fixtures` | **unchanged** |

Keeping the second and third rows untouched is most of the design. `remaining_games`
already filters to the current season, and `tidy_fixtures.sql` already hardcodes
`league_id = 71`, so the prediction side needs no change at all.

## Design

### Data

New optional file per season, same API-Football schema as `fixtures.csv`:

```
src/files/datasets/{season}/other_competitions.csv
```

One file holding every non-Série-A match for clubs in that season's Série A:
Copa do Brasil (league 73), Libertadores (13), Sudamericana (11), Série B (72),
state championships, Copa do Nordeste, Copa Verde. `SeasonData` loads it lazily
and returns an **empty frame when absent**, so every season without it behaves
exactly as today.

### A separate estimation relation

New query `all_matches.sql`, producing the same per-team-per-match shape
`enriched_tidy_fixtures` does, but unioning the league fixtures with
`other_fixtures` and carrying three extra columns:

- `league_id` - so competitions can be filtered or weighted later
- `is_neutral` - cup finals and some continental ties are at neither club's ground
- `opponent_id` - already present, but now genuinely load-bearing, because
  opponents include clubs outside Série A

Registered as `all_matches`. `new_fixtures` keeps its current meaning and
contents.

### One query changes source

`team_params_same_venue_average.sql` reads from a template variable:

```sql
from $estimation_source
```

defaulting to `new_fixtures`. With no other-competition file, or with the
parameter left alone, **the rendered SQL is byte-identical to today's** - the
same gate the lookback, recency-weight and prior parameters already use.

Nothing else changes. `standings.sql`, `tidy_fixtures.sql`, `enriched_tidy_fixtures.sql`
and `remaining_games` are untouched.

### The problem this creates, and why Elo solves it

**The current model cannot use the broader data.** Its estimate is a raw
goals-per-game average with no opponent adjustment, so a 5-0 win over a small
state club would inflate a club's attack rating exactly as much as a 5-0 over
Palmeiras. Feeding cup matches into today's estimator would make it *worse*, not
better.

So broader data requires an estimator that prices opponents. Two exist:

- **Dixon-Coles**, already built (`domain/dixon_coles.py`). Refitting it over all
  competitions is the smallest change: same code, wider input.
- **Elo**, proposed below, which additionally handles clubs outside Série A
  naturally and needs no window at all.

Both should be measured. The plan should not assume which wins.

### Elo

New module `domain/elo.py`. Ratings for **every club encountered**, including
Série B sides and foreign opposition, updated chronologically:

```
expected_home = 1 / (1 + 10 ** (-(R_home + H * (1 - is_neutral) - R_away) / 400))
R_home += K * G(goal_difference) * (actual - expected_home)
```

with `actual` in {1, 0.5, 0} and `G` the usual goal-difference multiplier
(1 for a one-goal win, 1.5 for two, `(11 + d) / 8` beyond). Parameters `K`,
`H`, the starting rating and `G`'s shape are all defaults to be swept, not
tuned by hand.

Elo has no window: its exponential memory is set by `K`, which removes the
"19 matches per venue" problem entirely - a club playing thirty extra cup
matches simply gets thirty extra updates.

### Turning Elo into two lambdas

Elo gives a win probability, not goals. Their site solves this with a
decomposition worth copying, because it separates two things our current blend
conflates:

- a **difference** parameter - how much better one club is, which sets the
  expected goal *difference*
- a **sum** parameter - each club's contribution to the *total* goals in a match

Then:

```
expected_difference = f(elo_home + H - elo_away)      # fitted, monotone
expected_total      = sum_home + sum_away             # per-club, venue-aware

lambda_home = (expected_total + expected_difference) / 2
lambda_away = (expected_total - expected_difference) / 2
```

clamped positive. `f` is fitted once by regressing observed goal difference on
Elo difference over historical matches; the sum parameters are per-club averages
of total goals in their matches, opponent-adjusted.

This is a genuinely different model, so it gets a **new adapter** (`elo`)
registered beside `loop`, `batch`, `uncertain` and `dixon_coles`. Nothing
existing is modified except the registry.

### Competition scope filter

Their rule, worth copying verbatim: cup competitions count **only from the group
stage, or from the round of 16 where there is no group stage**. This drops the
early rounds against tiny clubs, which are the matches most likely to distort a
rating and least likely to inform it. Implemented as a filter on `league_round`
in `all_matches.sql`, and exposed as a parameter so its value can be tested
rather than assumed.

## Validation

- **Equivalence, exact:** with no `other_competitions.csv`, every team parameter,
  every lambda and every existing test must be unchanged. `np.array_equal`, not
  `allclose`.
- **Deliberate break** on that gate, as with every previous parameter.
- **Prediction set is unchanged:** the set of simulated fixtures must be
  identical with and without the extra data. A test asserting `remaining_games`
  returns the same fixture ids either way - this is the constraint the whole
  design exists to protect.
- **Opponents outside Série A are rated:** with cup data loaded, Elo must hold
  ratings for clubs that never appear in `new_fixtures`, and those ratings must
  order sensibly (a Série B club below the Série A median).
- **The extra data actually arrives:** assert match counts per club rise by a
  plausible margin, and report the distribution. Silent no-ops are the main risk
  when adding a data source.

## Measurement

Score at horizon 0 across every season with data, on the existing harness, with
RPS as the headline. **Four arms**, so the two variables are separated rather
than confounded:

| arm | estimator | data |
|---|---|---|
| baseline | current marginal average | league only |
| A | Dixon-Coles | league only *(already measured: 3-10, loses)* |
| B | Dixon-Coles | all competitions |
| C | Elo | all competitions |

B vs A isolates the value of the data. C vs B compares estimators on equal data.
The eight-of-ten-seasons rule applies to any claim of improvement, and the
chancedegol benchmark is the external check.

## Stated in advance

1. B beats A. The data is the differentiator, on the evidence above.
2. B or C closes **some** of the 0.0022 gap to chancedegol but not all of it,
   because their twelve-month multi-competition window is still broader than
   whatever subset we assemble first.
3. Elo and Dixon-Coles on the same data land within noise of each other. They are
   both joint opponent-adjusted fits; the parameterisation should matter less
   than the input.

If 1 fails - broader data does not help even with an estimator that can use it -
then the ceiling argument is much stronger than currently believed, and the
remaining gap to chancedegol is likely forecast timing rather than model.

## What is needed from the data side

Per season, one CSV in the existing schema covering Série A clubs' matches in:
Copa do Brasil (73), Libertadores (13), Sudamericana (11), Série B (72), state
championships, Copa do Nordeste, Copa Verde. Team **ids** must be the
API-Football ids already used in `fixtures.csv`, since that is the join key -
names differ across competitions and `import_seasons.py` already canonicalises by
id for exactly this reason.

Seasons 2016-2026 ideally; any subset is testable, with the caveat that fewer
seasons means the eight-of-ten rule cannot be applied and the result stays
provisional.

## Scope

In scope: the optional data file, `all_matches.sql`, the `$estimation_source`
parameter, `domain/elo.py`, an `elo` adapter, and the four-arm comparison.

Out of scope: xG or shot data; fixture-congestion and rotation modelling;
lineup or injury data; changing any default; promoting either new model over
`batch`. Also out of scope: `$schedule_weight`, which this supersedes for the
second time - a joint fit over broader data does its job properly.

# Multi-competition strength estimation and an Elo rating

**Date:** 2026-09-10
**Status:** Approved in discussion; ready for planning

## Problem

chancedegol beats us by ~0.0022 RPS, consistently, in 4 of 5 seasons. Their
published method (`chancedegol.com.br/introducao.htm`) says why: they rate
Brazilian clubs from **twelve months across eight competitions** - Série A, B, C,
D, Copa do Brasil, Copa do Nordeste, Copa Verde and the state championships -
plus twenty-four months of continental football, fitted jointly with venue,
match age and opponent strength.

We use one competition.

Two facts make this worth chasing rather than guessing:

- **A blend of the two forecasts beats ours significantly** (-0.00307 RPS, CI
  [-0.00477, -0.00137]) and its optimum wants **40% of our forecast**, not 0%. We
  are not a noisier copy of them; we hold signal they lack. That is what you
  expect when the difference is which matches go in.
- **The joint fit alone buys nothing.** Dixon-Coles is mechanically their rating
  system and, on league-only data, it lost 3-10. The estimator is not the
  differentiator. The data is.

They use no lineups, injuries, xG or congestion model - purely scorelines, venue,
recency and opponent strength. The gap is reproducible from public data.

## Decisions taken

1. **Other competitions never enter the SQL.** All cross-competition estimation
   happens in Python and produces one small table, `team_strength`, which the
   simulation consumes. Every existing query stays byte-identical.
2. **Elo is seeded by division**, not flat, and is **replayed from scratch** on
   every run rather than maintained incrementally.
3. **Five seasons of data across all competitions** are assumed: 2022-2026. 2022
   is burn-in and is not evaluated.

## The central constraint

**Other competitions inform the estimate; only Brasileirão fixtures are
simulated.** Two roles for match data, kept in two places:

| role | served by | change |
|---|---|---|
| estimate club strength | `MatchStore` -> `team_strength` (Python) | **new** |
| decide what to simulate | `remaining_games` via `tidy_fixtures.sql`, `league_id = 71` | none |
| standings, positions, relegation | `standings.sql` on `new_fixtures` | none |
| today's lambda | `team_params_same_venue_average.sql` | none - remains the default |

`tidy_fixtures.sql` already hardcodes `league_id = 71` and `remaining_games`
already filters to the current season. The prediction side is structurally
protected today and is not touched.

## Design

### `MatchStore` - every match of every competition, one chronological stream

New domain object. Elo is a cross-season quantity - a club's rating in March 2025
depends on its Libertadores run in 2024 - so it cannot live inside the
per-season `SeasonData`, which stays the Brasileirão-specific view.

```
src/files/datasets/competitions/{league_id}/{season}.csv     # API-Football schema
```

`MatchStore` loads everything under that directory, keeps only `FT` matches,
**dedupes on `fixture_id`**, and sorts by `fixture_date`. Team identity is the
API-Football **id**, never the name - names differ across competitions and
`import_seasons.py` already canonicalises by id for this reason.

### The inclusion rules are a committed config, not query logic

```python
# domain/competitions.py
COMPETITIONS = {
    71: Rule("Série A",        from_round=None,           weight=1.0, division=1),
    72: Rule("Série B",        from_round=None,           weight=1.0, division=2),
    73: Rule("Copa do Brasil", from_round="Round of 16",  weight=1.0, division=None),
    13: Rule("Libertadores",   from_round="Group Stage",  weight=1.0, division=None),
    11: Rule("Sudamericana",   from_round="Group Stage",  weight=1.0, division=None),
}
```

A match from any league id not in this dict is ignored even if the file is
present. `from_round` copies chancedegol's rule - cups count only from the group
stage, or the round of 16 where there is none - and drops the early ties against
tiny clubs that distort a rating most and inform it least. Every admitted match
is tagged with the rule that admitted it, so "why is this in the estimate?"
always has an answer. `weight` exists so per-competition weighting can be swept
later; it starts at 1.0 everywhere.

### Elo

`domain/elo.py`. Ratings for **every club encountered** - Série B sides, state
clubs that reach a cup's round of 16, foreign opposition - updated in
chronological order over the whole `MatchStore`:

```
expected_home = 1 / (1 + 10 ** (-(R_home + H * (1 - is_neutral) - R_away) / 400))
delta         = K * G(goal_difference) * (actual - expected_home)
R_home += delta;  R_away -= delta
```

`actual` in {1, 0.5, 0}; `G` the standard goal-difference multiplier (1 for one
goal, 1.5 for two, `(11 + d) / 8` beyond). `is_neutral` comes from the fixture's
venue not matching either club's home ground.

**Seeding by division.** A club's first appearance seeds it from the highest
division it is found in that season: Série A 1500, Série B 1400, Série C/D or
state-only 1300, foreign clubs by confederation tier (CONMEBOL 1450 default).
Division comes from which league ids the club appears in that season; a club
seen only as a cup opponent takes the lowest tier. All seed values are
parameters.

**No window.** Elo's memory is `K`. A club with thirty extra cup matches gets
thirty extra updates; the "19 per venue" imbalance does not exist here.

**Replay, not increment.** The full store is a few thousand matches; a
chronological replay is milliseconds. Replaying from scratch on every run is
idempotent by construction - no ledger, no "already applied" bookkeeping, and a
corrected score upstream just flows through. Incremental here means only that
new data is absorbed with no manual step, which replay gives for free.

**Burn-in.** From division seeds a club needs roughly 30 matches before its
rating is informative. 2022 is burn-in and is never scored.

Parameters to sweep, never hand-tuned: `K`, `H`, the seed values, `G`'s shape.

### From Elo to two lambdas

Elo gives a win probability, not goals. chancedegol's decomposition separates the
two things our 50/50 blend conflates - how much *better* one side is, and how
many goals a match between them tends to produce:

```
expected_difference = f(elo_home + H - elo_away)     # monotone, fitted once
expected_total      = total_home + total_away        # per club, opponent-adjusted

lambda_home = max(eps, (expected_total + expected_difference) / 2)
lambda_away = max(eps, (expected_total - expected_difference) / 2)
```

`f` is a regression of observed goal difference on Elo difference over the burn-in
season. The per-club `total` parameters are each club's average contribution to
total goals in its matches, adjusted for opponents the same way attack and
defence are in Dixon-Coles.

### `team_strength` - the one table the simulation consumes

```
team_strength(team_id, as_of_date, elo, total, matches_used, competitions_used)
```

Produced by `MatchStore` for a given as-of date, using only matches strictly
before it. Registered into DuckDB as its own relation. `build_baseline` takes it
as an optional input: present, lambdas come from it; absent, from today's
`team_params` exactly as now.

That is the whole seam. It sits in Python, where it is testable, rather than in
SQL as a template variable.

### Adapters

Two new ones, both registered beside `loop`, `batch`, `uncertain` and
`dixon_coles`, changing nothing else:

- `dixon_coles_all` - the existing Dixon-Coles fit, fed from `MatchStore`
  instead of league-only fixtures.
- `elo` - the Elo decomposition above.

### The pipeline

```
API-Football export for any competition
  -> src/files/datasets/competitions/{league_id}/{season}.csv
  -> MatchStore: FT only, dedupe on fixture_id, apply COMPETITIONS rules, sort
  -> Elo replay over everything
  -> team_strength as of each Brasileirão forecast date
  -> lambdas for Brasileirão fixtures only
```

Idempotency rests on two rules: dedupe on `fixture_id` at ingest, and replay
rather than patch. A new round of any competition is a file drop and a re-run.

## Validation

- **Equivalence, exact:** with no `competitions/` directory, every lambda and
  every existing test is unchanged. `np.array_equal`, not `allclose`.
  Deliberate-break it.
- **The prediction set is unchanged:** `remaining_games` returns the same
  fixture ids with and without the extra data. This is the constraint the design
  exists to protect and it gets its own test.
- **Nothing reaches the SQL:** the set of DuckDB relations the existing queries
  read from is identical with and without `MatchStore`. Assert it.
- **Rules bite:** a fixture from an unlisted league id, or a Copa do Brasil first
  round, must be absent from `matches_used`.
- **Idempotent:** ingesting the same file twice yields the same `MatchStore`
  and the same ratings. Ingesting a corrected score changes ratings from that
  date forward and nowhere before it.
- **Ratings order sensibly:** after replay, the Série A median rating sits above
  the Série B median, which sits above cup-only clubs.
- **The extra data actually arrives:** matches per Série A club per season must
  rise materially over league-only, and the distribution is reported. A silent
  no-op is the main risk when adding a data source.

## Measurement

Horizon 0, RPS headline, existing harness. **Four arms**, so data and estimator
are never confounded:

| arm | estimator | data |
|---|---|---|
| baseline | current marginal average | league only |
| A | Dixon-Coles | league only *(measured: 3-10, loses)* |
| B | Dixon-Coles | all competitions |
| C | Elo | all competitions |

B vs A isolates the value of the data. C vs B compares estimators on equal data.
chancedegol is the external check.

**Evaluated seasons: 2023, 2024, 2025 complete, 2026 partial.** Three full
seasons. The eight-of-ten rule cannot apply, so any improvement is **provisional**
and will be labelled so. What the sample does support cleanly is the within-season
contrast of B against A on identical matches, which is the comparison that
answers the original hypothesis. Each season records a `coverage` manifest -
which competitions, how many matches - so no comparison mixes coverage levels
silently.

## Stated in advance

1. B beats A on all three full seasons. The data is the differentiator.
2. B or C closes part of the 0.0022 gap to chancedegol, not all of it: their
   twelve-month, eight-competition window is broader than this first assembly.
3. C and B land within noise of each other. Both are joint opponent-adjusted
   fits; the parameterisation matters less than the input.

If 1 fails - broader data does not help even with an estimator built to use it -
the ceiling argument is far stronger than currently believed, and the remaining
gap to chancedegol is most likely forecast timing, not model.

## Scope

In scope: `MatchStore`, `competitions.py`, `elo.py`, `team_strength`, the two
adapters, the pipeline entrypoint, the four-arm comparison.

Out of scope: xG or shot data; congestion and rotation; lineups or injuries;
changing any default; promoting either new model over `batch`; `$schedule_weight`
(superseded a second time - a joint fit over broader data does its job properly).

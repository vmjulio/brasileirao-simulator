# Sweeping the lookback window

**Date:** 2026-09-08
**Status:** Draft

## Problem

The simulator beats a base-rate reference by **2.8%** on match-outcome Brier —
measured at horizon 0, on forecasts made a median of one day before kick-off,
with every prior result known. That is the model at its most informed.

Two readings, and nothing so far distinguishes them:

1. **Football is close to irreducibly random** at match level, and 2.8% is a fair
   share of what is available.
2. **The rate estimates leave signal on the table.** The constants that produce
   them — the 19-match window, the 4/3/1 recency weights, the 50/50
   attack/defence blend, the newcomer prior — were never fitted. They are
   inherited values that have never been compared against alternatives.

Sweeping one constant separates these. If skill moves with the window, there is
signal being left behind and the other constants are worth sweeping too. If it
does not move at all, that is a ceiling, and further tuning is wasted effort —
which is equally worth knowing.

This design sweeps the **lookback window** first: it is the single most likely to
matter, and one axis is enough to tell whether the exercise has legs.

## The confound that must be handled

`team_params_same_venue_average.sql` uses `19` for **two different jobs**:

```sql
teams_ as (... from base where rn <= 19 ...)          -- (a) window size

backfill as (select 'not_enough_games' ..., 19 as data_points ...)   -- (b) blend denominator

teams_w_avg as (
    (t.data_points * t.goals_for_average
     + (r.data_points - t.data_points) * r.goals_for_average) / r.data_points
)
```

(a) is how many matches inform a team's average. (b) is the denominator of the
shrinkage blend toward the newcomer prior.

Change only (a) — say to 12 — and a team that previously had 19 matches now has
12, so `(19 − 12)/19 = 37%` of its estimate becomes prior. The measurement would
then confound **window size** with **shrinkage strength**, and a drop in skill
could be caused by either.

**Both must move together.** A single `$lookback` substitutes into both places, so
a full-window team stays fully self-determined at any window size and only the
amount of history changes. This is the difference between a sweep that means
something and one that does not.

## Design

### Parameterising the query

`Queries` already substitutes `$season` / `$previous_season` via
`string.Template.safe_substitute`. Add `lookback`:

```python
Queries(season: int, lookback: int = FULL_WINDOW_MATCHES)
```

with `FULL_WINDOW_MATCHES = 19` as the existing shared constant. Both occurrences
of `19` in `team_params_same_venue_average.sql` become `$lookback`.

**The default must reproduce today's query byte-for-byte.** That is the gate: a
test renders `Queries(2025)` and asserts the SQL text equals the pre-change file
with `$season` substituted, so the whole batch-equivalence guarantee is preserved
by construction rather than by hope.

`team_match_counts.sql` also carries `rn <= 19`, but it feeds only `uncertain`'s
`n_eff`. This sweep runs `batch` alone, so it is left alone — and the sweep must
not be run with `uncertain` until that is reconciled.

### Sweeping

`entrypoints/lookback_sweep.py --season 2025 --windows 8,12,19,26 --iterations 5000`

For each window: generate the season's forecasts with `batch` into scratch, score
horizon-0 with the existing match-Brier harness, record mean Brier and skill
against the base-rate reference. Print a table, delete the scratch.

Measured cost: **0.66s per date at 5,000 iterations**, so ~1.2 minutes per window
for 2025's 110 dates. Four windows is under five minutes.

Common random numbers across windows — same seed per date for every window — so
the comparison between windows is paired and Monte Carlo noise largely cancels.

### Window values

`8, 12, 19, 26` spans half to a third-again of the current value. 26 exceeds a
single season's 19 home matches, so it reaches further into the previous season —
a real test of whether older data still carries signal.

## Validation

- **Default equivalence, exact:** `Queries(season)` with no lookback renders SQL
  identical to today's. If this fails, nothing else in the sweep can be trusted.
- **The parameter actually bites:** `Queries(2025, lookback=8)` must produce
  different team parameters than `lookback=19` on real data — a sweep whose knob
  does nothing would report a flat line and be indistinguishable from a genuine
  ceiling.
- **Both occurrences move:** a test asserting no bare `19` remains in the
  rendered SQL, so the blend denominator cannot silently stay fixed.
- The existing 134-test suite must stay green.

## What the result means

- **Skill varies materially with the window** → signal is being left behind; sweep
  the recency weights, the blend, and the prior next, then refit.
- **Skill is flat across windows** → 2.8% is close to the ceiling for this model
  family; tuning constants is not where the remaining value is, and the honest
  conclusion is that match outcomes are mostly not predictable from goal rates.

Either answer is publishable, and the second is more valuable than it sounds: it
would close a line of enquiry rather than leave it open.

**Stated in advance:** I expect a shallow optimum somewhere between 12 and 26,
with skill changing by less than one percentage point across the range. If the
range moves skill by more than two points, the constants matter far more than
assumed and the whole set should be refitted.

## Scope

In scope: parameterising the window, the sweep entrypoint, and a 2025 run.

Out of scope: the recency weights, the attack/defence blend, the newcomer prior
(each its own axis); sweeping across all eleven seasons; `uncertain`, which needs
`team_match_counts.sql` reconciled first; and any change to a default — this
measures, it does not retune.

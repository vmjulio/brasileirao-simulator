# Multi-competition strength and Elo — Kanban board

**Spec:** `docs/superpowers/specs/2026-09-10-multi-competition-and-elo-design.md`
**Source review:** the Elo notebooks (`~/py/notebooks/{elo_score,new-elo}.ipynb`) and
`~/Documents/GitHub/elo-brasileirao/main.py`, which is a verbatim copy of the first
notebook. Eight defects found there are acceptance criteria in E4, not hopes.

Tickets are named, not numbered: the name is the branch, the report file and
the reference in every other ticket. Every ticket has one gate. A ticket is done when its gate passes in Docker
(`docker-compose run --rm app python3 -m pytest /tests -q`, baseline **180 passed /
13 deselected**, neither may drop) and the diff is committed with explicit `git add`.

Sizes: **S** ≤ half a day, **M** one to two days, **L** three or more.

---

## Dependency graph

```
E1 Data ──► E2 Gates ──► E3 Dixon-Coles on all data ──► DECISION ──► E4 Elo ──► E5 Elo→λ ──► E6 Sweeps ──► E8 Report
                │                                                        ▲
                └──────────────► E7 Live pipeline (parallel, any time after match-store) ──┘
```

**Critical path:** shard-competitions → match-store → equivalence-gates → dixon-coles-all-adapter → data-vs-league-backtest → *decision* → elo-interface → elo-replay ∥ elo-division-seeds ∥ elo-snapshots → elo-lambda-interface → … → four-arm-backtest → elo-sweeps → findings-and-dashboard.

**Parallel lanes, once match-store is merged:**

| lane | what runs |
|---|---|
| A (critical) | equivalence-gates → dixon-coles-all-adapter → data-vs-league-backtest |
| B | E7 entirely (extract-leagues-param, retrieve-all-competitions, refresh-competitions) |
| C — *optional, at risk* | E4 may start before the decision. It is independent of E3 in code. It is dependent on it in *value*: if data-vs-league-backtest shows the data does not help, E4 is wasted. Start it early only with a second pair of hands and eyes open. |

Inside E4 and E5, tickets marked ∥ are independent of each other once the
interface ticket in that epic is merged, and can be taken by different people.

---

## E1 — Data foundation

### shard-competitions · Shard `all_fixtures.csv` by league — **S**
**Blocked by:** nothing. **Start day 0.**

Split `~/py/notebooks/all_fixtures.csv` into
`src/files/datasets/competitions/{league_id}/{season}.csv` for league ids 71, 72,
73, 13, 11, seasons **≤ 2025 only**. Same 41-column schema, no transformation.
2026 is never taken from this file: the repo's `datasets/2026/fixtures.csv` is the
source for 71, and the other four have no 2026 rows anywhere yet (see E7).

**Gate**
- Row counts match the profile: 72 → 4,940 · 73 → 1,215 · 13 → 1,085 · 11 → 950.
- `fixture_id` unique across every shard (expected: 0 duplicates, 0 ids in two leagues).
- Zero rows with `league_season = 2026`.
- Shards committed; the script that produced them committed as
  `entrypoints/shard_competitions.py` so it is reproducible.

---

## E2 — `MatchStore` and the safety gates

### match-store · `MatchStore` + `COMPETITIONS` rules — **M**
**Blocked by:** shard-competitions.

`domain/match_store.py` loads every shard under `datasets/competitions/`, and
`domain/competitions.py` holds the committed inclusion rules:

```python
COMPETITIONS = {
    71: Rule("Série A",        from_round=None,          division=1),
    72: Rule("Série B",        from_round=None,          division=2),
    73: Rule("Copa do Brasil", from_round=ROUND_OF_16,   division=None),
    13: Rule("Libertadores",   from_round=GROUP_STAGE,   division=None),
    11: Rule("Sudamericana",   from_round=GROUP_STAGE,   division=None),
}
```

Rules, all of them decided:
- admit statuses `FT`, `AET`, `PEN`; drop `CANC`, `PST`, `NS`, `TBD`, `AWD`
- **score every match from `score_fulltime_*`, never `goals_*`** — the 90-minute
  result, so extra time and shootouts never touch a rating
- dedupe on `fixture_id`; sort by `(fixture_date, fixture_id)` so replays are deterministic
- identity is `team_id`; names are display only
- `ROUND_OF_16` must match both `Round of 16` and `8th Finals` — Copa do Brasil
  labels the same stage both ways, 80 rows each
- `is_neutral` = venue id matches neither club's usual home venue
- a league id absent from `COMPETITIONS` is ignored even if its shard exists
- every admitted match is tagged with the rule that admitted it

**Gate**
- Loading twice yields an identical frame (idempotent).
- A Copa do Brasil `1st Round` fixture and a fixture from an unlisted league are absent.
- All 165 `PEN`/`AET` rows in 73/13/11 carry their 90-minute score; the 5 whose
  `goals_*` differs (e.g. Palmeiras–Flamengo AET, 1–1 at 90') are asserted individually.
- A per-season **coverage manifest** is written: competitions present, matches per
  competition, matches per Série A club. This is what stops a later comparison
  silently mixing a league-only year with a full one.

### equivalence-gates · Equivalence and prediction-set gates — **S**
**Blocked by:** match-store. **Lane A.**

Prove `MatchStore`'s existence changes nothing until something consumes it.

**Gate**
- `remaining_games` returns the identical set of fixture ids with and without
  the `competitions/` directory present.
- The set of DuckDB relations read by every existing `.sql` file is unchanged
  (assert by listing registrations, not by inspection).
- Every λ from `build_baseline` is `np.array_equal` — not `allclose` — to the
  pre-change value when no `team_strength` is supplied.
- Deliberate-break: register a bogus relation and confirm the second assertion
  fails; revert; `git diff` clean. Recorded in the ticket.

---

### cup-round-labels-2026 · Teach the stage scale the 2026 preliminary rounds — **S** · *done*
**Blocked by:** match-store, retrieve-all-competitions. **Found by the integration check.**

Not on the original board. `match-store` was built and tested on the ≤2025
shards; `retrieve-all-competitions` landed the 2026 shards on a branch where
`MatchStore` did not yet exist. Each was green alone; merged, `MatchStore()`
raised — `_stage_rank` refuses labels it has never seen, by design — on Copa do
Brasil's expanded early rounds (`1/256-finals`, `1/128-finals`, `Round of 128`,
`Round of 64`) and continental `Qualification Round 1–3`. All map to the
preliminary tier and are dropped; `Round of 32` is untouched; unseen labels
still raise.

**Gate (met):** `MatchStore()` constructs from the real root; every new label
ranks 0 and is not admitted; the ≤2025 anchors (165 PEN/AET rows, 15,869
admitted) hold when scoped to their seasons, with 2026 a positive addition.

---

## E3 — Dixon-Coles on all competitions (the decision gate)

### dixon-coles-all-adapter · `dixon_coles_all` adapter — **S**
**Blocked by:** equivalence-gates.

Feed the existing `domain/dixon_coles.fit` from `MatchStore` instead of the
league-only frame. Register `dixon_coles_all` in `SIMULATORS`. No change to
`dixon_coles.py` itself: same fit, wider input. Opponents outside Série A get
ratings because the fit already rates every club it sees.

**Gate**
- `simulator_for("dixon_coles_all", ...)` resolves; the existing three plus
  `dixon_coles` untouched.
- On a real 2025 date, at least one λ differs from `dixon_coles`'s.
- Ratings exist for clubs that never appear in `new_fixtures` (a Série B side, a
  Libertadores opponent), and the Série A median rating exceeds the Série B median.

### data-vs-league-backtest · Arm A vs Arm B backtest — **M**
**Blocked by:** dixon-coles-all-adapter.

Extend `entrypoints/dixon_coles_backtest.py` to produce arms A (league only) and
B (all competitions) on **identical matches** at horizon 0, seasons 2020–2025,
with 2019 as burn-in. Paired bootstrap per season and pooled; RPS headline.

**Gate**
- Arm A reproduces the earlier per-season numbers exactly on 2020–2025
  (e.g. 2025: 0.2051 / 2023: 0.2243). If not, the harness changed, not the model — stop.
- Both arms score the same match set; dropped matches are counted, never silently lost.
- Results committed to `src/files/exports/dixon_coles_all_vs_league.csv`; report in
  `.superpowers/sdd/`.

**Result (2026-09-10):** B better in **6 of 6** full seasons; pooled B − A = **−0.00439,
95% CI [−0.00640, −0.00244]**; B vs incumbent −0.00238, CI [−0.00515, +0.00047];
Série A rating drift ≤ 9e-4 on every full season. Arm A reproduced the earlier
numbers to 1e-9. 2026 (partial) re-run on the integration branch: B − A −0.0010 on
a wide interval, and drift 4.3e-1 — *not clean*, see known issues. Exports:
`src/files/exports/dixon_coles_all_vs_league{,_pooled}.csv`. **Threshold met;
decision awaiting the owner.**

**→ DECISION.** B beats A in ≥ 4 of 6 seasons with a pooled interval clear of zero:
proceed to E4. Otherwise the data is not the lever; open a *forecast-timing capture*
ticket instead and park E4–E6. Six seasons cannot support the eight-of-ten rule, so
the report labels whatever it finds **provisional**.

---

## E4 — Elo

### elo-interface · Interface ticket — **S** · *done*
**Blocked by:** match-store (code) · *decision* (value). Merged first; unblocks the ∥ tickets.

**Done (2026-09-10, `86113d3`):** `domain/elo.py` with `EloParams` (frozen,
`MappingProxyType` seeds), `EloHistory` (eleven documented columns), stubs
naming their owning ticket, `margin_multiplier` implemented; 30 shape tests.
File ownership for the ∥ tickets is stated at the top of the module: replay in
`elo.py`, seeds in `elo_seeds.py`, snapshots in `elo_snapshots.py`.
Local-run note: tests must run from `src/` (`PYTHONPATH=. pytest ../tests`);
from the repo root ~130 tests fail on relative data paths. Three
`test_display_names.py` tests also hard-code the Docker `/src/...` path and
fail outside the container (known issue, below).

Fix the signatures so the rest of the epic can be built in parallel:

```python
@dataclass
class EloParams:
    k: float = 20.0                 # scaled by G(margin)
    home_advantage: float = 85.0    # starting value from the notebook, to be swept
    seeds: dict = SEED_BY_DIVISION  # {1: 1500, 2: 1400, 3: 1300, "foreign": 1450}
    margin_ladder: tuple = (1.0, 1.75, 2.5)   # 20/35/50 as multipliers of k

def replay(store: MatchStore, params: EloParams) -> EloHistory
def ratings_as_of(history: EloHistory, date: str) -> dict[int, float]   # team_id -> elo
```

Stub bodies that raise; tests for the *shape* only.

### elo-replay ∥ Replay core — **M** · *done*
**Blocked by:** elo-interface.

**Done (2026-09-10, `0d1079e`, merged `d5db38f`):** all six gates as tests in
`tests/test_elo_replay.py`; 0.1 s over 16,405 store matches. Seeds arrive
through the module-level `seed_for` name so the ticket landed before seeds did.

Chronological replay over the whole store; standard expectation with
`home_advantage` applied to the home side for the expectation and removed after;
`is_neutral` → no advantage; update by `k × G(margin) × (actual − expected)`.

**Gate — the notebook defects, each as a failing-then-passing test**
1. **Margin symmetry:** a 4–0 away win moves ratings exactly as much as a 4–0 home
   win. (Notebook: away wins always got the one-goal K.)
2. **Identity by `team_id`:** two spellings of one club in the input produce one rating.
3. **90-minute scoring:** an AET 2–1 that was 1–1 at 90' updates as a draw.
4. **Deterministic ordering:** replaying a shuffled input gives identical output.
5. **Home advantage sign:** with equal ratings, expected home result > 0.5 by the
   documented amount, and `elo_diff` logged as `home + H − away`, not `+2H`.
6. **Idempotent:** replay twice → identical; replay with one corrected score →
   ratings differ from that date forward and nowhere before.

### elo-division-seeds ∥ Division seeding — **S** · *done*
**Blocked by:** elo-interface.

**Done (2026-09-10, `3f6d2d0`, merged `125aa28`):** `domain/elo_seeds.py`
with `seed_for` and `division_table`, tier table cached per store. 2025: 20
tier-1, 20 tier-2, 2 tier-3, 50 foreign. The median gate lives in
`tests/test_elo_integration.py` (Série A > Série B > cup-only at the close of
2022–2025, real store).

A club's seed on first appearance is set by the highest division it appears in
that season, derived from which league ids it plays in — *not* a hand-typed table.
Clubs seen only as cup opponents take the lowest tier; foreign clubs the foreign seed.

**Gate**
7. A club whose only appearance is a Copa do Brasil tie seeds at the bottom tier.
8. No club seeds at a value absent from `EloParams.seeds` (the notebook's silent 800 fallback).
- After burn-in, median(Série A) > median(Série B) > median(cup-only).

### elo-snapshots ∥ Snapshot table — **S** · *done*
**Blocked by:** elo-interface.

**Done (2026-09-10, `a82fb22`, merged `a3043ab`):** `domain/elo_snapshots.py`
with `ratings_as_of`, `team_strength(team_id, as_of_date, elo, matches_used,
competitions_used)` and `register_team_strength` (a one-line
`connection.register`, the primitive every other frame already uses). The
spec's `total` column is elo-total-goals's to add. Relation-set gate: static
grep of the SQL tree for `team_strength` (zero hits) plus a dynamic check that
registering adds exactly one relation.

`ratings_as_of` via the daily-snapshot query pattern from `new-elo.ipynb` cell 18
(the one piece of the notebook worth keeping as-is), producing
`team_strength(team_id, as_of_date, elo, matches_used, competitions_used)`.

**Gate:** as-of a date strictly before a match, that match has not influenced the
rating (no leakage); the table is registered in DuckDB under its own name and no
existing query's relation set changes (re-run equivalence-gates's assertion).

---

## E5 — Elo → lambda and arm C

### elo-lambda-interface · Interface ticket — **S** · *done*
**Blocked by:** elo-replay, elo-division-seeds, elo-snapshots.

**Done (2026-09-10, `0b32440`, merged `28dd806`):** `domain/elo_lambda.py` -
`EloLambdaParams(home_advantage=85, eps=0.05, burn_in_season=2019)`,
`DifferenceMap` (linear, `slope > 0` enforced by the fitter), `lambdas()`
implemented, stubs for the two ∥ tickets. Re-exports resolve through a module
`__getattr__` for `fit_difference_map` (that module imports the dataclasses
from here; a plain import in either direction broke depending on import order).

```python
def fit_difference_map(history, burn_in_season) -> Callable[[float], float]   # elo diff → expected goal diff
def total_goals_params(store, as_of) -> dict[int, float]                     # team_id → contribution to total
def lambdas(elo_home, elo_away, total_home, total_away, params) -> tuple[float, float]
```

### elo-difference-map ∥ Difference map — **S** · *done*
Regress observed 90-minute goal difference on `elo_home + H − elo_away` over the
burn-in season only. Monotone; fitted once; coefficients stored with the season
they came from. **Gate:** fitted on 2019, evaluated on 2020 it is monotone and
its slope is positive; no cubic, no unbounded output.

**Done (2026-09-10, `95e0059`, merged `1ec2761`):** OLS on 944 matches of 2019
(every competition): slope 0.00403 goals per Elo point, intercept 0.064. On
2020 Série A the predicted/observed goal-difference correlation is 0.26.

### elo-total-goals ∥ Total-goals parameters — **S** · *done*
Per-club contribution to total goals, opponent-adjusted the same way Dixon-Coles
adjusts attack/defence. **Gate:** league mean of `total_home + total_away`
matches the observed mean total goals within 2%.

**Done (2026-09-10, `d6d30dd`, merged `d1ce1fb`):** 365-day window,
Gauss-Seidel fixed point (Jacobi sweeps did not converge in 200 iterations
on the real store), additive re-centring. Gate on Série A, fit as of the
season's last date + 1: 2023 2.490 obs / 2.454 fit (−1.4%), 2024 2.445 / 2.428
(−0.7%), 2025 2.524 / 2.475 (−1.9%). `team_strength_with_totals` adds the
spec's `total` column.

### elo-adapter · `build_baseline` optional input + `elo` adapter — **M** · *done*
**Blocked by:** elo-difference-map, elo-total-goals.

**Done (2026-09-10, `a6eedca`, merged `388289c`):** `TeamStrength` +
`team_strength_as_of` in `elo_lambda.py`; `build_baseline(...,
team_strength=None)` with the absent path untouched (equivalence gate 3 still
bit-identical) and `SeasonBaseline.lambda_fallbacks`; `adapters/elo_adapter.py`
registered as `elo`. On 2025-08-01: incumbent mean λ 1.339 / 0.983, Elo 1.386 /
0.988; fixture set identical; zero fallbacks.

`build_baseline` accepts `team_strength`; present → λ from the decomposition
`(total ± diff)/2`, clamped positive; absent → today's path, untouched. Register
`elo` in `SIMULATORS`.

**Gate:** with `team_strength` absent every λ is bit-identical to before
(re-run equivalence-gates's third assertion); with it present at least one λ differs; the
simulated fixture set is unchanged (re-run equivalence-gates's first assertion).

### four-arm-backtest · Arm C backtest — **S** · *done*
**Blocked by:** elo-adapter.

Add arm C to the backtest from data-vs-league-backtest, same matches, same seasons. **Gate:** C vs B
and C vs baseline reported per season and pooled; committed to
`src/files/exports/four_arms.csv`.

**Result (2026-09-10, `ed90aee`, merged `13e8391`), 2020–2025, 2,254 identical
matches, horizon 0, Elo defaults untuned (K 20, H 85, seeds 1500/1400/1300/1450,
ladder 1/1.75/2.5, difference map on 2019, 365-day totals, independent Poisson):**

| arm | pooled RPS | vs incumbent | 95% CI | seasons better |
|---|---:|---:|---|---:|
| incumbent | 0.2135 | – | – | – |
| A Dixon-Coles, Série A | 0.2155 | +0.0020 | [−0.0012, +0.0053] | – |
| B Dixon-Coles, all | 0.2111 | −0.0024 | [−0.0052, +0.0005] | 5/6 |
| **C Elo, all** | **0.2097** | **−0.0038** | **[−0.0064, −0.0011]** | **6/6** |

C − B −0.0014, CI [−0.0030, +0.0002], C better 5/6 (2021 a dead heat). C − A
−0.0058, CI clear. 2026 partial: C 0.2047 vs incumbent 0.2098. Zero fallbacks,
zero drops; A/B/incumbent reproduced `dixon_coles_all_vs_league.csv` exactly.
**First model to beat the incumbent with an interval clear of zero.** Six
seasons: provisional. Report at `docs/superpowers/four-arm-backtest-report.md`.
Stated-in-advance predictions: (1) B beats A - held; (2) closes part of the
chancedegol gap - **held, and then some** (see below); (3) C and B within noise
of each other - held (interval touches zero).

### chancedegol-vs-arm-c — done

Scored arm C against chancedegol's own published probabilities on their own
matches (`benchmark_chancedegol.py --model elo-fixed-2019`). On the 1,498 matches of the
four full seasons, incumbent +0.0023 behind them (better in 1/4); **arm C
−0.0020 ahead (better in 3/4), CI [−0.0046, +0.0005]**. 2026 partial: −0.0033.
Swing ~0.0044, matching the 0.0038 C gained on the incumbent internally. The
interval touches zero, so the claim is the deficit erased, not a lead. Default
path gated against committed `benchmark.json` (1.7e-18). FINDINGS 3d.

---

## E6 — Sweeps

### elo-ten-seasons · Score Elo on 2016–2025 — **S** · *done*
**Blocked by:** four-arm-backtest.

**Result (`e8f65f5`):** gate held (fixed-2019 reproduces four-arm to 1e-9);
previous-season minus fixed-2019 on 2020–2025 −0.00027, nothing. **Elo −
incumbent, 2016–2025, 3,760 matches: −0.00358 [−0.00542, −0.00180], better in 9
of 10** — first model to pass the eight-of-ten rule. Halves −0.00306 / −0.00411,
both clear. Against chancedegol −0.0016 [−0.0034, +0.0001], 8 of 10. Report
`docs/superpowers/elo-ten-seasons-report.md`, FINDINGS 3f.

Elo is scored only from 2020 because its goal-difference line is fitted once, on
2019; scoring 2019 or earlier with it would use the future. Six seasons cannot
meet the eight-of-ten rule, and every Elo result so far carries that caveat.

**Do:** fit the line on the season *before* each scored season (`burn_in_season =
season − 1`, already an `EloLambdaParams` field, so no domain change). Build
`entrypoints/elo_backtest.py`: per-match horizon-0 forecasts for Elo under any
`(EloParams, EloLambdaParams)` and for the incumbent, scored on identical
matches, per season and pooled (flat paired bootstrap), with find (2016–2020) /
confirm (2021–2025) halves. The same harness serves `elo-sweeps`.

**Gates:**
- The harness with the fixed-2019 line reproduces the committed four-arm Elo
  column (`rps_elo`) on 2020–2025 to 1e-9, and the incumbent column exactly.
- Report rolling vs fixed on 2020–2025, paired: the scheme change's own effect,
  stated before any ten-season claim.
- Report Elo (rolling) vs incumbent on 2016–2025, per season and pooled, and vs
  chancedegol on the same seasons.
- Caveat in the report: before 2019 the store has no continental matches and
  before 2016 no Copa do Brasil, so pre-2019 Elo is a narrower arm than the one
  measured on 2020–2025.

**No default changes** — the adapter keeps the fixed 2019 fit until a separate
decision.

### elo-sweeps · Elo parameter sweeps — **M** · *done*
**Blocked by:** elo-ten-seasons.

**Result:** flat. **No level passes; seven are clearly worse** (K 25/30/40,
season regression 0.1/0.2/0.33, totals window 180). K 10/15 and a 730-day
totals window edge the default by ≤0.0002 in 6 of 10, early half only. Home
advantage, seeds, margin ladder and every competition weight are flat. Effects
shrink from 2016–2020 to 2021–2025 across the board. Gate held (default
reproduces `elo_ten_seasons.csv`). Defaults stand. Report
`docs/superpowers/elo-sweeps-report.md`, FINDINGS 3g.

Every Elo setting is inherited, not fitted. Sweep them one at a time against the
defaults, ten seasons, on the `elo-ten-seasons` harness with the rolling line.

**Three new settings first**, each defaulting to today's behaviour (bit-identical
replay at the default, tested):
- `EloParams.competition_weight` — a K multiplier per `league_id` (default 1.0
  everywhere). A Sudamericana group match currently moves ratings exactly as
  much as a Série A match.
- `EloParams.season_regression` — at a club's first match of a new season, move
  its rating this fraction of the way toward its division seed for that season
  (default 0). Ratings currently carry across the break untouched.
- `EloLambdaParams.totals_window_days` — the total-goals fit window (default 365).

**Grid** (default in bold):

| setting | levels |
|---|---|
| K | 10, 15, **20**, 25, 30, 40 |
| home advantage (both params, kept equal) | 50, 70, **85**, 100, 120 |
| margin ladder | flat 1/1/1, mild 1/1.5/2, **1/1.75/2.5**, steep 1/2/3 |
| seed gap (B = 1500 − g, cup-only = 1500 − 2g, foreign = 1500 − g/2) | 50, **100**, 150 |
| season regression | **0**, 0.1, 0.2, 0.33 |
| continental weight (11, 13) | 0.5, **1**, 1.5 |
| Copa do Brasil weight (73) | 0.5, **1**, 1.5 |
| Série B weight (72) | 0.5, **1** |
| totals window, days | 180, **365**, 730 |

`from_round` (dropped from the earlier draft of this ticket) is a `MatchStore`
admission rule: it changes the data, not the rating, and belongs with adding
competitions.

**Gate — written before running.** A level *beats the default* only if it is
better in at least 8 of 10 seasons **and** its pooled interval excludes zero.
Report find/confirm halves for every level. Flat results are reported as flat.
**No default is changed by this ticket** — retuning is a separate decision with
its own ticket.

### elo-line-previous-season · Adapter fits the line on the previous season — **S** · *done*
**Blocked by:** elo-ten-seasons.

`EloLambdaParams.burn_in_season` defaults to `None`, meaning the season before
the one forecast; `EloAdapter` resolves it, and `fit_difference_map` refuses an
unresolved `None` rather than guess. A pinned int still pins (2019 reproduces
every earlier export; the four-arm backtest pins it explicitly).
`benchmark_chancedegol --model elo` is now the default; `--model elo-fixed-2019`
replaces `--model elo-previous-season`.

Why: the 9-of-10 evidence was measured with this line, so the running model
should be the tested one; a fixed year is an assumption that ages. Accuracy is
neutral (2020–2025 −0.00027; 2026 so far −0.0003 [−0.0016, +0.0009]).

**Gate:** the committed four-arm, `elo_ten_seasons.*`, `benchmark_elo.json`
(now `--model elo-fixed-2019`) and `benchmark_elo_ten.json` (now `--model elo`)
all reproduce unchanged.

### elo-line-pooled-seasons · Fit the line on the last three seasons — **S**
**Blocked by:** elo-line-previous-season.

One season is 900–1,000 matches and the fitted slope swings about ±15% year to
year (0.0027–0.0043 since 2016). Pooling the three seasons before the forecast
season should roughly halve that noise and keep the line self-updating and
leak-free.

**Do:** let `fit_difference_map` take a span of seasons (default one, so today's
fit is unchanged); add a `last_three_seasons` policy beside `previous_season` in
`elo_backtest`; score it against the previous-season default on 2016–2025 with
the sweep harness (2016 pools 2013–2015: Série A and B only).

**Gate:** the same rule as elo-sweeps, fixed before running — better in at least
8 of 10 seasons and a pooled interval clear of zero — or reported as flat. **No
default changes in this ticket.**

---

## E10 — Match context features

Every model so far sees only past scorelines. This epic adds facts about the
match itself, on top of the Elo rating and the existing inputs, starting with
rest. Probe first, build only if the probe finds something: a feature that
re-measures strength Elo already has adds a pipeline and no accuracy.

### rest-hours-probe · Does rest explain what Elo gets wrong? — **S**
**Blocked by:** nothing (needs only the four-arm per-match forecasts and `MatchStore`).

**The feature.** For each club in each match: hours between this kick-off and
that club's previous kick-off in *any* competition in `MatchStore`, from the UTC
`fixture_date`. Per match: `rest_home`, `rest_away`, `rest_diff = rest_home −
rest_away`, `rest_min`. The previous kick-off is known before this one, so the
feature carries no result leakage.

**Why test against Elo's residuals, not raw win rates.** Rest is confounded with
strength: the clubs with the least rest are mostly the strong ones playing
continental football. A raw "short rest loses more" table would partly re-measure
that. The question is whether rest explains what Elo *misses*: `outcome −
p_elo` and per-match RPS.

**First read (2026-09-10, scratch, 2,216 of 2,254 Série A matches 2020–2025
matched):** correlation between `rest_diff` and Elo's home-win residual is
**0.025**. By rest-gap bin the home side's win rate tracks Elo's forecast within
about three points (residuals −0.014 to +0.033), not monotone. Elo's own
`p_home` falls from 0.535 to 0.399 as the home side's relative rest grows, which
is the confound showing. Weak, not zero, and one crude cut: this ticket does it
properly.

**Do:**
- Build `rest_hours(store) -> DataFrame` keyed by `(fixture_id, team_id)` in
  `domain/`, one pass over the store. Cap at 336 h (14 days): an off-season or
  international-break gap is not "more rest" in any sense a model can use, and
  uncapped values let the first match of each season dominate any fit.
- Score residuals by bins of `rest_diff` and `rest_min`, plus a short-rest flag
  (< 72 h) for each side. Split-half: find on 2020–2022, confirm on 2023–2025.
- Report coverage honestly (below).

**Coverage caveat that biases the feature.** The store has no state
championships (January–April), no Copa do Nordeste / Copa Verde, no Recopa or
Supercopa, no friendlies, and no Club World Cup (June–July 2025 for Flamengo,
Palmeiras, Fluminense and Botafogo). Continental competitions start in 2019 and
Copa do Brasil in 2016. Measured rest is therefore an **upper bound**, and the
bias is worst early in the season and for the clubs in the most competitions.
Report `rest_diff` both over all rounds and over rounds 6+ only, where the state
championships are over.

**Gate — pre-registered, decides whether `rest-hours-elo` is built:** proceed if
either (a) a rest bin or the short-rest flag shows an Elo residual whose sign
holds in both halves and whose 2023–2025 interval excludes zero, or (b) adding
`rest_diff` to a logistic model of `outcome` on Elo's probabilities improves
out-of-sample log loss on 2023–2025 when fitted on 2020–2022. Otherwise record
the flat result in FINDINGS and close the epic.

### rest-hours-elo · Rest as an Elo gap adjustment — **M**
**Blocked by:** rest-hours-probe passing its gate.

Add rest to Elo's forecast, not its rating: the effective gap becomes `elo_home +
H − elo_away + β · f(rest)`, where `f` is whichever form the probe supported
(clipped `rest_diff`, or the short-rest flags). Ratings themselves never see
rest, so the replay is untouched. Fit `β` on the 2019 burn-in, as the
difference map is, so every scored season stays out of sample. Optional input on
`EloAdapter`, off by default.

**Horizon-0 detail.** At forecast time (median one day before kick-off) a club's
previous match can still be in the future. Use the *scheduled* kick-off as known
on the forecast date; the backtest uses fixture dates as played, which leaks the
rare postponement - measure how many matches that touches and report it.

**Gate:** paired backtest against Elo defaults, 2020–2025, horizon 0, identical
matches; per-season and pooled RPS with bootstrap CI; also scored against
chancedegol. Six seasons cannot meet the eight-of-ten rule, so the result is
provisional whichever way it goes. **No default changes in this ticket.**

---

## E7 — Live pipeline (parallel lane B)

### extract-leagues-param · Parameterise `league_id` in lean-pype — **S**
**Blocked by:** nothing. **Start day 0.**

`extract_all_pipeline.py` hardcodes `league_id=71`; `FixturesExtractor` already
accepts it. Add a `LEAGUES` env var beside the existing `SEASONS`, default `71`, and
pass it through `docker-compose.yml`'s `extraction` service like `SEASONS` is.
**Gate:** `LEAGUES=71 SEASONS=2026` reproduces today's output byte-for-byte.

### retrieve-all-competitions · Pull 2026 for 72, 73, 13, 11 — **S**
**Blocked by:** extract-leagues-param.

Land them as `datasets/competitions/{league_id}/2026.csv`. **Gate:** `fixture_id`
unique against the shard-competitions shards; `MatchStore` loads them with no new rule needed.

### refresh-competitions · `refresh_competitions` entrypoint — **S**
**Blocked by:** retrieve-all-competitions, match-store.

One command: pull the current season for every id in `COMPETITIONS`, drop the
files, reload the store. **Gate:** running it twice in a row is a no-op on the
second run (dedupe on `fixture_id` proves out end to end).

**Addendum (2026-09-10, `b54aee7`) — Série A was missing from the store.**
`LIVE_LEAGUES` excludes 71 by design (its season comes from
`datasets/{season}/fixtures.csv`), but nothing wrote `competitions/71/2026.csv`,
so `MatchStore` - and therefore the Elo replay and arm B - had no 2026 Série A
matches. Step 2b now mirrors the season file into the 71 shard (same `shard()`
call, verified identical to the committed 2024/2025 shards in every scored
column). Coverage 2026 is now `{72: 267, 71: 249, 11: 131, 13: 114, 73: 24}`.
Re-scoring 2026 with the league present: B − A −0.0045, CI [−0.0108, +0.0010];
B − incumbent −0.0041, CI [−0.0123, +0.0039]; drift unchanged at 0.43 (attack)
/ 0.76 (defence) - still partial, still not clean.

---

## E8 — Report

### findings-and-dashboard · `FINDINGS.md` + dashboard — **S**
**Blocked by:** four-arm-backtest (and elo-sweeps if run).

Four-arm table, the decision outcome, coverage manifests, and the chancedegol gap
before/after. **Gate:** every number in the doc names the export it came from.

### explorer-model-dropdown · Model picker on the forecast explorer — **M** · *done*
**Blocked by:** elo-line-previous-season.

The explorer shows one model's simulations. It now carries every explorer
model's archive and a dropdown in the header switches every chart - race,
heatmap, points-to-survive, calibration, season table, the chancedegol table
- and the model-specific prose. Opens on Elo; `?model=` keeps the choice in a
shared link. Registry: `config/explorer_models.py` (incumbent keeps
`files/pkl`; every other model gets `files/pkl_models/<key>`).
`backfill`/`topup_iterations`/`export_forecast_dataset` take `--model`.

Also fixed on the way: Elo rebuilt its team strengths for every 100-iteration
batch (200 per date at 20k); cached per cutoff, a date costs ~0.5 s per 500
iterations instead of ~20 s per 20,000. The chancedegol notes were stale
("the two models are indistinguishable", written for five seasons); they are
now a pooled sentence from the data plus each model's own reading. Elo's skill
tile (3.40%) is computed exactly as the incumbent's (the harness reproduces
the incumbent's 2.2057% to 16 digits).

**Backfilled (2026-09-11, on the user's go):** Elo at 1,000 iterations on all
1,139 dates, 2016-2026 (`topup_iterations --model elo --target 1000`, 13 min),
exported to `forecast_dataset.elo.json`, published to the explorer's existing
link. **Open:** top up to 20,000 (`--target 20000`, ~2 h) - only on the user's
go.

**Gate:** the page's md5 re-pinned deliberately (incumbent-only build);
registry and multi-model loader tested; both `?model=` values render every
section (checked on a throwaway build).

---

## E9 — Portuguese dashboard (lane B, independent of E1–E8)

The forecast explorer is going to colleagues, and they read Portuguese. This
epic touches only the report build (`scratchpad/forecasts_template.html` and
`inject.py`, to be moved into the repo under `entrypoints/report/`), never the
model. It can start on day 0 and needs nothing from the other epics; E8's new
strings simply flow through the same table when they land.

### report-strings-table · Strings table and `--lang` build — **M**
**Blocked by:** nothing. **Start day 0.**

Every user-facing string in the template — headings, prose, axis titles, legend
words, tooltips, table headers, the archive and calibration explanations, month
abbreviations, the "season in progress" notes — moves out of the markup into one
`strings.{en,pt}.json`, keyed by id. `inject.py` grows `--lang` and writes
`forecasts.{lang}.html`. English output must be byte-identical to today's build.

**Gate**
- `--lang en` reproduces the current `forecasts.html` byte-for-byte.
- A test greps the built `pt` page against a list of English tokens that must not
  survive (`Title`, `Relegation`, `season`, `matches`, `points`, `chance`, month
  abbreviations) and fails on any hit outside `<script>` data.
- No string literal in the template outside the table (assert by scanning the
  template for quoted runs of ≥3 words).

### club-display-names · Club display names by `team_id` — **S**
**Blocked by:** nothing. ∥ with report-strings-table.

The dashboard shows API-Football's ASCII names — `Sao Paulo`, `Vasco DA Gama`,
`Gremio`, `Atletico-MG`, `Chapecoense-sc`. To a Brazilian reader those are wrong,
not merely unaccented. Add a display-name map keyed by **`team_id`** (never name),
used for rendering only; every join and every export keeps the canonical name.
Reuse the ids in `~/py/bolaondroid/dist/logos/team_logos.json` as the starting
key set.

**Gate:** every club in the dataset has a display name; `Vasco DA Gama` renders as
`Vasco da Gama`, `Gremio` as `Grêmio`; the exported CSVs are unchanged.

### translate-pt-br · pt-BR translation and native review — **M**
**Blocked by:** report-strings-table.

Translate `strings.pt.json`. Numbers follow pt-BR conventions in display only
(`21,5%`, `20.000 iterações`, `3º`); the underlying data is untouched. The
retracted-finding callout and the "factual range, not a probability" wording
are the passages most likely to lose their precision in translation — flag them
for line-by-line review. **The gate is the owner's sign-off, not a test:** a
native reader confirms the statistical caveats still say what they say in English.

### publish-pt-artifact · Publish the Portuguese artifact — **S**
**Blocked by:** club-display-names, translate-pt-br.

Publish `forecasts.pt.html` as its **own** artifact (a separate URL, so the
English page and its watch are undisturbed), with a Portuguese title and
description and the same favicon. **Gate:** the page renders in both themes, the
crests and hover tooltips work, and the artifact is left **private** — sharing is
the owner's action from the page menu, not part of the ticket.

## Ticket count and shape

| epic | tickets | size | can start |
|---|---|---|---|
| E1 Data | 1 | S | day 0 |
| E2 Gates | 2 | M, S | after shard-competitions |
| E3 Dixon-Coles all | 2 | S, M | after equivalence-gates |
| E4 Elo | 4 (3 ∥) | S, M, S, S | after match-store / decision |
| E5 Elo→λ | 5 (2 ∥) | S, S, S, M, S | after E4 |
| E6 Sweeps | 1 | M | after four-arm-backtest |
| E7 Live | 3 | S, S, S | day 0, lane B |
| E8 Report | 1 | S | last |
| E9 Portuguese dashboard | 4 (2 ∥) | M, S, M, S | day 0, lane B |

**23 tickets.** Four can start on day 0 (shard-competitions, extract-leagues-param, report-strings-table, club-display-names). With two
people: one on the critical path, one on lane B — E7 and E9 are independent of
every model ticket and of each other — then the ∥ tickets of E4/E5. With one
person: the critical path in order, E7 and E9 slotted wherever a Docker run is
blocking. E9 is the natural fill while data-vs-league-backtest's backtest runs.

**Branching:** one branch per ticket, `feat/<ticket-slug>` — `feat/shard-competitions`
is the first. Ticket branches fork from `feat/match-brier`, which holds the spec,
the board and `dixon_coles`, and is acting as the integration branch until it is
merged to `main`.

---

## Known issues surfaced by tickets (not scheduled)

Real, pre-existing, and outside every ticket's scope. Recorded so they are not
rediscovered. Each becomes a ticket only when something needs it.

- **`tidy_fixtures.sql` has no `ORDER BY` on ties**, so its row order is not
  stable run to run. `equivalence-gates` had to align its lambda comparison by
  `fixture_id` to avoid a flaky `np.array_equal`. Any future test that compares
  positional arrays across two builds must do the same until the query orders
  deterministically.
- **Four "fast" tests read production pickles** (`src/files/pkl/`, gitignored):
  `test_batch_adapter.py::test_log_batch_matches_a_real_pickles_match_results` and
  three in `test_match_brier_harness.py`. They fail in every fresh worktree and in
  CI, and pass only where the pickles happen to exist. They should either carry a
  committed fixture or be marked `slow`.
- **`dixon_coles.fit` does not converge on the all-competitions graph** (207 clubs;
  `converged=False` at 200 and 5,000 iterations). On full seasons Série A ratings
  are stable to < 1e-3 across caps, so the decision gate is clean; on the live
  2026 season the drift is 4.3e-1 — the fit wanders where cup opponents have a
  handful of matches. Damped updates or a per-club minimum-matches rule belong
  to `dixon_coles.py`; until then, quote arm B on a partial season only with
  the drift number beside it.
- **`.superpowers/` was ignored only by a local, untracked rule** until
  `match-store` put it in the root `.gitignore`. Any checkout older than that
  commit will track agent reports.

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

**→ DECISION.** B beats A in ≥ 4 of 6 seasons with a pooled interval clear of zero:
proceed to E4. Otherwise the data is not the lever; open a *forecast-timing capture*
ticket instead and park E4–E6. Six seasons cannot support the eight-of-ten rule, so
the report labels whatever it finds **provisional**.

---

## E4 — Elo

### elo-interface · Interface ticket — **S**
**Blocked by:** match-store (code) · *decision* (value). Merged first; unblocks the ∥ tickets.

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

### elo-replay ∥ Replay core — **M**
**Blocked by:** elo-interface.

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

### elo-division-seeds ∥ Division seeding — **S**
**Blocked by:** elo-interface.

A club's seed on first appearance is set by the highest division it appears in
that season, derived from which league ids it plays in — *not* a hand-typed table.
Clubs seen only as cup opponents take the lowest tier; foreign clubs the foreign seed.

**Gate**
7. A club whose only appearance is a Copa do Brasil tie seeds at the bottom tier.
8. No club seeds at a value absent from `EloParams.seeds` (the notebook's silent 800 fallback).
- After burn-in, median(Série A) > median(Série B) > median(cup-only).

### elo-snapshots ∥ Snapshot table — **S**
**Blocked by:** elo-interface.

`ratings_as_of` via the daily-snapshot query pattern from `new-elo.ipynb` cell 18
(the one piece of the notebook worth keeping as-is), producing
`team_strength(team_id, as_of_date, elo, matches_used, competitions_used)`.

**Gate:** as-of a date strictly before a match, that match has not influenced the
rating (no leakage); the table is registered in DuckDB under its own name and no
existing query's relation set changes (re-run equivalence-gates's assertion).

---

## E5 — Elo → lambda and arm C

### elo-lambda-interface · Interface ticket — **S**
**Blocked by:** elo-replay, elo-division-seeds, elo-snapshots.

```python
def fit_difference_map(history, burn_in_season) -> Callable[[float], float]   # elo diff → expected goal diff
def total_goals_params(store, as_of) -> dict[int, float]                     # team_id → contribution to total
def lambdas(elo_home, elo_away, total_home, total_away, params) -> tuple[float, float]
```

### elo-difference-map ∥ Difference map — **S**
Regress observed 90-minute goal difference on `elo_home + H − elo_away` over the
burn-in season only. Monotone; fitted once; coefficients stored with the season
they came from. **Gate:** fitted on 2019, evaluated on 2020 it is monotone and
its slope is positive; no cubic, no unbounded output.

### elo-total-goals ∥ Total-goals parameters — **S**
Per-club contribution to total goals, opponent-adjusted the same way Dixon-Coles
adjusts attack/defence. **Gate:** league mean of `total_home + total_away`
matches the observed mean total goals within 2%.

### elo-adapter · `build_baseline` optional input + `elo` adapter — **M**
**Blocked by:** elo-difference-map, elo-total-goals.

`build_baseline` accepts `team_strength`; present → λ from the decomposition
`(total ± diff)/2`, clamped positive; absent → today's path, untouched. Register
`elo` in `SIMULATORS`.

**Gate:** with `team_strength` absent every λ is bit-identical to before
(re-run equivalence-gates's third assertion); with it present at least one λ differs; the
simulated fixture set is unchanged (re-run equivalence-gates's first assertion).

### four-arm-backtest · Arm C backtest — **S**
**Blocked by:** elo-adapter.

Add arm C to the backtest from data-vs-league-backtest, same matches, same seasons. **Gate:** C vs B
and C vs baseline reported per season and pooled; committed to
`src/files/exports/four_arms.csv`.

---

## E6 — Sweeps

### elo-sweeps · Elo parameter sweeps — **M**
**Blocked by:** four-arm-backtest.

On the `variant_sweep` harness: `k`, `home_advantage`, the seed values, the margin
ladder, per-competition `weight`, `from_round`. One knob at a time, 2020–2025,
paired against the four-arm-backtest defaults.

**Gate:** results committed; a flat result is reported as flat. **No default is
changed by this ticket** — retuning is a separate decision with its own ticket.

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

---

## E8 — Report

### findings-and-dashboard · `FINDINGS.md` + dashboard — **S**
**Blocked by:** four-arm-backtest (and elo-sweeps if run).

Four-arm table, the decision outcome, coverage manifests, and the chancedegol gap
before/after. **Gate:** every number in the doc names the export it came from.

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
- **`.superpowers/` was ignored only by a local, untracked rule** until
  `match-store` put it in the root `.gitignore`. Any checkout older than that
  commit will track agent reports.

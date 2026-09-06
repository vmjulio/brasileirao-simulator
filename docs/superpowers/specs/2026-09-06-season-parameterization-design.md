# Season parameterization

**Date:** 2026-09-06
**Status:** Approved, ready for implementation planning

## Problem

The simulator is hardcoded to the 2025 Brasileirão season across eight
locations spanning Python, SQL, and config. Adding the 2026 season today means
editing those shared files in place, which breaks the ability to re-run 2025.
Season is not a parameter; it is a property of the checkout.

The goal is to run `--season 2026` and `--season 2025` from the same working
tree, with each season's inputs, results, and exports isolated from the other,
and to make adding 2027 a matter of dropping a folder in rather than editing
code.

### Where the season is currently hardcoded

| # | Location | Hardcode |
|---|----------|----------|
| 1 | `domain/datasets.py:8-9` | `fixtures_2025.csv`, `fixtures_2024.csv` |
| 2 | `files/queries/tidy_fixtures.sql:17,34,53` | `league_season = '2024'` / `'2025'` / `in ('2025','2024')` |
| 3 | `files/queries/standings.sql:16` | `season = '2025'` |
| 4 | `adapters/poisson_same_venue_average_adapter.py:31` | `new_fixtures["season"] == 2025` (int, not str) |
| 5 | `config/settings.py` | `DATES` / `BACKFILL_DATES` are 2025 dates |
| 6 | `adapters/pickle_adapter.py:16,25` | no season in the path; the no-suffix case writes `average_results.pkl` for every season |
| 7 | `entrypoints/backfill2.py:25-83` | its own inline 2024 date list |
| 8 | `entrypoints/results_history_dataset.py:18`, `matches_history_dataset.py:15` | exports write to `DATASETS_PATH` root, unnamespaced |

### Structural constraint: DuckDB replacement scans

The SQL files reference bare table names — `fixtures`, `previous_year`,
`new_fixtures`, `enriched_tidy_fixtures`, `tidy_fixtures`, `df` — that DuckDB
resolves by replacement scan, picking up whichever Python variable of that name
is in the calling scope. This is why the module-level globals in `datasets.py`
work today, and why `get_brasileirao_standings` assigns
`enriched_tidy_fixtures = df` to a local that is never otherwise read
(`poisson_same_venue_average_adapter.py:78`).

Per-season loading breaks this implicit binding, so the design replaces it with
explicit `con.register(name, frame)`.

## Scope

In scope: fixtures, previous-year data, punters, doubles, pickled results,
season date lists, CSV exports, and the SQL that filters on season.

Out of scope, deliberately:

- **The lookback window** (`rn <= 19` in `team_params_same_venue_average.sql`,
  `rn <= 12` in `team_params_weighted_venue_average.sql`). A modeling knob, not
  a season-scoping problem. Tunable later as its own change.
- **League as a parameter.** `league_id = 71` stays hardcoded. The
  substitution mechanism below takes `$league_id` as a one-line addition when
  that becomes real work.
- **Reviving the bolão feature.** It is currently dead code (see Decisions).
- **A fetcher for new-season data.** Files are copied in by hand from the
  `lean-pype` pipeline; this spec documents the step rather than automating it.

## Design

### 1. Data layout

```
src/files/
  datasets/
    2024/  fixtures.csv, punters.json, doubles.json
    2025/  fixtures.csv, punters.json, doubles.json, dates.json
    2026/  fixtures.csv, dates.json
  pkl/
    2025/  average_results_2025-12-04.pkl, ...
    2026/  average_results_2026-03-01.pkl, ...
  exports/
    2025/  results_pivot.csv, positions.csv, relegation_points.csv, matches_results_pivot.csv
    2026/  ...
```

Season folders hold inputs only. Exports move out of `datasets/` because they
are outputs; mixing the two is why that directory holds twenty files today.
Pre-existing scratch and reference files at the `datasets/` root
(`campeonato-brasileiro-*.csv`, `out_*.csv`, `fixtures.csv`,
`previous_year.csv`) are left untouched — unrelated to this change.

`dates.json` has the shape `{"dates": ["2026-01-28", ...]}` and is generated
from that season's `fixtures.csv` distinct match dates by a helper, then
committed. The file lives on disk per season (rather than being derived at
runtime) so that a season's date list is inspectable and stable, but generating
it avoids transcribing a hundred dates by hand.

`BACKFILL_DATES` — today a list where processed dates are commented out one by
one — is removed. `backfill.py` selects a subset with `--from-date` /
`--to-date` instead.

### 2. `SeasonData`

A new module owning everything about one season's data:

```python
class SeasonData:
    def __init__(self, season: int, datasets_path: str = DATASETS_PATH) -> None: ...
    def register(self, con: duckdb.DuckDBPyConnection) -> None: ...
```

Responsibilities:

- Load `{season}/fixtures.csv`.
- Resolve previous-year data as `{season - 1}/fixtures.csv`, read directly.
  No duplicated `previous_year.csv` per season.
- Load `{season}/dates.json`.
- Load `punters.json` / `doubles.json` if present, else leave them `None`.
- `register()` binds each frame onto a DuckDB connection by the name the SQL
  expects, replacing the replacement-scan binding.

Callers construct `SeasonData(2026)` and never see a path. This also removes
the file IO that `datasets.py` performs at import time today, where importing
`tables.py` reads four files off disk as a side effect.

**Missing previous season.** If `{season - 1}/fixtures.csv` does not exist,
raise an error naming the exact missing path. Simulating without the
previous-season union would silently produce a model with no history to fall
back on, which matters most in exactly the case where it would go unnoticed —
the first rounds of a new season.

**Why previous-year data matters.** The lookback windows are 19 games per venue
(same-venue-average strategy) and 12 (weighted-venue strategy). Early in a
season a team has not played 19 home games yet, so the union with the previous
season backfills the window. This is the mechanism that makes an "as of an
early date" simulation meaningful, and it must keep working for 2026 reaching
back into 2025.

### 3. SQL parameterization

`Queries` takes a season and substitutes placeholders via
`string.Template.safe_substitute` — standard library, and unlike `str.format`
it will not break if the SQL ever contains a literal brace.

Changes:

- `tidy_fixtures.sql:17` → `where league_season = '$previous_season' and league_id = 71`
- `tidy_fixtures.sql:34` → `where league_season = '$season' and league_id = 71`
- `tidy_fixtures.sql:53` → `WHERE fixtures.league_season in ('$season', '$previous_season')`
- `standings.sql:16` → `and season = '$season'`

The remaining SQL files (`enriched_tidy_fixtures`, `match_results`,
`team_params*`, `bolao_standings`) contain no season literals and are unchanged.

**Dtype hazard.** The `season` column arrives as int64 from
`pd.read_csv`, while the SQL compares against quoted string literals that
DuckDB casts. The pandas-side filter at
`poisson_same_venue_average_adapter.py:31` must therefore receive an `int`,
while the SQL substitution receives a `str`. Reversing these produces an empty
DataFrame and no exception — a silent wrong answer rather than a crash. The
tests below cover this directly.

### 4. Threading the parameter

`SimulationParams` gains `season: int`, required, with no default. The season
decides which data is being simulated; a default is how a 2026 run ends up
reading 2025 data unnoticed.

Flow: entrypoint `--season` → `SimulationParams` → `SimulationService` →
`Tables(season)` and the simulator adapter → `Queries(season)`.

Entrypoints (`current_probabilities.py`, `backfill.py`, the three dataset
exporters) parse `--season` with argparse. `entrypoint.sh` passes
`--season "$SEASON"`; `docker-compose.yml` sets `SEASON: ${SEASON:-2026}`.
Re-running an older season is then `SEASON=2025 make all`.

`backfill2.py` carries a stale inline 2024 date list, duplicating
`backfill.py` with dates for a season that is data-only in this checkout (see
§6). Delete it; `backfill.py --season N --from-date … --to-date …` covers the
use case for any season that has a folder.

### 5. Persistence

`PickleAdapter(directory, season)` resolves paths under
`files/pkl/{season}/`, creating the directory when absent. This fixes the
no-suffix collision where `current_probabilities.py` writes
`average_results.pkl` for every season to the same path.

`DataTransformationService.results_pkl_to_rows` and its siblings currently call
`.items()` on the result of `load_results`, which returns `None` for a missing
file. Guard this while touching the class: a date with no pickle should be
skipped, not raise `AttributeError`.

### 6. Migration

One-time, as `git mv` so history is preserved and the change is reversible:

1. `datasets/fixtures_2025.csv` → `datasets/2025/fixtures.csv`
2. `datasets/fixtures_2024.csv` → `datasets/2024/fixtures.csv`
3. `datasets/punters.json`, `doubles.json` → `datasets/2025/`
4. `datasets/punters_2024.json`, `doubles_2024.json` → `datasets/2024/` (renamed to `punters.json`, `doubles.json`)
5. Generate `datasets/2025/dates.json` from the current `DATES` list verbatim,
   so backfill reproduces identically. Then delete `DATES` and
   `BACKFILL_DATES` from `settings.py`.
6. `files/pkl/*.pkl` and `pkl_bkp/*.pkl` → `files/pkl/{year}/`, keyed on the
   date already in each filename.
7. Copy `lean-pype/app/files/processed_fixtures_2026_71.csv` →
   `datasets/2026/fixtures.csv`, and generate `datasets/2026/dates.json` from
   it. The schema matches the existing fixtures files exactly: 38 columns, 380
   rows.

**2024 is data-only.** The 2024 folder exists to serve as 2025's previous-year
source. Running `--season 2024` would require a 2023 folder that does not
exist, and would fail with the error from §2. That is correct behaviour, not a
gap: 2024 has no `dates.json` and is not a simulatable season in this
checkout. Adding one later means supplying 2023 fixtures.

Step 6 assumes the date in a pickle filename shares a year with its season.
This holds for every file present, including 2026 (whose season opens
2026-01-28), because a Brasileirão season sits inside one calendar year. The
assumption is used only by the migration; no runtime code depends on it.

### 7. Testing

`tests/` is empty today. This refactor's characteristic failure is a silently
empty DataFrame rather than an exception, so the minimum set targets that:

- `SeasonData(2026)` loads 380 fixture rows and previous-year data that is
  2025's.
- A season whose previous-season folder is absent raises an error naming the
  path.
- `Queries(2026)` output contains no unsubstituted `$` placeholder.
- Smoke: two iterations for 2026 with a mid-season `ignore_results_after`
  yields standings of 20 teams with probabilities summing to approximately 100%.
- Regression guard: the same smoke test for 2025 still produces non-empty
  standings. This is the evidence that adding 2026 did not break 2025.

## Decisions

**Season folders over flat per-season filenames.** Flat names
(`fixtures_2026.csv`, `dates_2026.json`, `punters_2026.json`) would mean a new
naming convention per dataset type and continued accumulation of loose files in
one directory. A folder per season makes "what does season N need?" answerable
by listing one directory.

**Previous-year data resolved, not copied.** Season N reads season N-1's own
`fixtures.csv`. The alternative, a `previous_year.csv` copy inside each season
folder, duplicates data that can drift from its source and adds a manual copy
step every year. The cost is that season N-1's folder must exist, which the
error in `SeasonData` makes explicit.

**Explicit `con.register` over DuckDB replacement scans.** The implicit binding
between a Python variable name and a SQL table name is invisible at both ends;
`get_brasileirao_standings` currently contains an assignment to a local whose
only purpose is to be found by a scan. Per-season data makes this untenable.

**punters/doubles load optionally.** The bolão feature is currently dead: no
code joins punter columns onto fixtures, and `get_bolao_standings` is never
called from `SimulationRunner.run()`, though `inspect_dataset.py:13` still
references a `home_punter` column that nothing produces. The datasets are
carried forward season-scoped for symmetry, but 2026 must not require a
`punters.json` to run. Reviving the feature is separate work.

**`season` is required on `SimulationParams`.** See §4.

## Open questions

None. Lookback window and league parameterization are explicitly deferred
above.

# brasileirao-simulator

Monte Carlo simulation of the Brasileirão. Remaining fixtures are simulated
with a Poisson model built from each team's recent scoring and conceding
averages, over many iterations, to produce title and relegation probabilities.

## Running

```
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/current_probabilities.py --season 2026
```

Simulate as of a past date with `--date 2026-03-01`, and set the iteration
count with `--iterations`.

Replay a whole season day by day:

```
docker-compose run --rm app python3 brasileirao_simulator/entrypoints/backfill.py --season 2026 --from-date 2026-01-28
```

`make all` runs the full pipeline for the season in `$SEASON` (default 2026):

```
SEASON=2025 make all
```

Backfill skips any date that already has a pickled result, so a repeat run only
fills gaps left by a previous one — it does not recompute a season that is
already backfilled. Pass `--force` to `backfill.py` to replay a date anyway;
that adds its new iterations on top of the ones already stored, it does not
replace them.

Results are pickled under `src/files/pkl/{season}/` and CSV exports land in
`src/files/exports/{season}/`.

## Adding a season

1. Create `src/files/datasets/{season}/` and copy that season's fixtures in as
   `fixtures.csv`. The source is the `lean-pype` pipeline's
   `processed_fixtures_{season}_71.csv` (league 71 is Serie A).
2. Generate the date list:
   `docker-compose run --rm app python3 brasileirao_simulator/entrypoints/generate_season_dates.py --season {season}`
3. Run with `--season {season}`.

The previous season's folder must exist: early-season simulations reach back
into it for scoring averages, since a team has not yet played enough games in
the new season to fill the lookback window.

## Tests

```
docker-compose run --rm app pytest /tests -v
```

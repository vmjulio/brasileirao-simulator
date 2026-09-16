"""Sweep the team-parameter lookback window and measure match-outcome skill.

See docs/superpowers/specs/2026-09-08-lookback-sweep-design.md for the full
rationale. In short: match_brier_backtest.py found `batch` beats a base-rate
reference by only 2.8% at horizon 0 (the last forecast before each match,
every prior result known - the model at its most informed). That could mean
football is close to unpredictable at match level, or that the constants
producing the rate estimates - starting with the 19-match lookback window -
leave signal behind. This sweeps the window; the other constants (recency
weights, attack/defence blend, newcomer prior) are out of scope here, each
its own axis to sweep later if this one moves skill.

THE CONFOUND THIS MODULE MUST NOT REINTRODUCE: the window and the
shrinkage-blend denominator lived on one shared literal (19) before
domain/queries.py grew a $lookback parameter (see
tests/test_lookback_window.py). Queries(season, lookback=...) moves both
together, so this module can pass one lookback value straight through
without doing anything special - the query itself guarantees a full-window
team stays fully self-determined at every window size.

Does not modify batch_poisson_adapter.py: IterationBatchAdapter always
builds Queries(season) with no lookback, since every other caller must keep
seeing the full-window default. LookbackBatchAdapter below is a subclass
that swaps in the parameterised query afterwards - composition instead of
adding a constructor argument that only this sweep would ever use.

Every pickle this module writes goes through PickleAdapter to a scratch
directory (default `files/pkl_lookback_sweep/`), one subdirectory per
window, deleted immediately after that window is scored - never
files/pkl/, the real, gitignored, unrecoverable production history.

Common random numbers: every window replays the same `dates` list and draws
each date from np.random.default_rng(seed + date_index), so all windows see
an identical per-date RNG stream and the BETWEEN-WINDOW comparison is
paired - Monte Carlo noise mostly cancels, leaving the window's real effect
(if any) as the difference.

This sweep runs `batch` only. team_match_counts.sql (which feeds
`uncertain`'s n_eff) still carries its own independent `rn <= 19` and is
deliberately left alone - do not run this sweep with `uncertain` until the
two are reconciled (see the design doc's Scope section).

This measures; it does not retune. Do not change any default based on the
result.

MULTI-SEASON EXTENSION (2016-2025): the single-season run above (2025 only,
n=374, no CI) left the headline 19-vs-26 comparison unresolved - 19->26
gained only 0.17pp of skill, roughly the noise floor at that sample size.
run_multi_season_sweep below replays the same per-window generate/score/
delete cycle across ten seasons and adds what the single-season run skipped:
a paired bootstrap (reusing match_brier_backtest.paired_bootstrap_ci)
between each challenger window and the lookback=19 baseline, per season AND
pooled across all ten. The pairing is exact within a season - every window
replays the identical as-of dates at np.random.default_rng(seed +
date_index), so a challenger window's matches line up one-to-one with
baseline's - but only within a season; RNG streams are not shared across
seasons (different fixture calendars), so the pooled bootstrap treats all
~3,799 scored matches as one flat sample (see run_multi_season_sweep's
docstring for exactly how "pooled" is computed).

2016 played 379 matches, not 380 - the Chapecoense x Atletico-MG round-38
fixture is CANC (cancelled after the November 2016 crash) in fixtures.csv,
so played_matches' goals_home-not-null filter already drops it with no
special-casing needed here.
"""

import argparse
import shutil
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.domain.batch_simulation import FULL_WINDOW_MATCHES
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.match_brier_backtest import (
    assign_horizon0,
    base_rate_probs,
    match_brier,
    match_forecast_probs,
    one_hot_outcomes,
    paired_bootstrap_ci,
    played_matches,
    skill_score,
)
from brasileirao_simulator.service_layer.simulation_service import SimulationService


SCRATCH_ROOT = "files/pkl_lookback_sweep"
DEFAULT_WINDOWS = (8, 12, FULL_WINDOW_MATCHES, 26)
DEFAULT_SEASONS = tuple(range(2016, 2026))  # 2016-2025; 2015 is prior-season-only data, 2026 is mid-season.
BASELINE_LOOKBACK = FULL_WINDOW_MATCHES  # what every challenger window is paired against.


class LookbackBatchAdapter(IterationBatchAdapter):
    """IterationBatchAdapter with an overridable team-parameter lookback.

    Built by subclassing rather than adding a constructor argument to
    batch_poisson_adapter.py - that file is out of scope for this sweep, and
    every one of its other callers must keep getting the full-window default.
    simulate_batch is inherited unchanged; only self.queries differs.
    """

    def __init__(
        self,
        strategy: Optional[str],
        season: int,
        lookback: int,
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        super().__init__(strategy, season, rng=rng)
        self.queries = Queries(season, lookback=lookback)


# ---------------------------------------------------------------------------
# Generating one window's forecasts into scratch.
# ---------------------------------------------------------------------------


def generate_lookback_forecasts(
    season: int,
    lookback: int,
    iterations: int,
    dates: list,
    scratch_root: str = SCRATCH_ROOT,
    seed: int = 0,
) -> str:
    """Replay `season` date by date at one lookback window, writing one
    pickle per date under `{scratch_root}/lookback_{lookback}/{season}/` via
    PickleAdapter. Returns that variant directory.

    Mirrors match_brier_backtest.generate_forecast_pickles: one shot per date
    (max_batch_size == iterations), load_results=False (nothing resumed from
    a previous scratch run), and np.random.default_rng(seed + date_index) per
    date - common random numbers, so every window this sweep runs at the same
    seed sees an identical per-date stream.
    """
    variant_dir = f"{scratch_root}/lookback_{lookback}"
    persistence = PickleAdapter(variant_dir, season)
    strategy = SimulationParams(season=season).strategy

    for date_index, date in enumerate(dates):
        rng = np.random.default_rng(seed + date_index)
        simulator = LookbackBatchAdapter(strategy, season, lookback, rng=rng)
        params = SimulationParams(
            season=season,
            iterations=iterations,
            max_batch_size=iterations,
            ignore_results_after=date,
            load_results=False,
        )
        SimulationService(
            persistence_adapter=persistence, simulator_adapter=simulator, params=params
        ).run_simulation(print_results=False)

    return variant_dir


# ---------------------------------------------------------------------------
# Scoring one window's forecasts at horizon 0.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowScore:
    lookback: int
    n_scored: int
    missing: int
    mean_brier: float
    mean_reference_brier: float
    skill: float


def score_lookback_window_matches(
    season: int, lookback: int, root: str, dates: list, strategy: str = "average"
) -> pd.DataFrame:
    """One row per horizon-0 match scored against `root`'s pickles: match_key,
    local_date, as_of_date, outcome, brier, reference_brier. The match-level
    building block behind score_lookback_window's single-window summary AND
    run_multi_season_sweep's paired comparison - pairing a challenger
    window's diff against the lookback=19 baseline needs each match's own
    Brier score, not just the season mean.

    df.attrs["missing"] carries the count of played matches whose pickle (or
    match_results entry) was not found - reported, never silently dropped
    into a smaller N (see match_brier_backtest.MatchBrierRun's docstring for
    why that matters).
    """
    played = assign_horizon0(played_matches(season), dates)
    persistence = PickleAdapter(root, season)
    reference_probs = base_rate_probs(played_matches(season)["outcome"])

    rows = []
    missing = 0
    for row in played.itertuples(index=False):
        probs = match_forecast_probs(persistence, strategy, row.as_of_date, row.match_key)
        if probs is None:
            missing += 1
            continue
        outcome_one_hot = one_hot_outcomes([row.outcome])
        rows.append(
            {
                "match_key": row.match_key,
                "local_date": row.local_date,
                "as_of_date": row.as_of_date,
                "outcome": row.outcome,
                "brier": float(match_brier([probs], outcome_one_hot)[0]),
                "reference_brier": float(match_brier([reference_probs], outcome_one_hot)[0]),
            }
        )

    matches = pd.DataFrame(
        rows, columns=["match_key", "local_date", "as_of_date", "outcome", "brier", "reference_brier"]
    )
    matches.attrs["missing"] = missing
    return matches


def score_lookback_window(
    season: int, lookback: int, root: str, dates: list, strategy: str = "average"
) -> WindowScore:
    """Score one window's pickles at horizon 0 - the last forecast made
    before each match, every match scored exactly once (see
    match_brier_backtest.py's module docstring for why pooling across as-of
    dates would bias the sample). Thin summary wrapper around
    score_lookback_window_matches.
    """
    matches = score_lookback_window_matches(season, lookback, root, dates, strategy)
    mean_brier = float(matches["brier"].mean())
    mean_reference_brier = float(matches["reference_brier"].mean())
    return WindowScore(
        lookback=lookback,
        n_scored=len(matches),
        missing=matches.attrs["missing"],
        mean_brier=mean_brier,
        mean_reference_brier=mean_reference_brier,
        skill=skill_score(mean_brier, mean_reference_brier),
    )


# ---------------------------------------------------------------------------
# The sweep itself: generate, score, delete, one window at a time.
# ---------------------------------------------------------------------------


def run_sweep(
    season: int,
    windows: list,
    iterations: int,
    seed: int = 0,
    scratch_root: str = SCRATCH_ROOT,
) -> pd.DataFrame:
    """One row per window: lookback, n_scored, missing, mean_brier,
    mean_reference_brier, skill.

    `dates` is computed once and reused for every window, which is what
    makes the common-random-number pairing exact: every window replays
    literally the same as-of dates in the same order, so
    np.random.default_rng(seed + date_index) hands each window the identical
    stream at every date.

    Each window's scratch directory is deleted immediately after it is
    scored, not after the whole sweep - the disk footprint of one window at
    a time only, and a crash partway through a later window leaves earlier
    windows' scratch already cleaned up.
    """
    dates = backfill_dates(season)
    rows = []
    for lookback in windows:
        variant_dir = generate_lookback_forecasts(
            season, lookback, iterations, dates, scratch_root=scratch_root, seed=seed
        )
        try:
            score = score_lookback_window(season, lookback, variant_dir, dates)
        finally:
            shutil.rmtree(variant_dir, ignore_errors=True)
        rows.append(vars(score))

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Multi-season extension: pair every challenger window against the lookback
# =19 baseline, per season and pooled, to check whether the 2025-only result
# (26 edges out 19 by 0.17pp of skill, inside the single-season noise floor)
# is stable across seasons or was one season's Monte Carlo noise.
# ---------------------------------------------------------------------------


def _paired_diff(challenger: pd.DataFrame, baseline: pd.DataFrame, ci_seed: int) -> tuple:
    """(mean_diff, ci_low, ci_high) for challenger_brier - baseline_brier,
    matched row-for-row on match_key (per season, match_key alone is unique -
    each fixture is played once). Negative mean_diff: challenger beats
    baseline on average. Both frames come from the SAME season replayed at
    the SAME seed (see generate_lookback_forecasts), so this is the paired,
    common-random-number comparison paired_bootstrap_ci is built for - not
    an independent-samples comparison.
    """
    aligned = challenger.set_index("match_key")["brier"].align(
        baseline.set_index("match_key")["brier"], join="inner"
    )
    if len(aligned[0]) != len(challenger) or len(aligned[0]) != len(baseline):
        raise ValueError(
            "challenger and baseline match sets differ - expected identical "
            "match_key sets within one season/window pair"
        )
    diffs = (aligned[0] - aligned[1]).to_numpy()
    return paired_bootstrap_ci(diffs, seed=ci_seed)


def run_multi_season_sweep(
    seasons: list,
    windows: list,
    iterations: int,
    seed: int = 0,
    ci_seed: int = 0,
    baseline: int = BASELINE_LOOKBACK,
    scratch_root: str = SCRATCH_ROOT,
) -> tuple:
    """Run the window sweep across many seasons and pair every challenger
    window against `baseline` (default 19) within each season.

    Returns (per_season, pooled, matches_by_season):

    - per_season: one row per (season, lookback) - n_scored, missing,
      mean_brier, mean_reference_brier, skill, diff_vs_baseline (challenger -
      baseline mean Brier; 0 for the baseline row itself), diff_ci_low,
      diff_ci_high (95% paired-bootstrap CI on that diff), beats_baseline
      (challenger's mean_brier < baseline's that season; None for the
      baseline row).

    - pooled: one row per lookback, aggregated across every season.
      Aggregation is MATCH-weighted, not season-weighted: every scored
      match from every season is concatenated into one flat sample
      (~3,799 matches: 379 for 2016's cancelled-fixture season, 380 for
      each of the other nine) before taking the mean and running the
      paired bootstrap. Because season sizes differ by at most one match,
      match-weighting and season-weighting agree almost exactly here;
      `mean_skill_across_seasons` (the plain, season-weighted average of
      the ten per-season skill numbers) is reported alongside `skill`
      (the match-weighted pooled figure) so the two can be compared
      directly rather than asserting they must agree.

      The pooled bootstrap CI is NOT a common-random-number comparison
      across seasons (each season's RNG stream is independent, seeded by
      date index within that season only) - it is a plain paired bootstrap
      over the pooled diffs, clustered by match as usual. That still
      cancels each match's difficulty (challenger and baseline forecast
      the identical match) and, within a season, the shared Monte Carlo
      draw; it does not cancel noise BETWEEN seasons, which is exactly
      what pooling ten independent seasons is for.

    - matches_by_season: {season: {lookback: matches_df}}, returned so a
      caller (or a test) can re-derive anything above without re-running
      the sweep.

    Every window's scratch directory is generated, scored, and deleted
    before the next window starts (see generate_lookback_forecasts /
    score_lookback_window_matches) - at most one window's pickles for one
    season ever sit on disk at a time, never files/pkl/.
    """
    per_season_rows = []
    matches_by_season = {}

    for season in seasons:
        dates = backfill_dates(season)
        matches_by_window = {}
        for lookback in windows:
            variant_dir = generate_lookback_forecasts(
                season, lookback, iterations, dates, scratch_root=scratch_root, seed=seed
            )
            try:
                matches_by_window[lookback] = score_lookback_window_matches(
                    season, lookback, variant_dir, dates
                )
            finally:
                shutil.rmtree(f"{variant_dir}/{season}", ignore_errors=True)
        matches_by_season[season] = matches_by_window

        baseline_matches = matches_by_window[baseline]
        baseline_mean_brier = float(baseline_matches["brier"].mean())

        for lookback in windows:
            matches = matches_by_window[lookback]
            mean_brier = float(matches["brier"].mean())
            mean_reference_brier = float(matches["reference_brier"].mean())
            row = {
                "season": season,
                "lookback": lookback,
                "n_scored": len(matches),
                "missing": matches.attrs["missing"],
                "mean_brier": mean_brier,
                "mean_reference_brier": mean_reference_brier,
                "skill": skill_score(mean_brier, mean_reference_brier),
            }
            if lookback == baseline:
                row.update(
                    {"diff_vs_baseline": 0.0, "diff_ci_low": 0.0, "diff_ci_high": 0.0, "beats_baseline": None}
                )
            else:
                mean_diff, lo, hi = _paired_diff(matches, baseline_matches, ci_seed)
                row.update(
                    {
                        "diff_vs_baseline": mean_diff,
                        "diff_ci_low": lo,
                        "diff_ci_high": hi,
                        "beats_baseline": mean_brier < baseline_mean_brier,
                    }
                )
            per_season_rows.append(row)

    per_season = pd.DataFrame(per_season_rows)

    pooled_rows = []
    baseline_pool = pd.concat(
        [
            matches_by_season[s][baseline].assign(season=s)
            for s in seasons
        ],
        ignore_index=True,
    )
    for lookback in windows:
        pool = pd.concat(
            [matches_by_season[s][lookback].assign(season=s) for s in seasons], ignore_index=True
        )
        mean_brier = float(pool["brier"].mean())
        mean_reference_brier = float(pool["reference_brier"].mean())
        row = {
            "lookback": lookback,
            "n_scored": len(pool),
            "mean_brier": mean_brier,
            "mean_reference_brier": mean_reference_brier,
            "skill": skill_score(mean_brier, mean_reference_brier),
            "mean_skill_across_seasons": float(
                per_season.loc[per_season["lookback"] == lookback, "skill"].mean()
            ),
        }
        if lookback == baseline:
            row.update({"diff_vs_baseline": 0.0, "diff_ci_low": 0.0, "diff_ci_high": 0.0})
        else:
            pool_key = pool.set_index(["season", "match_key"])["brier"]
            baseline_key = baseline_pool.set_index(["season", "match_key"])["brier"]
            aligned = pool_key.align(baseline_key, join="inner")
            if len(aligned[0]) != len(pool) or len(aligned[0]) != len(baseline_pool):
                raise ValueError(
                    "pooled challenger and baseline match sets differ across seasons"
                )
            diffs = (aligned[0] - aligned[1]).to_numpy()
            mean_diff, lo, hi = paired_bootstrap_ci(diffs, seed=ci_seed)
            row.update({"diff_vs_baseline": mean_diff, "diff_ci_low": lo, "diff_ci_high": hi})
        pooled_rows.append(row)

    pooled = pd.DataFrame(pooled_rows)

    return per_season, pooled, matches_by_season


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--season", type=int, default=None, help="single-season mode (original CLI, unchanged)."
    )
    parser.add_argument(
        "--seasons",
        default=None,
        help="multi-season mode: comma-separated seasons, e.g. 2016,2017,...,2025. "
        "Adds paired-bootstrap CIs vs the --baseline window, per season and pooled. "
        "Mutually exclusive with --season.",
    )
    parser.add_argument(
        "--windows",
        default=",".join(str(w) for w in DEFAULT_WINDOWS),
        help="comma-separated lookback windows, e.g. 8,12,19,26.",
    )
    parser.add_argument(
        "--baseline",
        type=int,
        default=BASELINE_LOOKBACK,
        help="multi-season mode only: the window every other window is paired against (default 19).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5_000,
        help="per as-of-date iteration count for every window "
        "(measured at ~0.66s/date at 5,000 iterations).",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--ci-seed", type=int, default=0, help="multi-season mode only: bootstrap RNG seed.")
    parser.add_argument("--scratch-root", default=SCRATCH_ROOT)
    parser.add_argument(
        "--out", default=None, help="write the sweep table to this CSV path (per-season table in multi-season mode)."
    )
    parser.add_argument(
        "--pooled-out", default=None, help="multi-season mode only: write the pooled table to this CSV path."
    )
    args = parser.parse_args()

    windows = [int(w) for w in args.windows.split(",")]

    if args.seasons and args.season:
        parser.error("pass --season or --seasons, not both")
    if not args.seasons and not args.season:
        parser.error("pass --season (single-season) or --seasons (multi-season)")

    if args.seasons:
        seasons = [int(s) for s in args.seasons.split(",")]

        per_season, pooled, _ = run_multi_season_sweep(
            seasons=seasons,
            windows=windows,
            iterations=args.iterations,
            seed=args.seed,
            ci_seed=args.ci_seed,
            baseline=args.baseline,
            scratch_root=args.scratch_root,
        )

        print(
            f"seasons: {seasons}  windows: {windows}  baseline: {args.baseline}  "
            f"iterations/date: {args.iterations}  seed: {args.seed}"
        )
        print()
        print("--- per season ---")
        print(
            per_season[
                [
                    "season",
                    "lookback",
                    "n_scored",
                    "missing",
                    "mean_brier",
                    "skill",
                    "diff_vs_baseline",
                    "diff_ci_low",
                    "diff_ci_high",
                    "beats_baseline",
                ]
            ].to_string(index=False)
        )
        print()
        print("--- pooled (match-weighted across all seasons) ---")
        print(
            pooled[
                [
                    "lookback",
                    "n_scored",
                    "mean_brier",
                    "skill",
                    "mean_skill_across_seasons",
                    "diff_vs_baseline",
                    "diff_ci_low",
                    "diff_ci_high",
                ]
            ].to_string(index=False)
        )

        for lookback in windows:
            if lookback == args.baseline:
                continue
            rows = per_season[per_season["lookback"] == lookback]
            wins = int(rows["beats_baseline"].sum())
            print()
            print(
                f"lookback={lookback} beats lookback={args.baseline} in "
                f"{wins}/{len(rows)} seasons (point estimate, mean Brier)."
            )

        if args.out:
            per_season.to_csv(args.out, index=False)
            print(f"\nper-season table written to {args.out}")
        if args.pooled_out:
            pooled.to_csv(args.pooled_out, index=False)
            print(f"pooled table written to {args.pooled_out}")

    else:
        table = run_sweep(
            season=args.season,
            windows=windows,
            iterations=args.iterations,
            seed=args.seed,
            scratch_root=args.scratch_root,
        )

        print(f"season: {args.season}  iterations/date: {args.iterations}  seed: {args.seed}")
        print(f"windows: {windows}")
        print()
        print(
            table[["lookback", "n_scored", "missing", "mean_brier", "mean_reference_brier", "skill"]]
            .to_string(index=False)
        )

        skill_span = table["skill"].max() - table["skill"].min()
        print()
        print(f"skill range across windows: {skill_span * 100:.2f} percentage points")

        if args.out:
            table.to_csv(args.out, index=False)
            print(f"\nsweep table written to {args.out}")

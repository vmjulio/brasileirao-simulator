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
    played_matches,
    skill_score,
)
from brasileirao_simulator.service_layer.simulation_service import SimulationService


SCRATCH_ROOT = "files/pkl_lookback_sweep"
DEFAULT_WINDOWS = (8, 12, FULL_WINDOW_MATCHES, 26)


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


def score_lookback_window(
    season: int, lookback: int, root: str, dates: list, strategy: str = "average"
) -> WindowScore:
    """Score one window's pickles at horizon 0 - the last forecast made
    before each match, every match scored exactly once (see
    match_brier_backtest.py's module docstring for why pooling across as-of
    dates would bias the sample). Reuses that harness's own building blocks
    (assign_horizon0, match_forecast_probs, match_brier, base_rate_probs,
    skill_score) rather than its two-simulator score_horizon0 orchestrator,
    since this sweep only ever has one arm (batch) to score per window.
    """
    played = assign_horizon0(played_matches(season), dates)
    persistence = PickleAdapter(root, season)
    reference_probs = base_rate_probs(played_matches(season)["outcome"])

    briers = []
    reference_briers = []
    missing = 0
    for row in played.itertuples(index=False):
        probs = match_forecast_probs(persistence, strategy, row.as_of_date, row.match_key)
        if probs is None:
            missing += 1
            continue
        outcome_one_hot = one_hot_outcomes([row.outcome])
        briers.append(float(match_brier([probs], outcome_one_hot)[0]))
        reference_briers.append(float(match_brier([reference_probs], outcome_one_hot)[0]))

    mean_brier = float(np.mean(briers))
    mean_reference_brier = float(np.mean(reference_briers))
    return WindowScore(
        lookback=lookback,
        n_scored=len(briers),
        missing=missing,
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--windows",
        default=",".join(str(w) for w in DEFAULT_WINDOWS),
        help="comma-separated lookback windows, e.g. 8,12,19,26.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5_000,
        help="per as-of-date iteration count for every window "
        "(measured at ~0.66s/date at 5,000 iterations).",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--scratch-root", default=SCRATCH_ROOT)
    parser.add_argument(
        "--out", default=None, help="write the sweep table to this CSV path."
    )
    args = parser.parse_args()

    windows = [int(w) for w in args.windows.split(",")]

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

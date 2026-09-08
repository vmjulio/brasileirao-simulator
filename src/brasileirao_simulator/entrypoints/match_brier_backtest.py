"""Score a simulator's MATCH-level forecasts against real results.

C2's title-level backtest (calibration_backtest.py) scores one number per
season: who won the title. A season has one champion, so its 110-odd
backfill dates x 20 teams all resolve against a single realised outcome -
the effective sample is close to n=1, and the differences it measured
between `batch` and `uncertain` sat inside Monte Carlo noise.

A match outcome is what the model predicts DIRECTLY (a title probability is
a heavily aggregated derivative of 380 Poisson draws), and a season has
~380 of them, each independently resolved. This module scores those.

THE DESIGN CONSTRAINT THIS WHOLE MODULE EXISTS TO ENFORCE: never pool a
match's forecasts across the many as-of dates it was forecast at. A
round-38 match is forecast at ~100 different dates, all resolving against
the same result; pooling them would let matches whose outcome stays
uncertain longest dominate the sample - the exact bias that made a 2025
relegation-race club (hovering in the drop zone for eight weeks) look
badly overconfident when a per-club weighting showed no defect at all. So:
every match is scored EXACTLY ONCE, at the last forecast made before it was
played (see assign_horizon0) - one observation per match, full stop.

Every pickle this module WRITES goes through PickleAdapter to a scratch
directory (default `files/pkl_match_brier/`), never to `files/pkl/` - the
real, gitignored, unrecoverable simulation history lives there. Pickles this
module READS may come from `files/pkl/` (real production backfills, already
on disk for some seasons) with no writes back to them.
"""

import argparse
import bisect
import shutil
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.uncertain_params_adapter import UncertainParamsAdapter
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.season_dates import BRAZIL_UTC_OFFSET_HOURS
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.service_layer.simulation_service import SimulationService


SCRATCH_ROOT = "files/pkl_match_brier"

# home/draw/away, in the fixed column order every one-hot vector and
# probability triple in this module uses.
OUTCOMES = ("home", "draw", "away")


# ---------------------------------------------------------------------------
# Metrics - pure functions of (forecasts, outcomes), no simulator or fixture
# data involved. See tests/test_match_brier_metric.py for the synthetic
# cases these are checked against (perfect forecaster -> 0, maximally wrong
# -> 2, multiclass reduces to the familiar binary form on a degenerate
# two-outcome case).
# ---------------------------------------------------------------------------


def one_hot_outcomes(labels) -> np.ndarray:
    """labels: a sequence of 'home'/'draw'/'away'. Returns an (n, 3) one-hot
    array in OUTCOMES column order - the shape match_brier expects for its
    `outcomes` argument."""
    labels = list(labels)
    unknown = sorted(set(labels) - set(OUTCOMES))
    if unknown:
        raise ValueError(f"unknown outcome label(s) {unknown}; expected one of {OUTCOMES}")
    index = {outcome: i for i, outcome in enumerate(OUTCOMES)}
    encoded = np.zeros((len(labels), 3))
    for row, label in enumerate(labels):
        encoded[row, index[label]] = 1.0
    return encoded


def match_brier(forecasts, outcomes) -> np.ndarray:
    """Per-match multiclass Brier score: the sum of squared errors across the
    three outcomes (home/draw/away), where the realised outcome is 1 and the
    other two are 0. Range [0, 2] - 0 for a certain-and-correct forecast, 2
    for a certain-and-WRONG one (all mass on an outcome that did not
    happen). This is the K=3 case of Brier's original multi-category score;
    for K=2 it is exactly double the single-probability form most people
    call "the Brier score" (see the degenerate-case unit test).

    forecasts: (n, 3) array-like of probabilities, OUTCOMES column order.
    outcomes: (n, 3) one-hot array-like in the same column order (see
    one_hot_outcomes). Returns an (n,) array, one value per match.
    """
    forecasts = np.atleast_2d(np.asarray(forecasts, dtype=float))
    outcomes = np.atleast_2d(np.asarray(outcomes, dtype=float))
    if forecasts.shape != outcomes.shape:
        raise ValueError(
            f"forecasts and outcomes must be the same shape ({forecasts.shape} vs {outcomes.shape})"
        )
    if forecasts.shape[-1] != 3:
        raise ValueError(
            f"forecasts must have 3 columns (home, draw, away), got {forecasts.shape[-1]}"
        )
    return np.sum((forecasts - outcomes) ** 2, axis=-1)


def base_rate_probs(outcomes) -> tuple:
    """The empirical (home, draw, away) frequency across `outcomes` - the
    forecast a trivial "knows nothing but the base rate" reference
    forecaster gives every match, computed from real data rather than
    hardcoded so it reflects whatever season(s) it is asked to summarise."""
    labels = list(outcomes)
    if not labels:
        raise ValueError("cannot compute base rates from zero matches")
    n = len(labels)
    return tuple(labels.count(outcome) / n for outcome in OUTCOMES)


def skill_score(model_brier: float, reference_brier: float) -> float:
    """1 - model/reference. Positive: better than the base-rate reference.
    Zero: no better than knowing nothing but the base rate. Negative: worse
    than that trivial reference - a model this bad should not be trusted
    just because its raw Brier number looks small in isolation."""
    return 1.0 - model_brier / reference_brier


def paired_bootstrap_ci(diffs, n_boot: int = 10000, alpha: float = 0.05, seed: int = 0, chunk: int = 1000):
    """95% (or 1-alpha) percentile bootstrap CI for the mean of `diffs`.

    Resamples whole MATCHES with replacement - each match's paired
    Brier_uncertain - Brier_batch difference is one independent cluster, by
    construction (this module never scores a match twice within one
    horizon), so an ordinary paired bootstrap over these rows already is
    "clustered by match": there is nothing coarser to resample.

    Returns (mean, ci_low, ci_high).
    """
    diffs = np.asarray(diffs, dtype=float)
    n = diffs.size
    if n == 0:
        raise ValueError("cannot bootstrap an empty sample")

    rng = np.random.default_rng(seed)
    boot_means = []
    remaining = n_boot
    while remaining > 0:
        batch_size = min(chunk, remaining)
        idx = rng.integers(0, n, size=(batch_size, n))
        boot_means.append(diffs[idx].mean(axis=1))
        remaining -= batch_size
    boot_means = np.concatenate(boot_means)

    lo, hi = np.percentile(boot_means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(diffs.mean()), float(lo), float(hi)


# ---------------------------------------------------------------------------
# Real fixtures -> one row per played match. This is the ground truth every
# forecast in this module is scored against, and it never comes from a
# simulation.
# ---------------------------------------------------------------------------


def _local_date_and_round(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Attach each fixture's local (Brazil) match date and round number.

    Same tz shift season_dates.py uses (fixture_date is stored in UTC), and
    the same round-number parse tidy_fixtures.sql uses (`league_round` is
    "Regular Season - N"), duplicated here in Python rather than importing
    those private/SQL pieces because this operates on the raw fixtures.csv
    frame directly - one row per fixture, not tidy_fixtures.sql's
    home/away-doubled long form.
    """
    fixtures = fixtures.copy()
    kickoff = pd.to_datetime(fixtures["fixture_date"], utc=True)
    fixtures["local_date"] = (kickoff - pd.Timedelta(hours=BRAZIL_UTC_OFFSET_HOURS)).dt.strftime("%Y-%m-%d")
    fixtures["round_"] = fixtures["league_round"].str.split(" - ").str[-1].astype(int)
    return fixtures


def played_matches(season: int) -> pd.DataFrame:
    """One row per fixture that has actually been played (goals_home is not
    null), with the match's local date, round, the "Home x Away" key
    match_results is keyed by (see result_logger.py), and the realised
    outcome. A season in progress (2026) or one with a cancelled fixture
    (2016's round-38 Chapecoense x Atletico-MG) naturally yields fewer than
    380 rows via this same null filter - no special-casing needed.
    """
    fixtures = _local_date_and_round(SeasonData(season).fixtures)
    played = fixtures[fixtures["goals_home"].notnull()].copy()

    played["match_key"] = played["teams_home_name"] + " x " + played["teams_away_name"]
    played["outcome"] = np.select(
        [
            played["goals_home"] > played["goals_away"],
            played["goals_home"] < played["goals_away"],
        ],
        ["home", "away"],
        default="draw",
    )

    return played.rename(
        columns={"teams_home_name": "home_team", "teams_away_name": "away_team"}
    )[["match_key", "home_team", "away_team", "round_", "local_date", "outcome"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Horizon 0: the last backfill date strictly before each match was played.
# ---------------------------------------------------------------------------


def last_before(dates: list, target: str) -> Optional[str]:
    """The largest element of the ascending, sorted `dates` that is strictly
    less than `target`, or None if every element is >= target (there is no
    prior forecast to use - e.g. a match played on the season's very first
    backfill date)."""
    idx = bisect.bisect_left(dates, target)
    return dates[idx - 1] if idx > 0 else None


def assign_horizon0(played: pd.DataFrame, dates: list) -> pd.DataFrame:
    """Attach `as_of_date`: for each match, the last backfill date strictly
    before its local match date. Rows with no such date (the match was
    played on or before the season's first backfill date) are dropped - see
    last_before.
    """
    out = played.copy()
    out["as_of_date"] = out["local_date"].apply(lambda d: last_before(dates, d))
    dropped = int(out["as_of_date"].isna().sum())
    out = out.dropna(subset=["as_of_date"]).reset_index(drop=True)
    out.attrs["dropped_no_prior_forecast"] = dropped
    return out


# ---------------------------------------------------------------------------
# Reading one match's forecast probabilities out of one as-of-date pickle.
# ---------------------------------------------------------------------------


def match_forecast_probs(persistence: PickleAdapter, strategy: str, date: str, match_key: str) -> Optional[tuple]:
    """(p_home, p_draw, p_away) for `match_key` as forecast in the pickle
    dated `date`, or None if the pickle is missing, the match is absent from
    its match_results (e.g. a date this harness never generated), or its
    counts are all zero.

    match_results entries carry a 'round_' key alongside 'home'/'draw'/'away'
    counts (see result_logger.py) - excluded from the denominator here.
    """
    results = persistence.load_results(strategy=strategy, suffix=date)
    if results is None:
        return None
    counts = results.get("match_results", {}).get(match_key)
    if not counts:
        return None
    total = sum(v for k, v in counts.items() if k != "round_")
    if total <= 0:
        return None
    return tuple(counts.get(outcome, 0) / total for outcome in OUTCOMES)


# ---------------------------------------------------------------------------
# Generating the `uncertain` (or `batch`) side into a scratch directory.
# ---------------------------------------------------------------------------


def _simulator_for(name: str, strategy: str, season: int, rng: np.random.Generator):
    if name == "batch":
        return IterationBatchAdapter(strategy, season, rng=rng)
    if name == "uncertain":
        return UncertainParamsAdapter(strategy, season, rng=rng)
    raise ValueError(f"unsupported simulator {name!r}; choose 'batch' or 'uncertain'")


def generate_forecast_pickles(
    season: int,
    simulator_name: str,
    iterations: int,
    scratch_root: str = SCRATCH_ROOT,
    seed: int = 0,
    dates: Optional[list] = None,
) -> str:
    """Replay `season` date by date with `simulator_name`, writing one pickle
    per date under `{scratch_root}/{simulator_name}/{season}/` via
    PickleAdapter - never files/pkl/. Returns that variant directory.

    load_results=False throughout (nothing is ever resumed from a previous
    scratch run) and max_batch_size == iterations (one shot per date, no
    chunked re-writes) - the same choices collect_forecasts in
    calibration_backtest.py makes, for the same reasons.

    Each date draws from np.random.default_rng(seed + date_index): common
    random numbers, so two variants generated at the same seed see the
    identical stream at every date. Whether that actually cancels noise in a
    comparison depends on what the OTHER side of the pairing is - see
    score_horizon0's docstring for why that guarantee does not hold when
    pairing against pre-existing files/pkl/ pickles.
    """
    if dates is None:
        dates = backfill_dates(season)

    variant_dir = f"{scratch_root}/{simulator_name}"
    persistence = PickleAdapter(variant_dir, season)
    strategy = SimulationParams(season=season).strategy

    for date_index, date in enumerate(dates):
        rng = np.random.default_rng(seed + date_index)
        simulator = _simulator_for(simulator_name, strategy, season, rng)
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
# Putting it together: one row per scored match.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchBrierRun:
    """matches: one row per match that was successfully scored on BOTH
    simulators, columns:
        season, match_key, round_, local_date, as_of_date, outcome,
        p_home_batch, p_draw_batch, p_away_batch, brier_batch,
        p_home_uncertain, p_draw_uncertain, p_away_uncertain, brier_uncertain,
        brier_reference, diff (brier_uncertain - brier_batch).

    dropped: reason -> count, for every match excluded before scoring -
    "no_prior_forecast" (played on/before the season's first backfill date),
    "missing_batch_pickle" (that as-of date's batch pickle is not on disk,
    or that match is absent from its match_results), "missing_uncertain_pickle"
    likewise for the generated side. Reported rather than silently absorbed:
    a name-space drift between a match's fixtures.csv key and its
    match_results key would otherwise just shrink the sample and look like
    a smaller, unremarkable N instead of an obvious failure.
    """

    matches: pd.DataFrame
    dropped: dict = field(default_factory=dict)
    reference_probs: tuple = None


def score_horizon0(
    season: int,
    batch_root: str,
    uncertain_root: str,
    strategy: str = "average",
) -> MatchBrierRun:
    """Score every one of `season`'s played matches, once each, at the last
    forecast made before it was played (horizon 0), on both `batch_root` and
    `uncertain_root`.

    batch_root and uncertain_root need not have been generated by this
    module with common random numbers - batch_root is commonly
    files/pkl/{season}, real production history this module only reads.
    That means the paired difference this produces cancels match difficulty
    (both simulators score the SAME match) but NOT necessarily shared Monte
    Carlo noise, unless both sides happen to have been generated from the
    same seeded stream. Callers that generated both sides via
    generate_forecast_pickles at the same seed get that extra cancellation;
    callers reading real files/pkl/ history for one side do not, and should
    expect a wider confidence interval as a result.
    """
    dates = backfill_dates(season)
    played = assign_horizon0(played_matches(season), dates)
    dropped = {"no_prior_forecast": played.attrs.get("dropped_no_prior_forecast", 0)}

    batch_persistence = PickleAdapter(batch_root, season)
    uncertain_persistence = PickleAdapter(uncertain_root, season)

    reference_probs = base_rate_probs(played_matches(season)["outcome"])

    rows = []
    missing_batch = 0
    missing_uncertain = 0
    for row in played.itertuples(index=False):
        batch_probs = match_forecast_probs(batch_persistence, strategy, row.as_of_date, row.match_key)
        if batch_probs is None:
            missing_batch += 1
            continue
        uncertain_probs = match_forecast_probs(uncertain_persistence, strategy, row.as_of_date, row.match_key)
        if uncertain_probs is None:
            missing_uncertain += 1
            continue

        outcome_one_hot = one_hot_outcomes([row.outcome])
        brier_batch = float(match_brier([batch_probs], outcome_one_hot)[0])
        brier_uncertain = float(match_brier([uncertain_probs], outcome_one_hot)[0])
        brier_reference = float(match_brier([reference_probs], outcome_one_hot)[0])

        rows.append(
            {
                "season": season,
                "match_key": row.match_key,
                "round_": row.round_,
                "local_date": row.local_date,
                "as_of_date": row.as_of_date,
                "outcome": row.outcome,
                "p_home_batch": batch_probs[0],
                "p_draw_batch": batch_probs[1],
                "p_away_batch": batch_probs[2],
                "brier_batch": brier_batch,
                "p_home_uncertain": uncertain_probs[0],
                "p_draw_uncertain": uncertain_probs[1],
                "p_away_uncertain": uncertain_probs[2],
                "brier_uncertain": brier_uncertain,
                "brier_reference": brier_reference,
                "diff": brier_uncertain - brier_batch,
            }
        )

    dropped["missing_batch_pickle"] = missing_batch
    dropped["missing_uncertain_pickle"] = missing_uncertain

    return MatchBrierRun(matches=pd.DataFrame(rows), dropped=dropped, reference_probs=reference_probs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--batch-root",
        default="files/pkl",
        help="Directory holding batch's real, already-generated backfill pickles "
        "(files/pkl/{season}/...) - read only, never written to.",
    )
    parser.add_argument("--iterations", type=int, default=2000, help="uncertain generation only.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--scratch-root", default=SCRATCH_ROOT)
    parser.add_argument("--out", default=None, help="write MatchBrierRun.matches to this CSV path.")
    parser.add_argument(
        "--keep-scratch", action="store_true", help="keep the generated uncertain scratch pickles."
    )
    args = parser.parse_args()

    uncertain_dir = None
    try:
        uncertain_dir = generate_forecast_pickles(
            season=args.season,
            simulator_name="uncertain",
            iterations=args.iterations,
            scratch_root=args.scratch_root,
            seed=args.seed,
        )

        run = score_horizon0(
            season=args.season,
            batch_root=args.batch_root,
            uncertain_root=uncertain_dir,
        )
        matches = run.matches

        print(f"season: {args.season}  scored matches: {len(matches)}  dropped: {run.dropped}")
        print(f"reference (base rate) probs (home, draw, away): {run.reference_probs}")
        print()
        print(f"mean brier_batch:      {matches['brier_batch'].mean():.5f}")
        print(f"mean brier_uncertain:  {matches['brier_uncertain'].mean():.5f}")
        print(f"mean brier_reference:  {matches['brier_reference'].mean():.5f}")
        print()
        print(f"skill_batch:      {skill_score(matches['brier_batch'].mean(), matches['brier_reference'].mean()):.4f}")
        print(f"skill_uncertain:  {skill_score(matches['brier_uncertain'].mean(), matches['brier_reference'].mean()):.4f}")
        print()

        mean_diff, lo, hi = paired_bootstrap_ci(matches["diff"].to_numpy(), seed=args.seed)
        print(f"paired diff (uncertain - batch): {mean_diff:.5f}  95% CI [{lo:.5f}, {hi:.5f}]")

        if args.out:
            matches.to_csv(args.out, index=False)
            print(f"\nmatches written to {args.out}")
    finally:
        if not args.keep_scratch and uncertain_dir:
            shutil.rmtree(uncertain_dir, ignore_errors=True)

"""Sweep one SeasonBaseline knob (the team-parameter lookback window, or the
attack/defence blend weight) and measure match-outcome skill, using the
ANALYTIC match outcome route instead of a Monte Carlo simulation.

THE WASTE THIS MODULE REMOVES: the match-Brier harness (match_brier_backtest.py)
and the original lookback sweep scored each as-of date by running a full
5,000-iteration Monte Carlo over the WHOLE remaining season (up to 380
fixtures), writing it to a pickle, then reading back the ~10 matches whose
last-forecast-before falls on that date. ~97% of that computation was
discarded, and it was almost the entire cost of a multi-season sweep (tens
of minutes).

It is also unnecessary. Every simulator here already draws each fixture's
goals as independent Poissons with a fixed lambda pair computed BEFORE any
simulation (see domain/batch_simulation.py's build_baseline) - the match
outcome probabilities implied by one fixture's (lam_home, lam_away) are
therefore exact and closed-form (domain/match_outcome_probs.py), not a
quantity that needs sampling. analytic_forecasts_for_date computes that
closed form directly from build_baseline's own lambdas: same team_params
DuckDB query as before, same build_baseline call, but the season-long
simulate_batch Monte Carlo and the PickleAdapter round trip are both gone.

Per-date cost dropped from ~0.66s (5,000 iterations) to the cost of the
team_params query and build_baseline alone - microseconds of Poisson math on
top of a query that ran either way. See the module's own timing note in the
task report for the measured number.

WHAT THIS DOES NOT TOUCH: batch_poisson_adapter.py, IterationBatchAdapter,
PickleAdapter, and every real production backfill (backfill.py,
current_probabilities.py) are untouched - they still run the season-level
Monte Carlo, because title and relegation probabilities are aggregates over
a whole simulated SEASON that a single fixture's closed-form outcome cannot
give you. This module is an additional, faster route used only by sweeps
that only ever need MATCH-level (not season-level) forecasts.

THE CONFOUND THIS MODULE MUST NOT REINTRODUCE (inherited from
lookback_sweep.py): the lookback window and the shrinkage-blend denominator
share one $lookback substitution in team_params_same_venue_average.sql, and
domain/batch_simulation.py's adjustment_weight is completely independent of
it - varying one holds the other at its default, never both at once, unless
a caller explicitly asks for that.

This measures; it does not retune. Do not change any default based on the
result.
"""

import argparse

import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.domain.batch_simulation import (
    ADJUSTMENT_WEIGHT,
    FULL_WINDOW_MATCHES,
    build_baseline,
)
from brasileirao_simulator.domain.match_outcome_probs import match_outcome_probs
from brasileirao_simulator.domain.queries import (
    DEFAULT_PRIOR_WEIGHT,
    DEFAULT_WEIGHT_BASE,
    DEFAULT_WEIGHT_MID,
    DEFAULT_WEIGHT_RECENT,
    Queries,
)
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.match_brier_backtest import (
    assign_horizon0,
    base_rate_probs,
    match_brier,
    one_hot_outcomes,
    paired_bootstrap_ci,
    played_matches,
    skill_score,
)


DEFAULT_SEASONS = tuple(range(2016, 2026))  # 2016-2025; 2015 is prior-season-only data, 2026 is mid-season.
DEFAULT_WINDOWS = (8, 12, FULL_WINDOW_MATCHES, 26)
DEFAULT_WEIGHTS = (0.3, 0.4, 0.5, 0.6, 0.7)

# Recency-weight profiles: (weight_recent, weight_mid, weight_base) applied to
# the most recent match / next four / remaining window in
# team_params_same_venue_average.sql. "current" (4, 3, 1) is today's default -
# 53% of the estimate on the last 5 matches. The others span flat (no
# recency preference) to steeper than today, in roughly equal last-5-share
# steps, to see whether today's specific ratio is doing real work or an
# arbitrary point on a flat response surface.
DEFAULT_RECENCY_PROFILES = {
    "flat": (1, 1, 1),
    "mild": (2, 2, 1),
    "current": (DEFAULT_WEIGHT_RECENT, DEFAULT_WEIGHT_MID, DEFAULT_WEIGHT_BASE),
    "steep": (8, 4, 1),
    "very_steep": (16, 6, 1),
}
DEFAULT_RECENCY_WEIGHTS = DEFAULT_RECENCY_PROFILES["current"]

# prior_weight multiplier values for the newcomer-prior strength sweep: 0
# ignores the prior entirely, 1 is today's behaviour, 2 pulls twice as hard.
DEFAULT_PRIOR_WEIGHTS = (0.0, 0.5, 1.0, 2.0)


# ---------------------------------------------------------------------------
# One as-of date, forecast analytically - no simulation, no pickle.
# ---------------------------------------------------------------------------


def analytic_forecasts_for_date(
    season: int,
    as_of_date: str,
    lookback: int = FULL_WINDOW_MATCHES,
    adjustment_weight: float = ADJUSTMENT_WEIGHT,
    weights: tuple = DEFAULT_RECENCY_WEIGHTS,
    prior_weight: float = DEFAULT_PRIOR_WEIGHT,
    tables: Tables = None,
) -> dict:
    """match_key -> (p_home, p_draw, p_away) for every fixture remaining as
    of `as_of_date`, computed from the closed-form Poisson outcome
    probabilities implied by build_baseline's own lambdas.

    Recomputes fixtures/remaining_games/team_params from scratch, exactly as
    generate_forecast_pickles / generate_lookback_forecasts did per date -
    that overhead is real and shared with the Monte Carlo route this
    replaces, so timing the two against each other measures only the part
    that changed (season simulation + pickle I/O, now gone).

    `weights` is (weight_recent, weight_mid, weight_base) - the per-match
    recency weights team_params_same_venue_average.sql applies inside the
    lookback window; `prior_weight` scales how hard that query's newcomer
    backfill prior pulls a thin-window team's average. Both default to
    today's hardcoded values (see domain/queries.py), so a caller that never
    passes them is unaffected - same guarantee `lookback` and
    `adjustment_weight` already give.

    `tables` lets a caller sweeping many dates of the same season (e.g.
    score_variant_matches) build Tables(SeasonData(season)) once and reuse
    it - SeasonData re-reads fixtures.csv from disk on every construction,
    which is pure waste when nothing about the season's raw data changes
    between as-of dates. Standalone callers (tests, one-off checks) can
    leave it at the default and pay that cost once.
    """
    tables = tables if tables is not None else Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    remaining_games = tables.remaining_games(blank_from_date=as_of_date)

    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    queries = Queries(
        season,
        lookback=lookback,
        weight_recent=weights[0],
        weight_mid=weights[1],
        weight_base=weights[2],
        prior_weight=prior_weight,
    )
    team_params = con.sql(queries.team_params_same_venue_average()).df()

    baseline = build_baseline(
        fixtures, remaining_games, team_params, season, adjustment_weight=adjustment_weight
    )
    if len(baseline.lam_home) == 0:
        return {}

    probs = match_outcome_probs(baseline.lam_home, baseline.lam_away)

    return {
        f"{home} x {away}": (float(p[0]), float(p[1]), float(p[2]))
        for home, away, p in zip(baseline.home_name, baseline.away_name, probs)
    }


def partial_window_team_venues(season: int, dates: list) -> set:
    """(team_name, venue) pairs with fewer than FULL_WINDOW_MATCHES real
    matches behind their team_params on at least one of `dates` - the only
    population the prior_weight sweep can move, since a full-window team's
    blend denominator collapses to its own data_points regardless of
    prior_weight (see team_params_same_venue_average.sql's $prior_weight
    comment: the guard/scaling only has any effect when
    r.data_points != t.data_points).

    Uses team_match_counts.sql - team_params_same_venue_average.sql's own
    data_points column is post-blend (`greatest(t.data_points, r.data_points)`,
    always >= lookback) and useless for telling a thin window from a full one;
    team_match_counts.sql is its twin, reporting the same real per-venue count
    pre-blend. That query's window is hardcoded at 19 (not $lookback-driven),
    so this only characterises exposure at the default lookback - the same
    default every other sweep in this module holds constant unless told
    otherwise.
    """
    tables = Tables(SeasonData(season))
    affected = set()
    for date in dates:
        fixtures = tables.enriched_tidy_fixtures(blank_from_date=date)
        con = duckdb.connect()
        con.register("new_fixtures", fixtures)
        counts = con.sql(Queries(season).team_match_counts()).df()
        thin = counts[counts["match_count"] < FULL_WINDOW_MATCHES]
        affected.update(zip(thin["team_name"], thin["venue"]))
    return affected


# ---------------------------------------------------------------------------
# Scoring one (lookback, adjustment_weight) variant at horizon 0.
# ---------------------------------------------------------------------------


def score_variant_matches(
    season: int,
    dates: list,
    lookback: int = FULL_WINDOW_MATCHES,
    adjustment_weight: float = ADJUSTMENT_WEIGHT,
    weights: tuple = DEFAULT_RECENCY_WEIGHTS,
    prior_weight: float = DEFAULT_PRIOR_WEIGHT,
) -> pd.DataFrame:
    """One row per horizon-0 match: match_key, local_date, as_of_date,
    outcome, brier, reference_brier - the same shape
    lookback_sweep.score_lookback_window_matches produced from pickles, now
    computed directly from analytic_forecasts_for_date.

    Every as-of date's forecast dict is computed exactly once and reused for
    every match whose last-forecast-before falls on it (see assign_horizon0)
    - one team_params query and one build_baseline call per date, not per
    match.

    df.attrs["missing"] carries the count of played matches whose as-of-date
    forecast did not include them (e.g. a team absent from team_params) -
    reported, never silently dropped into a smaller N.
    """
    played = assign_horizon0(played_matches(season), dates)
    reference_probs = base_rate_probs(played_matches(season)["outcome"])

    tables = Tables(SeasonData(season))
    forecasts_by_date = {
        date: analytic_forecasts_for_date(
            season, date, lookback, adjustment_weight, weights, prior_weight, tables=tables
        )
        for date in dates
    }

    rows = []
    missing = 0
    for row in played.itertuples(index=False):
        probs = forecasts_by_date.get(row.as_of_date, {}).get(row.match_key)
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


# ---------------------------------------------------------------------------
# Multi-season sweep: pair every challenger variant against a baseline
# value, per season and pooled. Generic over which knob varies (lookback or
# adjustment_weight) so both sweeps share one implementation.
# ---------------------------------------------------------------------------


def _paired_diff(challenger: pd.DataFrame, baseline: pd.DataFrame, ci_seed: int) -> tuple:
    """(mean_diff, ci_low, ci_high) for challenger_brier - baseline_brier,
    matched row-for-row on match_key (per season, match_key alone is unique).
    Negative mean_diff: challenger beats baseline on average.
    """
    aligned = challenger.set_index("match_key")["brier"].align(
        baseline.set_index("match_key")["brier"], join="inner"
    )
    if len(aligned[0]) != len(challenger) or len(aligned[0]) != len(baseline):
        raise ValueError(
            "challenger and baseline match sets differ - expected identical "
            "match_key sets within one season/variant pair"
        )
    diffs = (aligned[0] - aligned[1]).to_numpy()
    return paired_bootstrap_ci(diffs, seed=ci_seed)


def run_multi_season_sweep(
    seasons: list,
    param_name: str,
    values: list,
    baseline_value,
    lookback: int = FULL_WINDOW_MATCHES,
    adjustment_weight: float = ADJUSTMENT_WEIGHT,
    weights: tuple = DEFAULT_RECENCY_WEIGHTS,
    prior_weight: float = DEFAULT_PRIOR_WEIGHT,
    ci_seed: int = 0,
) -> tuple:
    """Run a sweep of `values` for whichever knob `param_name` names
    ("lookback", "adjustment_weight", "weights" or "prior_weight") across
    `seasons`, holding every other knob at its given default, and pair every
    challenger value against `baseline_value` within each season.

    "weights" values are (weight_recent, weight_mid, weight_base) tuples
    (see DEFAULT_RECENCY_PROFILES) rather than a scalar - everything else
    about the pairing is identical to the scalar knobs.

    Returns (per_season, pooled, matches_by_season) - see
    lookback_sweep.run_multi_season_sweep's docstring for the exact shape;
    this is that same design generalised over which knob varies.

    per_season columns: season, <param_name>, n_scored, missing, mean_brier,
    mean_reference_brier, skill, diff_vs_baseline, diff_ci_low,
    diff_ci_high, beats_baseline (None for the baseline row itself).

    pooled columns: <param_name>, n_scored, mean_brier, mean_reference_brier,
    skill, mean_skill_across_seasons, diff_vs_baseline, diff_ci_low,
    diff_ci_high. Aggregation is match-weighted (every scored match from
    every season concatenated into one flat sample) - see
    lookback_sweep.run_multi_season_sweep's docstring for why that agrees
    almost exactly with season-weighting here.
    """
    if param_name not in ("lookback", "adjustment_weight", "weights", "prior_weight"):
        raise ValueError(
            "param_name must be 'lookback', 'adjustment_weight', 'weights' or "
            f"'prior_weight', got {param_name!r}"
        )

    per_season_rows = []
    matches_by_season = {}

    for season in seasons:
        dates = backfill_dates(season)
        matches_by_value = {}
        for value in values:
            kwargs = {
                "lookback": lookback,
                "adjustment_weight": adjustment_weight,
                "weights": weights,
                "prior_weight": prior_weight,
            }
            kwargs[param_name] = value
            matches_by_value[value] = score_variant_matches(season, dates, **kwargs)
        matches_by_season[season] = matches_by_value

        baseline_matches = matches_by_value[baseline_value]
        baseline_mean_brier = float(baseline_matches["brier"].mean())

        for value in values:
            matches = matches_by_value[value]
            mean_brier = float(matches["brier"].mean())
            mean_reference_brier = float(matches["reference_brier"].mean())
            row = {
                "season": season,
                param_name: value,
                "n_scored": len(matches),
                "missing": matches.attrs["missing"],
                "mean_brier": mean_brier,
                "mean_reference_brier": mean_reference_brier,
                "skill": skill_score(mean_brier, mean_reference_brier),
            }
            if value == baseline_value:
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
        [matches_by_season[s][baseline_value].assign(season=s) for s in seasons], ignore_index=True
    )
    for value in values:
        pool = pd.concat(
            [matches_by_season[s][value].assign(season=s) for s in seasons], ignore_index=True
        )
        mean_brier = float(pool["brier"].mean())
        mean_reference_brier = float(pool["reference_brier"].mean())
        row = {
            param_name: value,
            "n_scored": len(pool),
            "mean_brier": mean_brier,
            "mean_reference_brier": mean_reference_brier,
            "skill": skill_score(mean_brier, mean_reference_brier),
            "mean_skill_across_seasons": float(
                per_season.loc[per_season[param_name] == value, "skill"].mean()
            ),
        }
        if value == baseline_value:
            row.update({"diff_vs_baseline": 0.0, "diff_ci_low": 0.0, "diff_ci_high": 0.0})
        else:
            pool_key = pool.set_index(["season", "match_key"])["brier"]
            baseline_key = baseline_pool.set_index(["season", "match_key"])["brier"]
            aligned = pool_key.align(baseline_key, join="inner")
            if len(aligned[0]) != len(pool) or len(aligned[0]) != len(baseline_pool):
                raise ValueError("pooled challenger and baseline match sets differ across seasons")
            diffs = (aligned[0] - aligned[1]).to_numpy()
            mean_diff, lo, hi = paired_bootstrap_ci(diffs, seed=ci_seed)
            row.update({"diff_vs_baseline": mean_diff, "diff_ci_low": lo, "diff_ci_high": hi})
        pooled_rows.append(row)

    pooled = pd.DataFrame(pooled_rows)

    return per_season, pooled, matches_by_season


def _print_and_maybe_write(per_season, pooled, param_name, values, baseline_value, out, pooled_out):
    print("--- per season ---")
    print(
        per_season[
            [
                "season",
                param_name,
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
                param_name,
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

    for value in values:
        if value == baseline_value:
            continue
        rows = per_season[per_season[param_name] == value]
        wins = int(rows["beats_baseline"].sum())
        print()
        print(f"{param_name}={value} beats {param_name}={baseline_value} in {wins}/{len(rows)} seasons (point estimate, mean Brier).")

    if out:
        per_season.to_csv(out, index=False)
        print(f"\nper-season table written to {out}")
    if pooled_out:
        pooled.to_csv(pooled_out, index=False)
        print(f"pooled table written to {pooled_out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--param",
        choices=["lookback", "adjustment_weight", "weights", "prior_weight"],
        required=True,
        help="which knob to sweep.",
    )
    parser.add_argument(
        "--seasons",
        default=",".join(str(s) for s in DEFAULT_SEASONS),
        help="comma-separated seasons, e.g. 2016,2017,...,2025.",
    )
    parser.add_argument(
        "--values",
        default=None,
        help="comma-separated values for --param: ints for lookback, floats for "
        "adjustment_weight or prior_weight, and for weights either profile names "
        "(flat,mild,current,steep,very_steep - see DEFAULT_RECENCY_PROFILES) or "
        "explicit recent-mid-base triples (e.g. 8-4-1). Defaults to the standard "
        "sweep for whichever --param is chosen.",
    )
    parser.add_argument("--baseline", default=None, help="the value every other value is paired against.")
    parser.add_argument(
        "--lookback",
        type=int,
        default=FULL_WINDOW_MATCHES,
        help="lookback window held fixed when --param is not lookback.",
    )
    parser.add_argument(
        "--adjustment-weight",
        type=float,
        default=ADJUSTMENT_WEIGHT,
        help="attack/defence blend weight held fixed when --param is not adjustment_weight.",
    )
    parser.add_argument(
        "--prior-weight",
        type=float,
        default=DEFAULT_PRIOR_WEIGHT,
        help="newcomer-prior strength held fixed when --param is not prior_weight.",
    )
    parser.add_argument("--ci-seed", type=int, default=0)
    parser.add_argument("--out", default=None, help="write the per-season table to this CSV path.")
    parser.add_argument("--pooled-out", default=None, help="write the pooled table to this CSV path.")
    args = parser.parse_args()

    seasons = [int(s) for s in args.seasons.split(",")]

    def _parse_weights_token(token: str) -> tuple:
        if token in DEFAULT_RECENCY_PROFILES:
            return DEFAULT_RECENCY_PROFILES[token]
        recent, mid, base = (int(p) for p in token.split("-"))
        return (recent, mid, base)

    if args.param == "lookback":
        values = [int(v) for v in args.values.split(",")] if args.values else list(DEFAULT_WINDOWS)
        baseline_value = int(args.baseline) if args.baseline is not None else FULL_WINDOW_MATCHES
    elif args.param == "adjustment_weight":
        values = [float(v) for v in args.values.split(",")] if args.values else list(DEFAULT_WEIGHTS)
        baseline_value = float(args.baseline) if args.baseline is not None else ADJUSTMENT_WEIGHT
    elif args.param == "prior_weight":
        values = [float(v) for v in args.values.split(",")] if args.values else list(DEFAULT_PRIOR_WEIGHTS)
        baseline_value = float(args.baseline) if args.baseline is not None else DEFAULT_PRIOR_WEIGHT
    else:  # weights
        values = (
            [_parse_weights_token(v) for v in args.values.split(",")]
            if args.values
            else list(DEFAULT_RECENCY_PROFILES.values())
        )
        baseline_value = _parse_weights_token(args.baseline) if args.baseline is not None else DEFAULT_RECENCY_WEIGHTS

    per_season, pooled, _ = run_multi_season_sweep(
        seasons=seasons,
        param_name=args.param,
        values=values,
        baseline_value=baseline_value,
        lookback=args.lookback,
        adjustment_weight=args.adjustment_weight,
        prior_weight=args.prior_weight,
        ci_seed=args.ci_seed,
    )

    print(f"seasons: {seasons}  param: {args.param}  values: {values}  baseline: {baseline_value}")
    print()
    _print_and_maybe_write(per_season, pooled, args.param, values, baseline_value, args.out, args.pooled_out)

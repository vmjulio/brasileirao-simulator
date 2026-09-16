"""Score Dixon-Coles against the shipped model on real matches.

WHAT IS BEING COMPARED. Both arms forecast the same match at the same moment
with the same information, and differ only in how a fixture's two expected
goals are estimated:

  current      lambda = 0.5 * (attacker's own goals-for average at this venue)
               + 0.5 * (defender's goals-against average at this venue), from
               files/queries/team_params_same_venue_average.sql - two marginal
               averages, each confounding club quality with the opponents that
               club happened to face.
  dixon_coles  lambda = attack[i] * defence[j] * home_advantage, from ratings
               fitted jointly across every club, plus Dixon and Coles' rho
               correction to the four low scorelines.

NO MONTE CARLO ON EITHER SIDE. Match outcome probabilities are exact in both
models - domain/match_outcome_probs.py for the current one (already used by
variant_sweep.analytic_forecasts_for_date) and dixon_coles.outcome_probs for
the challenger. A comparison that may turn on 0.002 RPS must not have
sampling noise added to it on purpose.

HORIZON 0 ONLY. Every match is scored exactly once, at the last backfill date
strictly before it was played (assign_horizon0). Pooling a match's forecasts
across the ~100 dates it was forecast at would let the matches that stay
uncertain longest dominate the sample - see match_brier_backtest.py's module
docstring for the bias that caused.

xi AND rho ARE NOT TUNED HERE. xi is Dixon and Coles' published decay; rho is
fitted per date by maximum likelihood on the data visible at that date, which
is estimation, not tuning against the scored outcomes. Sweeping xi is
follow-up work; doing it against these same 2025 matches would be fitting the
test set.

This measures. It changes no default.

ARM B - THE DATA-VS-LEAGUE BACKTEST (data-vs-league-backtest, E3/T3.2). Arm A
above answers "does Dixon-Coles beat the incumbent" (measured: no, 3-10). Arm
B answers a different question: "does feeding Dixon-Coles every admitted
competition, instead of the league alone, beat feeding it the league only, on
IDENTICAL matches." `dixon_coles_all_forecasts_for_date` mirrors
`dixon_coles_forecasts_for_date` above but goes through
`DixonColesAllAdapter` (adapters/dixon_coles_all_adapter.py) instead of
`DixonColesAdapter`, fitting from `MatchStore` rather than
`enriched_tidy_fixtures`. `score_data_vs_league` builds on `score_season`
UNCHANGED - arm A's numbers here are exactly `score_season`'s, so they are
provably the ones already committed to
files/exports/dixon_coles_multiseason.csv - and adds arm B on the same match
set, dropping (and counting) anything arm B has no forecast for.

ARM B DOES NOT CONVERGE AT THE DOMAIN DEFAULT. Measured on 2025-08-01: the
207-club fit reports `converged=False` at both 200 and 5,000 iterations; the
max relative change in any single club's attack/defence rating between 200
and 2,000 iterations is the thing this module reports per date, restricted to
that season's Série A clubs - see `_max_relative_drift`. `dixon_coles.py` is
not touched to "fix" this; iterations are a `fit` parameter already, not a
domain change.

ARM C - THE FOUR-ARM BACKTEST (four-arm-backtest, E5). Arms A and B both
estimate their two lambdas from a Dixon-Coles fit (league-only, then
all-competitions); arm C answers a third question: "does an Elo-based
lambda estimate - the same all-competitions data B uses, but Elo's
win/loss/margin replay in place of a joint Poisson fit - beat B, beat A, and
beat the incumbent, on IDENTICAL matches." `elo_forecasts_for_date` builds
the two lambdas from `EloAdapter.build_baseline` (adapters/elo_adapter.py -
the Elo replay -> `DifferenceMap` -> total-goals decomposition documented in
`domain/elo_lambda.py`), then turns them into outcome probabilities with
`domain/match_outcome_probs.py`'s closed-form independent-Poisson formula -
the SAME function the incumbent's own forecasts use (`analytic_forecasts_
for_date` in variant_sweep.py) - and NOT `dixon_coles.outcome_probs`, because
Elo's lambdas carry no Dixon-Coles rho and there is no low-score correction
to apply: rho is fixed at 0 by construction, not estimated and set to zero.
`score_four_arms` layers arm C onto the exact match set `score_data_vs_
league` already scored A, B and current on, the same way B was layered onto
A - any match arm C has no forecast for is dropped from all four arms and
counted, never scored on three arms only.
"""

import argparse
import os
import time

import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.dixon_coles_adapter import DixonColesAdapter
from brasileirao_simulator.adapters.dixon_coles_all_adapter import (
    DixonColesAllAdapter,
    _all_competitions_rows,
    _id_by_canonical_name,
    _team_id_key,
)
from brasileirao_simulator.adapters.elo_adapter import EloAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH
from brasileirao_simulator.domain import dixon_coles
from brasileirao_simulator.domain.elo_lambda import EloLambdaParams
from brasileirao_simulator.domain.match_outcome_probs import match_outcome_probs
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.benchmark_chancedegol import brier, log_loss, rps
from brasileirao_simulator.entrypoints.match_brier_backtest import (
    OUTCOMES,
    paired_bootstrap_ci,
)
from brasileirao_simulator.entrypoints.variant_sweep import (
    analytic_forecasts_for_date,
    assign_horizon0,
    base_rate_probs,
    played_matches,
)

# Arm B's refit iteration cap - see the module docstring's ARM B DOES NOT
# CONVERGE section. 200 is the domain default `fit` already uses; the
# convergence-drift comparison is always between these two.
DRIFT_BASE_ITERATIONS = 200
DEFAULT_MAX_ITERATIONS = 2000

# Seasons the decision gate is scored on. 2019 is burn-in (the first season
# every admitted competition overlaps - see MatchStore/domain/competitions.py);
# 2026 is scored but partial and excluded from the pooled figure.
DATA_VS_LEAGUE_SEASONS = (2020, 2021, 2022, 2023, 2024, 2025)
PARTIAL_SEASONS = (2026,)


ONE_HOT = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}


def dixon_coles_forecasts_for_date(season: int, as_of_date: str, tables: Tables, xi: float):
    """(match_key -> (p_home, p_draw, p_away), Ratings) for every fixture
    remaining as of `as_of_date`.

    Goes through DixonColesAdapter rather than calling domain.dixon_coles
    directly, so the probabilities scored here are the ones the registered
    simulator would actually produce - with the single exception rho makes to
    a season-level Monte Carlo, documented in that adapter.
    """
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    remaining = tables.remaining_games(blank_from_date=as_of_date)

    adapter = DixonColesAdapter("average", season, xi=xi)
    baseline = adapter.build_baseline(fixtures, remaining)
    ratings = adapter.ratings

    forecasts = {
        f"{home} x {away}": dixon_coles.outcome_probs(float(lam_h), float(lam_a), ratings.rho)
        for home, away, lam_h, lam_a in zip(
            baseline.home_name, baseline.away_name, baseline.lam_home, baseline.lam_away
        )
    }
    return forecasts, ratings


def dixon_coles_all_forecasts_for_date(
    season: int,
    as_of_date: str,
    tables: Tables,
    match_store: MatchStore,
    xi: float,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
):
    """Arm B's (match_key -> (p_home, p_draw, p_away), Ratings at
    `max_iterations`, Ratings at `DRIFT_BASE_ITERATIONS`, that season's
    Série A team ids) for every fixture remaining as of `as_of_date`.

    Mirrors `dixon_coles_forecasts_for_date` above, but through
    `DixonColesAllAdapter` - fit from `MatchStore`, not
    `enriched_tidy_fixtures` - and TWO fits, not one.

    WHY TWO FITS. `DixonColesAllAdapter.build_baseline` fits at the domain
    default (200 iterations, `DRIFT_BASE_ITERATIONS`) as a side effect of
    producing the fixture identity (`home_name`/`away_name`) it needs - that
    fit is kept as `ratings_200` rather than discarded, because it is exactly
    one half of the convergence-drift comparison the module docstring
    requires. The forecast actually scored is then refit at `max_iterations`
    on the IDENTICAL frontier date and rows `DixonColesAllAdapter.fit_ratings`
    used for the first fit - `_all_competitions_rows` is reused verbatim, the
    frontier/cutoff computation is `fit_ratings`'s own (duplicated here
    because `fit_ratings` does not expose an iterations argument, and this
    ticket forbids adding one to the adapter).
    """
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    remaining = tables.remaining_games(blank_from_date=as_of_date)

    adapter = DixonColesAllAdapter("average", season, xi=xi, match_store=match_store)
    baseline = adapter.build_baseline(fixtures, remaining)
    ratings_200 = adapter.ratings

    played = fixtures[fixtures["goals_for"].notnull()]
    frontier_date = str(pd.to_datetime(played["fixture_date"], format="mixed").max().date())
    cutoff = str((pd.Timestamp(frontier_date) + pd.Timedelta(days=1)).date())
    rows = _all_competitions_rows(match_store.before(cutoff))
    ratings = dixon_coles.fit(rows, frontier_date, xi=xi, max_iterations=max_iterations)

    id_by_name = _id_by_canonical_name(fixtures)
    pairs = [
        dixon_coles.lambdas(ratings, _team_id_key(id_by_name, home), _team_id_key(id_by_name, away))
        for home, away in zip(baseline.home_name, baseline.away_name)
    ]
    forecasts = {
        f"{home} x {away}": dixon_coles.outcome_probs(float(lam_h), float(lam_a), ratings.rho)
        for (home, away), (lam_h, lam_a) in zip(zip(baseline.home_name, baseline.away_name), pairs)
    }
    serie_a_ids = set(id_by_name.values())
    return forecasts, ratings, ratings_200, serie_a_ids


def _max_relative_drift(base: dixon_coles.Ratings, refit: dixon_coles.Ratings, team_ids: set) -> dict:
    """Max |refit - base| / |base| across `team_ids`, separately for attack
    and defence, restricted to ids present in BOTH fits' rating dicts (a club
    with zero weighted matches at this as-of date is in neither, and
    contributes nothing to either fit - there is no drift to measure for it).

    `base` is expected to be the `DRIFT_BASE_ITERATIONS`-iteration fit,
    `refit` the `max_iterations` one - see the module docstring's ARM B DOES
    NOT CONVERGE section for why this comparison exists and what a value
    above 1e-3 would mean.
    """

    def max_relative(field: str) -> float:
        base_values = getattr(base, field)
        refit_values = getattr(refit, field)
        deltas = [
            abs(refit_values[key] - base_values[key]) / max(abs(base_values[key]), dixon_coles.MIN_RATING)
            for key in (str(team_id) for team_id in team_ids)
            if key in base_values and key in refit_values
        ]
        return max(deltas) if deltas else 0.0

    return {"attack_drift": max_relative("attack"), "defence_drift": max_relative("defence")}


def score_season(season: int, xi: float = dixon_coles.DEFAULT_XI, seed: int = 7):
    """One row per match scored on BOTH arms, plus the summary both arms and
    the base-rate reference are reported from.

    A match missing from either arm's forecast is dropped from BOTH and
    counted - never scored on one arm only, which would compare two models on
    two different match sets.
    """
    season_data = SeasonData(season)
    # backfill_dates, not season_data.dates: the same date list every other
    # scorer in this project uses (variant_sweep, match_brier_backtest), so
    # the ~374 horizon-0 matches here are the same ones their numbers refer to.
    dates = backfill_dates(season)
    tables = Tables(season_data)

    played = assign_horizon0(played_matches(season), dates)
    reference = base_rate_probs(played_matches(season)["outcome"])

    current_by_date = {}
    dixon_by_date = {}
    ratings_by_date = {}
    for date in dates:
        current_by_date[date] = analytic_forecasts_for_date(season, date, tables=tables)
        dixon_by_date[date], ratings_by_date[date] = dixon_coles_forecasts_for_date(
            season, date, tables, xi
        )

    rows = []
    dropped = {"missing_current": 0, "missing_dixon_coles": 0}
    for row in played.itertuples(index=False):
        current = current_by_date.get(row.as_of_date, {}).get(row.match_key)
        dixon = dixon_by_date.get(row.as_of_date, {}).get(row.match_key)
        if current is None:
            dropped["missing_current"] += 1
            continue
        if dixon is None:
            dropped["missing_dixon_coles"] += 1
            continue
        rows.append(
            {
                "season": season,
                "match_key": row.match_key,
                "local_date": row.local_date,
                "as_of_date": row.as_of_date,
                "outcome": row.outcome,
                **{f"p_{o}_current": p for o, p in zip(OUTCOMES, current)},
                **{f"p_{o}_dixon_coles": p for o, p in zip(OUTCOMES, dixon)},
            }
        )
    dropped["no_prior_forecast"] = played.attrs.get("dropped_no_prior_forecast", 0)

    matches = pd.DataFrame(rows)
    outcomes = np.array([ONE_HOT[o] for o in matches["outcome"]], dtype=float)
    arms = {
        "dixon_coles": matches[[f"p_{o}_dixon_coles" for o in OUTCOMES]].to_numpy(),
        "current": matches[[f"p_{o}_current" for o in OUTCOMES]].to_numpy(),
        "base_rate": np.tile(np.array(reference, dtype=float), (len(matches), 1)),
    }

    for name, probabilities in arms.items():
        matches[f"brier_{name}"] = brier(probabilities, outcomes)
        matches[f"rps_{name}"] = rps(probabilities, outcomes)
        matches[f"log_loss_{name}"] = log_loss(probabilities, outcomes)

    paired = matches["rps_dixon_coles"].to_numpy() - matches["rps_current"].to_numpy()
    mean_diff, ci_low, ci_high = paired_bootstrap_ci(paired, seed=seed)

    summary = {
        "season": season,
        "xi": xi,
        "matches": len(matches),
        "dropped": dropped,
        "observed_rates": {
            outcome: float((matches["outcome"] == outcome).mean()) for outcome in OUTCOMES
        },
        "reference_probs": reference,
        "models": {
            name: {
                "brier": float(matches[f"brier_{name}"].mean()),
                "rps": float(matches[f"rps_{name}"].mean()),
                "log_loss": float(matches[f"log_loss_{name}"].mean()),
                "mean_p_draw": float(probabilities[:, 1].mean()),
            }
            for name, probabilities in arms.items()
        },
        "paired_rps_diff": float(mean_diff),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "dixon_coles_wins": int((paired < 0).sum()),
        "final_ratings": ratings_by_date[dates[-1]],
    }
    return matches, summary


def score_data_vs_league(
    season: int,
    xi: float = dixon_coles.DEFAULT_XI,
    seed: int = 7,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    match_store: MatchStore = None,
):
    """Arms A (league only) and B (all competitions), plus the incumbent, on
    IDENTICAL matches at horizon 0. The data-vs-league-backtest ticket's
    scoring function - see the module docstring's ARM B section.

    Arm A's per-match forecasts and its own vs-current comparison come
    straight from `score_season`, UNCHANGED - so arm A's numbers here are
    provably `score_season`'s numbers, not a parallel reimplementation that
    could silently drift from them. Arm B is layered on the exact match set
    `score_season` already scored A and current on: every (as_of_date,
    match_key) pair is looked up in arm B's per-date forecasts, and any miss
    is dropped from the combined table and counted, never scored on two arms
    only.

    Returns (matches, summary). `summary["drift"]` is a per-as-of-date frame
    of arm B's convergence drift (see `_max_relative_drift`), restricted to
    that season's Série A clubs.
    """
    matches_a, summary_a = score_season(season, xi=xi, seed=seed)

    dates = backfill_dates(season)
    tables = Tables(SeasonData(season))
    store = match_store if match_store is not None else MatchStore()

    dixon_all_by_date = {}
    ratings_by_date = {}
    ratings_200_by_date = {}
    serie_a_ids_by_date = {}
    for date in dates:
        forecasts, ratings, ratings_200, serie_a_ids = dixon_coles_all_forecasts_for_date(
            season, date, tables, store, xi, max_iterations
        )
        dixon_all_by_date[date] = forecasts
        ratings_by_date[date] = ratings
        ratings_200_by_date[date] = ratings_200
        serie_a_ids_by_date[date] = serie_a_ids

    drift = pd.DataFrame(
        [
            {
                "season": season,
                "as_of_date": date,
                **_max_relative_drift(
                    ratings_200_by_date[date], ratings_by_date[date], serie_a_ids_by_date[date]
                ),
            }
            for date in dates
        ]
    )

    rows = []
    dropped_dixon_coles_all = 0
    for row in matches_a.itertuples(index=False):
        forecast = dixon_all_by_date.get(row.as_of_date, {}).get(row.match_key)
        if forecast is None:
            dropped_dixon_coles_all += 1
            continue
        record = row._asdict()
        record.update({f"p_{o}_dixon_coles_all": p for o, p in zip(OUTCOMES, forecast)})
        rows.append(record)

    matches = pd.DataFrame(rows)
    outcomes = np.array([ONE_HOT[o] for o in matches["outcome"]], dtype=float)
    arms = {
        "dixon_coles": matches[[f"p_{o}_dixon_coles" for o in OUTCOMES]].to_numpy(),
        "dixon_coles_all": matches[[f"p_{o}_dixon_coles_all" for o in OUTCOMES]].to_numpy(),
        "current": matches[[f"p_{o}_current" for o in OUTCOMES]].to_numpy(),
    }
    for name, probabilities in arms.items():
        matches[f"brier_{name}"] = brier(probabilities, outcomes)
        matches[f"rps_{name}"] = rps(probabilities, outcomes)
        matches[f"log_loss_{name}"] = log_loss(probabilities, outcomes)

    diff_b_a = matches["rps_dixon_coles_all"].to_numpy() - matches["rps_dixon_coles"].to_numpy()
    mean_b_a, lo_b_a, hi_b_a = paired_bootstrap_ci(diff_b_a, seed=seed)

    diff_b_current = matches["rps_dixon_coles_all"].to_numpy() - matches["rps_current"].to_numpy()
    mean_b_current, lo_b_current, hi_b_current = paired_bootstrap_ci(diff_b_current, seed=seed)

    summary = {
        "season": season,
        "xi": xi,
        "max_iterations": max_iterations,
        "matches": len(matches),
        "dropped_dixon_coles_all": dropped_dixon_coles_all,
        "dropped_arm_a_vs_current": summary_a["dropped"],
        # score_season's OWN arm-A RPS, untouched by any row arm B dropped -
        # the number the "arm A reproduces dixon_coles_multiseason.csv"
        # sanity check validates against.
        "arm_a_reference_rps": summary_a["models"]["dixon_coles"]["rps"],
        "coverage": store.coverage(season),
        "models": {
            name: {
                "brier": float(matches[f"brier_{name}"].mean()),
                "rps": float(matches[f"rps_{name}"].mean()),
                "log_loss": float(matches[f"log_loss_{name}"].mean()),
            }
            for name in arms
        },
        "b_vs_a": {
            "diff": float(mean_b_a),
            "ci_low": lo_b_a,
            "ci_high": hi_b_a,
            "b_wins": int((diff_b_a < 0).sum()),
            "b_better": bool(mean_b_a < 0),
        },
        "b_vs_current": {
            "diff": float(mean_b_current),
            "ci_low": lo_b_current,
            "ci_high": hi_b_current,
            "b_wins": int((diff_b_current < 0).sum()),
        },
        "a_vs_current": {
            "diff": summary_a["paired_rps_diff"],
            "ci_low": summary_a["ci_low"],
            "ci_high": summary_a["ci_high"],
            "a_wins": summary_a["dixon_coles_wins"],
        },
        "drift": drift,
    }
    return matches, summary


def elo_forecasts_for_date(
    season: int,
    as_of_date: str,
    tables: Tables,
    match_store: MatchStore,
    adapter: EloAdapter,
):
    """Arm C's (match_key -> (p_home, p_draw, p_away), lambda_fallbacks) for
    every fixture remaining as of `as_of_date`.

    The two lambdas come from `adapter.build_baseline` - the Elo replay ->
    `DifferenceMap` -> total-goals decomposition (see this module's ARM C
    docstring section and `domain/elo_lambda.py`). Outcome probabilities are
    then the closed-form independent-Poisson probabilities from
    `domain/match_outcome_probs.py` - the SAME function the incumbent's own
    match forecasts use (`variant_sweep.analytic_forecasts_for_date`) - NOT
    `dixon_coles.outcome_probs`: Elo's lambdas carry no Dixon-Coles rho, so
    there is no low-score correction to apply (rho = 0 by construction).

    `adapter` must be a single `EloAdapter` instance built ONCE per season by
    the caller and passed in here - its Elo replay and difference map are
    cached per instance (see `EloAdapter`'s module docstring); constructing a
    fresh adapter per date would re-replay the whole store and re-fit the
    difference map on every one of a season's ~110 as-of dates for
    identical output.
    """
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=as_of_date)
    remaining = tables.remaining_games(blank_from_date=as_of_date)
    return elo_forecasts_from_tables(adapter, fixtures, remaining)


def elo_forecasts_from_tables(adapter: EloAdapter, fixtures: pd.DataFrame, remaining: pd.DataFrame):
    """`elo_forecasts_for_date`'s body, given the as-of date's two fixture
    tables already built. The tables depend only on the season and date, not
    on any Elo setting, so a caller scoring many settings on the same dates
    (`elo_backtest`) builds them once and passes them to every adapter."""
    baseline = adapter.build_baseline(fixtures, remaining)

    if len(baseline.lam_home) == 0:
        return {}, baseline.lambda_fallbacks

    probs = match_outcome_probs(baseline.lam_home, baseline.lam_away)
    forecasts = {
        f"{home} x {away}": (float(p[0]), float(p[1]), float(p[2]))
        for home, away, p in zip(baseline.home_name, baseline.away_name, probs)
    }
    return forecasts, baseline.lambda_fallbacks


def _layer_arm(matches: pd.DataFrame, forecasts_by_date: dict, name: str):
    """Layers one more arm's forecasts onto a `matches` frame that already
    has `as_of_date`/`match_key` columns - the exact per-date dict lookup
    `score_data_vs_league` uses to layer arm B onto arm A, factored out so it
    is testable without pickles and reusable for arm C.

    `forecasts_by_date` is `{as_of_date: {match_key: (p_home, p_draw,
    p_away)}}`. Any row whose `(as_of_date, match_key)` is missing from
    `forecasts_by_date` is dropped from the returned frame and counted -
    never scored on the other arms only. Adds `p_{o}_{name}` columns (`o` in
    OUTCOMES) to each surviving row.

    Returns (matches_with_arm, dropped_count). Does not touch the existing
    arm-B layering inline in `score_data_vs_league` - that stays as is.
    """
    rows = []
    dropped = 0
    for row in matches.itertuples(index=False):
        forecast = forecasts_by_date.get(row.as_of_date, {}).get(row.match_key)
        if forecast is None:
            dropped += 1
            continue
        record = row._asdict()
        record.update({f"p_{o}_{name}": p for o, p in zip(OUTCOMES, forecast)})
        rows.append(record)
    return pd.DataFrame(rows), dropped


def score_four_arms(
    season: int,
    xi: float = dixon_coles.DEFAULT_XI,
    seed: int = 7,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    match_store: MatchStore = None,
):
    """Arms A, B, C (Elo) and current on IDENTICAL matches at horizon 0 - the
    four-arm-backtest ticket's scoring function.

    Calls `score_data_vs_league` UNCHANGED for arms A, B and current, then
    layers arm C on that exact match set with `_layer_arm` (mirroring how B
    is layered on A) - any match arm C has no forecast for is dropped from
    the returned `matches` and counted as `dropped_elo`, never scored on the
    other three arms only.

    Returns (matches, summary). `summary` carries `score_data_vs_league`'s
    own `models` (dixon_coles/dixon_coles_all/current), `b_vs_a`,
    `b_vs_current` and `a_vs_current` entries UNCHANGED - computed on the
    pre-arm-C match set, `summary["matches_abc"]` - so the reproduction gate
    in `run_four_arm_backtest` can validate them against
    `dixon_coles_all_vs_league.csv` without arm C's drops able to move them.
    `summary["matches_four_arms"]` is the (possibly smaller) count arm C's
    own numbers and the three C comparisons below are computed on.
    """
    matches_abc, summary_abc = score_data_vs_league(
        season, xi=xi, seed=seed, max_iterations=max_iterations, match_store=match_store
    )

    dates = backfill_dates(season)
    tables = Tables(SeasonData(season))
    store = match_store if match_store is not None else MatchStore()

    # One EloAdapter per season - its replay and difference-map fit are
    # cached per instance (see EloAdapter's module docstring and
    # elo_forecasts_for_date's docstring above); rebuilding it per date
    # would refit both ~110 times per season for identical output.
    # Pinned to the 2019 line this backtest was run and committed with
    # (four_arms.csv); the adapter's default has since become the previous
    # season (elo-line-previous-season).
    adapter = EloAdapter("average", season, match_store=store, lambda_params=EloLambdaParams(burn_in_season=2019))

    elo_by_date = {}
    lambda_fallbacks_total = 0
    for date in dates:
        forecasts, fallbacks = elo_forecasts_for_date(season, date, tables, store, adapter)
        elo_by_date[date] = forecasts
        lambda_fallbacks_total += fallbacks

    matches, dropped_elo = _layer_arm(matches_abc, elo_by_date, "elo")

    outcomes = np.array([ONE_HOT[o] for o in matches["outcome"]], dtype=float)
    probabilities = matches[[f"p_{o}_elo" for o in OUTCOMES]].to_numpy()
    matches["brier_elo"] = brier(probabilities, outcomes)
    matches["rps_elo"] = rps(probabilities, outcomes)
    matches["log_loss_elo"] = log_loss(probabilities, outcomes)

    def paired(a_col: str, b_col: str) -> dict:
        diff = matches[a_col].to_numpy() - matches[b_col].to_numpy()
        mean_diff, ci_low, ci_high = paired_bootstrap_ci(diff, seed=seed)
        return {
            "diff": float(mean_diff),
            "ci_low": ci_low,
            "ci_high": ci_high,
            "c_wins": int((diff < 0).sum()),
            "c_better": bool(mean_diff < 0),
        }

    c_vs_b = paired("rps_elo", "rps_dixon_coles_all")
    c_vs_a = paired("rps_elo", "rps_dixon_coles")
    c_vs_current = paired("rps_elo", "rps_current")

    summary = dict(summary_abc)
    summary["matches_abc"] = summary_abc["matches"]
    summary["matches_four_arms"] = len(matches)
    summary["dropped_elo"] = dropped_elo
    summary["lambda_fallbacks_total"] = lambda_fallbacks_total
    summary["models"] = dict(summary_abc["models"])
    summary["models"]["elo"] = {
        "brier": float(matches["brier_elo"].mean()),
        "rps": float(matches["rps_elo"].mean()),
        "log_loss": float(matches["log_loss_elo"].mean()),
    }
    summary["c_vs_b"] = c_vs_b
    summary["c_vs_a"] = c_vs_a
    summary["c_vs_current"] = c_vs_current

    return matches, summary


def print_report(summary: dict) -> None:
    print(
        f"season {summary['season']}  matches scored: {summary['matches']}  "
        f"dropped: {summary['dropped']}  xi={summary['xi']}"
    )
    print(
        "observed rates: "
        + "  ".join(f"{o} {100 * r:.1f}%" for o, r in summary["observed_rates"].items())
    )
    print()
    print(f"{'model':>14}{'brier':>10}{'rps':>10}{'log loss':>10}{'mean p(draw)':>15}")
    for name, m in summary["models"].items():
        print(
            f"{name:>14}{m['brier']:>10.4f}{m['rps']:>10.4f}{m['log_loss']:>10.4f}"
            f"{100 * m['mean_p_draw']:>14.1f}%"
        )
    print()
    print(
        f"paired RPS diff (dixon_coles - current): {summary['paired_rps_diff']:+.5f}  "
        f"95% CI [{summary['ci_low']:+.5f}, {summary['ci_high']:+.5f}]  "
        f"dixon_coles better on {summary['dixon_coles_wins']}/{summary['matches']} matches"
    )
    print()

    ratings = summary["final_ratings"]
    ordered = sorted(ratings.attack.items(), key=lambda kv: kv[1], reverse=True)
    print(
        f"final-date fit: home_advantage {ratings.home_advantage:.3f}  rho {ratings.rho:+.3f}  "
        f"converged {ratings.converged} in {ratings.iterations} iterations"
    )
    print("top 5 attack:    " + ", ".join(f"{t} {v:.2f}" for t, v in ordered[:5]))
    print("bottom 5 attack: " + ", ".join(f"{t} {v:.2f}" for t, v in ordered[-5:]))


def _season_export_row(summary: dict, partial_seasons: tuple) -> dict:
    """One row of `dixon_coles_all_vs_league.csv` from one
    `score_data_vs_league` summary - matches, both arms' RPS/Brier/log loss,
    both paired comparisons (B-A and B-current, plus A-current carried
    through from `score_season`), dropped-match counts, the Série A
    convergence-drift columns, and the coverage manifest."""
    drift = summary["drift"]
    models = summary["models"]
    return {
        "season": summary["season"],
        "partial": summary["season"] in partial_seasons,
        "matches": summary["matches"],
        "rps_dixon_coles": models["dixon_coles"]["rps"],
        "rps_dixon_coles_all": models["dixon_coles_all"]["rps"],
        "rps_current": models["current"]["rps"],
        "brier_dixon_coles": models["dixon_coles"]["brier"],
        "brier_dixon_coles_all": models["dixon_coles_all"]["brier"],
        "brier_current": models["current"]["brier"],
        "log_loss_dixon_coles": models["dixon_coles"]["log_loss"],
        "log_loss_dixon_coles_all": models["dixon_coles_all"]["log_loss"],
        "log_loss_current": models["current"]["log_loss"],
        "diff_b_vs_a": summary["b_vs_a"]["diff"],
        "ci_low_b_vs_a": summary["b_vs_a"]["ci_low"],
        "ci_high_b_vs_a": summary["b_vs_a"]["ci_high"],
        "b_wins_vs_a": summary["b_vs_a"]["b_wins"],
        "b_better_than_a": summary["b_vs_a"]["b_better"],
        "diff_b_vs_current": summary["b_vs_current"]["diff"],
        "ci_low_b_vs_current": summary["b_vs_current"]["ci_low"],
        "ci_high_b_vs_current": summary["b_vs_current"]["ci_high"],
        "b_wins_vs_current": summary["b_vs_current"]["b_wins"],
        "diff_a_vs_current": summary["a_vs_current"]["diff"],
        "ci_low_a_vs_current": summary["a_vs_current"]["ci_low"],
        "ci_high_a_vs_current": summary["a_vs_current"]["ci_high"],
        "a_wins_vs_current": summary["a_vs_current"]["a_wins"],
        "dropped_dixon_coles_all": summary["dropped_dixon_coles_all"],
        "dropped_missing_current": summary["dropped_arm_a_vs_current"]["missing_current"],
        "dropped_missing_dixon_coles": summary["dropped_arm_a_vs_current"]["missing_dixon_coles"],
        "dropped_no_prior_forecast": summary["dropped_arm_a_vs_current"]["no_prior_forecast"],
        "serie_a_attack_drift_max": float(drift["attack_drift"].max()) if len(drift) else float("nan"),
        "serie_a_defence_drift_max": float(drift["defence_drift"].max()) if len(drift) else float("nan"),
        "serie_a_attack_drift_median": float(drift["attack_drift"].median()) if len(drift) else float("nan"),
        "serie_a_defence_drift_median": float(drift["defence_drift"].median()) if len(drift) else float("nan"),
        "coverage_competitions": summary["coverage"]["competitions"],
        "coverage_clubs": len(summary["coverage"]["clubs"]),
    }


def run_data_vs_league_backtest(
    seasons: tuple = DATA_VS_LEAGUE_SEASONS,
    partial_seasons: tuple = PARTIAL_SEASONS,
    xi: float = dixon_coles.DEFAULT_XI,
    seed: int = 7,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    out: str = f"{EXPORTS_PATH}/dixon_coles_all_vs_league.csv",
    pooled_out: str = f"{EXPORTS_PATH}/dixon_coles_all_vs_league_pooled.csv",
    report_out: str = ".superpowers/sdd/data-vs-league-backtest-report.md",
    reference_csv: str = f"{EXPORTS_PATH}/dixon_coles_multiseason.csv",
):
    """The data-vs-league-backtest ticket's full run: score arms A and B for
    every season in `seasons` (full) and `partial_seasons` (2026, excluded
    from the pooled figure), validate arm A against the committed multiseason
    CSV, write the two export CSVs, and render the report.

    Returns (season_df, pooled_row, summaries) - see the CLI block for how
    these are used.
    """
    store = MatchStore()  # one load, reused across every season and date
    all_seasons = list(seasons) + list(partial_seasons)

    summaries = {}
    matches_by_season = {}
    for season in all_seasons:
        t0 = time.time()
        matches, summary = score_data_vs_league(
            season, xi=xi, seed=seed, max_iterations=max_iterations, match_store=store
        )
        elapsed = time.time() - t0
        n_dates = len(backfill_dates(season))
        per_date = elapsed / n_dates if n_dates else float("nan")
        print(
            f"season {season}: {elapsed:.1f}s over {n_dates} dates "
            f"({per_date:.2f}s/date, {summary['matches']} matches scored)",
            flush=True,
        )
        matches_by_season[season] = matches
        summaries[season] = summary

    # Sanity gate: arm A must reproduce the committed per-season numbers
    # exactly on every full season. Not `allclose` on purpose - a real
    # harness change and a floating-point coincidence should not look alike.
    reference = pd.read_csv(reference_csv).set_index("season")
    for season in seasons:
        expected = float(reference.loc[season, "rps_dixon_coles"])
        actual = summaries[season]["arm_a_reference_rps"]
        if abs(actual - expected) > 1e-9:
            raise ValueError(
                f"arm A season {season} RPS {actual!r} does not reproduce "
                f"{reference_csv}'s {expected!r} - the harness changed, not "
                "the model. Stopping."
            )
    print(f"arm A reproduces {reference_csv} exactly on {list(seasons)}", flush=True)

    season_df = pd.DataFrame(
        [_season_export_row(summaries[season], partial_seasons) for season in all_seasons]
    )
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    season_df.to_csv(out, index=False)

    # Pooled figure: full seasons only, matches concatenated flat - the same
    # match-weighted pooling convention variant_sweep.run_multi_season_sweep
    # uses ("every scored match from every season concatenated into one flat
    # sample"), not an average of per-season point estimates.
    pooled_matches = pd.concat([matches_by_season[s] for s in seasons], ignore_index=True)
    diff_b_a = pooled_matches["rps_dixon_coles_all"].to_numpy() - pooled_matches["rps_dixon_coles"].to_numpy()
    mean_b_a, lo_b_a, hi_b_a = paired_bootstrap_ci(diff_b_a, seed=seed)
    diff_b_current = pooled_matches["rps_dixon_coles_all"].to_numpy() - pooled_matches["rps_current"].to_numpy()
    mean_b_current, lo_b_current, hi_b_current = paired_bootstrap_ci(diff_b_current, seed=seed)
    diff_a_current = pooled_matches["rps_dixon_coles"].to_numpy() - pooled_matches["rps_current"].to_numpy()
    mean_a_current, lo_a_current, hi_a_current = paired_bootstrap_ci(diff_a_current, seed=seed)

    b_better_seasons = int(season_df.loc[season_df["season"].isin(seasons), "b_better_than_a"].sum())
    total_seasons = len(seasons)
    # The DECISION paragraph's threshold: B beats A in >= 4 of 6 seasons AND
    # the pooled interval is clear of zero, in B's favour (diff = B - A,
    # negative means B better, so the whole CI must sit below zero).
    decision_threshold_met = b_better_seasons >= 4 and hi_b_a < 0

    pooled_row = {
        "matches": len(pooled_matches),
        "seasons": ",".join(str(s) for s in seasons),
        "rps_dixon_coles": float(pooled_matches["rps_dixon_coles"].mean()),
        "rps_dixon_coles_all": float(pooled_matches["rps_dixon_coles_all"].mean()),
        "rps_current": float(pooled_matches["rps_current"].mean()),
        "brier_dixon_coles": float(pooled_matches["brier_dixon_coles"].mean()),
        "brier_dixon_coles_all": float(pooled_matches["brier_dixon_coles_all"].mean()),
        "brier_current": float(pooled_matches["brier_current"].mean()),
        "log_loss_dixon_coles": float(pooled_matches["log_loss_dixon_coles"].mean()),
        "log_loss_dixon_coles_all": float(pooled_matches["log_loss_dixon_coles_all"].mean()),
        "log_loss_current": float(pooled_matches["log_loss_current"].mean()),
        "diff_b_vs_a": mean_b_a,
        "ci_low_b_vs_a": lo_b_a,
        "ci_high_b_vs_a": hi_b_a,
        "b_wins_vs_a": int((diff_b_a < 0).sum()),
        "b_better_in_n_of_m_seasons": f"{b_better_seasons}/{total_seasons}",
        "diff_b_vs_current": mean_b_current,
        "ci_low_b_vs_current": lo_b_current,
        "ci_high_b_vs_current": hi_b_current,
        "diff_a_vs_current": mean_a_current,
        "ci_low_a_vs_current": lo_a_current,
        "ci_high_a_vs_current": hi_a_current,
        "decision_threshold_met": decision_threshold_met,
    }
    pd.DataFrame([pooled_row]).to_csv(pooled_out, index=False)

    report = _render_data_vs_league_report(
        season_df, pooled_row, summaries, seasons, partial_seasons, xi, max_iterations, seed, out, pooled_out
    )
    os.makedirs(os.path.dirname(report_out) or ".", exist_ok=True)
    with open(report_out, "w") as f:
        f.write(report)
    print(f"\nreport written to {report_out}")
    print(f"per-season export written to {out}")
    print(f"pooled export written to {pooled_out}")

    return season_df, pooled_row, summaries


def _render_data_vs_league_report(
    season_df: pd.DataFrame,
    pooled_row: dict,
    summaries: dict,
    seasons: tuple,
    partial_seasons: tuple,
    xi: float,
    max_iterations: int,
    seed: int,
    out: str,
    pooled_out: str,
) -> str:
    """Markdown report for `.superpowers/sdd/data-vs-league-backtest-report.md`.
    States the numbers and whether the DECISION paragraph's threshold is
    met; does NOT apply the decision - that call belongs to the ticket owner.
    """
    lines = []
    lines.append("# data-vs-league-backtest report")
    lines.append("")
    lines.append(
        f"Arm A (Dixon-Coles, league only) vs arm B (Dixon-Coles, all admitted "
        f"competitions) on identical matches, horizon 0, RPS headline. "
        f"`xi={xi}`, arm B refit at `max_iterations={max_iterations}` "
        f"(drift measured against the domain default of {DRIFT_BASE_ITERATIONS}), "
        f"paired-bootstrap seed {seed}, 10k draws."
    )
    lines.append("")

    lines.append("## Per-season")
    lines.append("")
    header = (
        "| season | matches | arm A RPS | arm B RPS | diff (B-A) | 95% CI | B better |"
    )
    lines.append(header)
    lines.append("|---|---:|---:|---:|---:|---|---|")
    for _, row in season_df.iterrows():
        label = f"{int(row['season'])}{' (partial)' if row['partial'] else ''}"
        lines.append(
            f"| {label} | {int(row['matches'])} | {row['rps_dixon_coles']:.4f} | "
            f"{row['rps_dixon_coles_all']:.4f} | {row['diff_b_vs_a']:+.5f} | "
            f"[{row['ci_low_b_vs_a']:+.5f}, {row['ci_high_b_vs_a']:+.5f}] | "
            f"{'yes' if row['b_better_than_a'] else 'no'} |"
        )
    lines.append("")
    lines.append(
        f"**B better in {pooled_row['b_better_in_n_of_m_seasons']} full seasons** "
        f"({', '.join(str(s) for s in seasons)}). 2026 is partial and excluded from "
        "the pooled figure below, per the ticket."
    )
    lines.append("")

    lines.append("## Pooled (full seasons only)")
    lines.append("")
    lines.append(
        f"- matches: {pooled_row['matches']}\n"
        f"- arm A RPS: {pooled_row['rps_dixon_coles']:.4f}  |  arm B RPS: "
        f"{pooled_row['rps_dixon_coles_all']:.4f}  |  current RPS: {pooled_row['rps_current']:.4f}\n"
        f"- arm A Brier: {pooled_row['brier_dixon_coles']:.4f}  |  arm B Brier: "
        f"{pooled_row['brier_dixon_coles_all']:.4f}  |  current Brier: {pooled_row['brier_current']:.4f}\n"
        f"- arm A log loss: {pooled_row['log_loss_dixon_coles']:.4f}  |  arm B log loss: "
        f"{pooled_row['log_loss_dixon_coles_all']:.4f}  |  current log loss: {pooled_row['log_loss_current']:.4f}\n"
        f"- **pooled diff (B - A): {pooled_row['diff_b_vs_a']:+.5f}, 95% CI "
        f"[{pooled_row['ci_low_b_vs_a']:+.5f}, {pooled_row['ci_high_b_vs_a']:+.5f}]**, "
        f"B wins {pooled_row['b_wins_vs_a']}/{pooled_row['matches']} individual matches\n"
        f"- B vs current: {pooled_row['diff_b_vs_current']:+.5f}, 95% CI "
        f"[{pooled_row['ci_low_b_vs_current']:+.5f}, {pooled_row['ci_high_b_vs_current']:+.5f}]\n"
        f"- A vs current: {pooled_row['diff_a_vs_current']:+.5f}, 95% CI "
        f"[{pooled_row['ci_low_a_vs_current']:+.5f}, {pooled_row['ci_high_a_vs_current']:+.5f}]"
    )
    lines.append("")
    lines.append(
        "**Six seasons cannot support the eight-of-ten rule; whatever this finds is "
        "provisional**, per the ticket."
    )
    lines.append("")

    lines.append("## DECISION threshold")
    lines.append("")
    lines.append(
        "The DECISION paragraph (E3/T3.2): B beats A in >= 4 of 6 seasons with a "
        "pooled interval clear of zero, in B's favour. **This report states whether "
        "that threshold is met and does not apply the decision** - that call belongs "
        "to the ticket owner."
    )
    lines.append("")
    lines.append(
        f"- B better in N of M: {pooled_row['b_better_in_n_of_m_seasons']}\n"
        f"- pooled CI clear of zero (upper bound < 0): "
        f"{'yes' if pooled_row['ci_high_b_vs_a'] < 0 else 'no'} "
        f"(upper bound {pooled_row['ci_high_b_vs_a']:+.5f})\n"
        f"- **threshold met: {pooled_row['decision_threshold_met']}**"
    )
    lines.append("")

    lines.append("## Arm B convergence: does not converge at the domain default")
    lines.append("")
    lines.append(
        "Arm B's 207-club fit does not converge at 200 iterations (nor, on the "
        "measured 2025-08-01 date, at 5,000). It is scored here at "
        f"`max_iterations={max_iterations}`. For every scored as-of date, the max "
        f"relative change in attack/defence between the {DRIFT_BASE_ITERATIONS}- and "
        f"{max_iterations}-iteration fits, RESTRICTED TO that season's Série A clubs, "
        "is recorded. Distribution across every scored date (all seasons, including "
        "2026):"
    )
    lines.append("")
    all_drift = pd.concat([summaries[s]["drift"] for s in list(seasons) + list(partial_seasons)], ignore_index=True)
    for field, label in (("attack_drift", "attack"), ("defence_drift", "defence")):
        values = all_drift[field]
        lines.append(
            f"- {label}: min {values.min():.2e}, median {values.median():.2e}, "
            f"p95 {values.quantile(0.95):.2e}, **max {values.max():.2e}**"
        )
    overall_max = max(all_drift["attack_drift"].max(), all_drift["defence_drift"].max())
    lines.append("")
    if overall_max > 1e-3:
        lines.append(
            f"**FLAG: at least one Série A club moved more than 1e-3 relative on at "
            f"least one date (max {overall_max:.2e}).** Arm B's lambdas depend on an "
            "arbitrary iteration cap and the comparison above is not clean - see the "
            "per-season `serie_a_attack_drift_max`/`serie_a_defence_drift_max` "
            f"columns in {out} for which dates."
        )
    else:
        lines.append(
            f"No Série A club moved more than 1e-3 relative on any scored date "
            f"(max observed {overall_max:.2e}). Arm B's Série A lambdas are stable "
            f"to the iteration cap even though the fit as a whole is not converged - "
            "the comparison above is clean on that axis."
        )
    lines.append("")

    lines.append("## Coverage manifest")
    lines.append("")
    lines.append("| season | competitions (league_id: matches) | Série A clubs with any match |")
    lines.append("|---|---|---:|")
    for season in list(seasons) + list(partial_seasons):
        coverage = summaries[season]["coverage"]
        label = f"{season}{' (partial)' if season in partial_seasons else ''}"
        competitions = ", ".join(f"{k}: {v}" for k, v in sorted(coverage["competitions"].items()))
        lines.append(f"| {label} | {competitions} | {len(coverage['clubs'])} |")
    lines.append("")

    lines.append("## Sanity checks")
    lines.append("")
    lines.append(
        f"- Arm A reproduces `{EXPORTS_PATH}/dixon_coles_multiseason.csv` exactly "
        f"(abs diff < 1e-9) on {list(seasons)} - checked before any number above "
        "was written; the run raises and stops otherwise.\n"
        "- Dropped matches, counted per season (never silently lost): see "
        f"`dropped_dixon_coles_all`, `dropped_missing_current`, "
        f"`dropped_missing_dixon_coles` in {out}.\n"
        "- Arm B's forecast for a match uses only matches strictly before it: "
        "`MatchStore.before` filters `kickoff < cutoff` with `cutoff` the day "
        "after the forecast's frontier date - see "
        "`tests/test_dixon_coles_backtest.py::"
        "test_arm_b_forecast_uses_only_matches_strictly_before_it`."
    )
    lines.append("")

    return "\n".join(lines)


def _four_arm_export_row(summary: dict, partial_seasons: tuple) -> dict:
    """One row of `four_arms.csv` - matches, all four arms' RPS/Brier/log
    loss, the three arm-C comparisons (C vs B, C vs A, C vs current), the
    B-vs-A/B-vs-current comparisons carried unchanged from
    `score_data_vs_league`, drop/fallback counts, and the coverage manifest.

    `matches` is `summary["matches_abc"]` - the pre-arm-C-layering count the
    arm A/B/current RPS/Brier/log-loss figures in this row are computed on
    (see `score_four_arms`'s docstring). `matches_abc` and
    `matches_four_arms` are also written out explicitly so a season where
    `dropped_elo > 0` (arm C scored on fewer matches than A/B/current) is
    never silently ambiguous - expected `dropped_elo == 0` on every season.
    """
    models = summary["models"]
    return {
        "season": summary["season"],
        "partial": summary["season"] in partial_seasons,
        "matches": summary["matches_abc"],
        "rps_dixon_coles": models["dixon_coles"]["rps"],
        "rps_dixon_coles_all": models["dixon_coles_all"]["rps"],
        "rps_current": models["current"]["rps"],
        "rps_elo": models["elo"]["rps"],
        "brier_dixon_coles": models["dixon_coles"]["brier"],
        "brier_dixon_coles_all": models["dixon_coles_all"]["brier"],
        "brier_current": models["current"]["brier"],
        "brier_elo": models["elo"]["brier"],
        "log_loss_dixon_coles": models["dixon_coles"]["log_loss"],
        "log_loss_dixon_coles_all": models["dixon_coles_all"]["log_loss"],
        "log_loss_current": models["current"]["log_loss"],
        "log_loss_elo": models["elo"]["log_loss"],
        "diff_c_vs_b": summary["c_vs_b"]["diff"],
        "ci_low_c_vs_b": summary["c_vs_b"]["ci_low"],
        "ci_high_c_vs_b": summary["c_vs_b"]["ci_high"],
        "c_wins_vs_b": summary["c_vs_b"]["c_wins"],
        "c_better_than_b": summary["c_vs_b"]["c_better"],
        "diff_c_vs_a": summary["c_vs_a"]["diff"],
        "ci_low_c_vs_a": summary["c_vs_a"]["ci_low"],
        "ci_high_c_vs_a": summary["c_vs_a"]["ci_high"],
        "diff_c_vs_current": summary["c_vs_current"]["diff"],
        "ci_low_c_vs_current": summary["c_vs_current"]["ci_low"],
        "ci_high_c_vs_current": summary["c_vs_current"]["ci_high"],
        "c_wins_vs_current": summary["c_vs_current"]["c_wins"],
        "diff_b_vs_a": summary["b_vs_a"]["diff"],
        "ci_low_b_vs_a": summary["b_vs_a"]["ci_low"],
        "ci_high_b_vs_a": summary["b_vs_a"]["ci_high"],
        "diff_b_vs_current": summary["b_vs_current"]["diff"],
        "ci_low_b_vs_current": summary["b_vs_current"]["ci_low"],
        "ci_high_b_vs_current": summary["b_vs_current"]["ci_high"],
        "dropped_elo": summary["dropped_elo"],
        "lambda_fallbacks_total": summary["lambda_fallbacks_total"],
        "coverage_competitions": summary["coverage"]["competitions"],
        "coverage_clubs": len(summary["coverage"]["clubs"]),
        "matches_abc": summary["matches_abc"],
        "matches_four_arms": summary["matches_four_arms"],
    }


def _four_arm_pooled_row(pooled_matches: pd.DataFrame, season_df: pd.DataFrame, seasons: tuple, seed: int) -> dict:
    """The `four_arms_pooled.csv` row: `pooled_matches` (full seasons only,
    concatenated flat - the same match-weighted pooling convention
    `run_data_vs_league_backtest` uses) reduced to the four arms' mean
    RPS/Brier/log-loss, the pooled `c_vs_b`/`c_vs_a`/`c_vs_current`/`b_vs_a`/
    `b_vs_current` paired-bootstrap comparisons, and how many of `seasons`'
    rows in `season_df` show C beating B (`c_better_than_b`) or beating
    current (`diff_c_vs_current < 0`).

    Factored out of `run_four_arm_backtest` so it is testable on a small
    hand-built `pooled_matches`/`season_df` without a six-season run.
    """

    def pooled_pair(a_col: str, b_col: str):
        diff = pooled_matches[a_col].to_numpy() - pooled_matches[b_col].to_numpy()
        mean_diff, ci_low, ci_high = paired_bootstrap_ci(diff, seed=seed)
        return float(mean_diff), ci_low, ci_high

    mean_c_b, lo_c_b, hi_c_b = pooled_pair("rps_elo", "rps_dixon_coles_all")
    mean_c_a, lo_c_a, hi_c_a = pooled_pair("rps_elo", "rps_dixon_coles")
    mean_c_current, lo_c_current, hi_c_current = pooled_pair("rps_elo", "rps_current")
    mean_b_a, lo_b_a, hi_b_a = pooled_pair("rps_dixon_coles_all", "rps_dixon_coles")
    mean_b_current, lo_b_current, hi_b_current = pooled_pair("rps_dixon_coles_all", "rps_current")

    full_season_rows = season_df[season_df["season"].isin(seasons)]
    c_better_seasons = int(full_season_rows["c_better_than_b"].sum())
    c_better_than_current_seasons = int((full_season_rows["diff_c_vs_current"] < 0).sum())
    total_seasons = len(seasons)

    return {
        "matches": len(pooled_matches),
        "seasons": ",".join(str(s) for s in seasons),
        "rps_dixon_coles": float(pooled_matches["rps_dixon_coles"].mean()),
        "rps_dixon_coles_all": float(pooled_matches["rps_dixon_coles_all"].mean()),
        "rps_current": float(pooled_matches["rps_current"].mean()),
        "rps_elo": float(pooled_matches["rps_elo"].mean()),
        "brier_dixon_coles": float(pooled_matches["brier_dixon_coles"].mean()),
        "brier_dixon_coles_all": float(pooled_matches["brier_dixon_coles_all"].mean()),
        "brier_current": float(pooled_matches["brier_current"].mean()),
        "brier_elo": float(pooled_matches["brier_elo"].mean()),
        "log_loss_dixon_coles": float(pooled_matches["log_loss_dixon_coles"].mean()),
        "log_loss_dixon_coles_all": float(pooled_matches["log_loss_dixon_coles_all"].mean()),
        "log_loss_current": float(pooled_matches["log_loss_current"].mean()),
        "log_loss_elo": float(pooled_matches["log_loss_elo"].mean()),
        "diff_c_vs_b": mean_c_b,
        "ci_low_c_vs_b": lo_c_b,
        "ci_high_c_vs_b": hi_c_b,
        "diff_c_vs_a": mean_c_a,
        "ci_low_c_vs_a": lo_c_a,
        "ci_high_c_vs_a": hi_c_a,
        "diff_c_vs_current": mean_c_current,
        "ci_low_c_vs_current": lo_c_current,
        "ci_high_c_vs_current": hi_c_current,
        "diff_b_vs_a": mean_b_a,
        "ci_low_b_vs_a": lo_b_a,
        "ci_high_b_vs_a": hi_b_a,
        "diff_b_vs_current": mean_b_current,
        "ci_low_b_vs_current": lo_b_current,
        "ci_high_b_vs_current": hi_b_current,
        "c_better_in_n_of_m_seasons": f"{c_better_seasons}/{total_seasons}",
        "c_better_than_current_in_n_of_m": f"{c_better_than_current_seasons}/{total_seasons}",
    }


def run_four_arm_backtest(
    seasons: tuple = DATA_VS_LEAGUE_SEASONS,
    partial_seasons: tuple = PARTIAL_SEASONS,
    xi: float = dixon_coles.DEFAULT_XI,
    seed: int = 7,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    out: str = f"{EXPORTS_PATH}/four_arms.csv",
    pooled_out: str = f"{EXPORTS_PATH}/four_arms_pooled.csv",
    report_out: str = ".superpowers/sdd/four-arm-backtest-report.md",
    reference_csv: str = f"{EXPORTS_PATH}/dixon_coles_all_vs_league.csv",
):
    """The four-arm-backtest ticket's full run: score arms A, B, C (Elo) and
    current for every season in `seasons` (full) and `partial_seasons`
    (2026, excluded from the pooled figure), validate arm A/B/current
    against the committed `dixon_coles_all_vs_league.csv`, write the two
    export CSVs, and render the report.

    Returns (season_df, pooled_row, summaries).
    """
    store = MatchStore()  # one load, reused across every season and date
    all_seasons = list(seasons) + list(partial_seasons)

    summaries = {}
    matches_by_season = {}
    for season in all_seasons:
        t0 = time.time()
        matches, summary = score_four_arms(
            season, xi=xi, seed=seed, max_iterations=max_iterations, match_store=store
        )
        elapsed = time.time() - t0
        n_dates = len(backfill_dates(season))
        per_date = elapsed / n_dates if n_dates else float("nan")
        print(
            f"season {season}: {elapsed:.1f}s over {n_dates} dates "
            f"({per_date:.2f}s/date, {summary['matches_four_arms']} matches scored, "
            f"dropped_elo={summary['dropped_elo']})",
            flush=True,
        )
        matches_by_season[season] = matches
        summaries[season] = summary

    # Reproduction gate: the arm A/B/current numbers this module reports -
    # summary["models"]["dixon_coles"/"dixon_coles_all"/"current"] and
    # summary["matches_abc"] - are score_data_vs_league's own, computed
    # BEFORE arm C's layering (see score_four_arms's docstring), so this
    # compares them against dixon_coles_all_vs_league.csv exactly as
    # run_data_vs_league_backtest already does - not `allclose`, on purpose.
    reference = pd.read_csv(reference_csv).set_index("season")
    for season in seasons:
        expected_row = reference.loc[season]
        models = summaries[season]["models"]
        checks = {
            "rps_dixon_coles": models["dixon_coles"]["rps"],
            "rps_dixon_coles_all": models["dixon_coles_all"]["rps"],
            "rps_current": models["current"]["rps"],
        }
        for col, actual in checks.items():
            expected = float(expected_row[col])
            if abs(actual - expected) > 1e-9:
                raise ValueError(
                    f"arm A/B/current season {season} {col} {actual!r} does not "
                    f"reproduce {reference_csv}'s {expected!r} - the harness "
                    "changed, not the model. Stopping."
                )
        expected_matches = int(expected_row["matches"])
        actual_matches = summaries[season]["matches_abc"]
        if actual_matches != expected_matches:
            raise ValueError(
                f"arm A/B/current season {season} matches {actual_matches} does "
                f"not reproduce {reference_csv}'s {expected_matches} - the "
                "harness changed, not the model. Stopping."
            )
    print(f"arm A/B/current reproduces {reference_csv} exactly on {list(seasons)}", flush=True)

    season_df = pd.DataFrame(
        [_four_arm_export_row(summaries[season], partial_seasons) for season in all_seasons]
    )
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    season_df.to_csv(out, index=False)

    # Pooled figure: full seasons only, matches concatenated flat - the same
    # match-weighted pooling convention run_data_vs_league_backtest uses.
    pooled_matches = pd.concat([matches_by_season[s] for s in seasons], ignore_index=True)
    pooled_row = _four_arm_pooled_row(pooled_matches, season_df, seasons, seed)
    pd.DataFrame([pooled_row]).to_csv(pooled_out, index=False)

    report = _render_four_arm_report(
        season_df,
        pooled_row,
        summaries,
        seasons,
        partial_seasons,
        xi,
        max_iterations,
        seed,
        out,
        pooled_out,
        reference_csv,
    )
    os.makedirs(os.path.dirname(report_out) or ".", exist_ok=True)
    with open(report_out, "w") as f:
        f.write(report)
    print(f"\nreport written to {report_out}")
    print(f"per-season export written to {out}")
    print(f"pooled export written to {pooled_out}")

    return season_df, pooled_row, summaries


def _render_four_arm_report(
    season_df: pd.DataFrame,
    pooled_row: dict,
    summaries: dict,
    seasons: tuple,
    partial_seasons: tuple,
    xi: float,
    max_iterations: int,
    seed: int,
    out: str,
    pooled_out: str,
    reference_csv: str,
) -> str:
    """Markdown report for `.superpowers/sdd/four-arm-backtest-report.md`:
    per-season table, pooled table, the three arm-C comparisons in plain
    English, the arm-A/B/current reproduction gate result, and the standing
    caveat that six seasons cannot meet the eight-of-ten rule. States the
    numbers; does not editorialise on whether arm C "won."
    """
    lines = []
    lines.append("# four-arm-backtest report")
    lines.append("")
    lines.append(
        f"Four arms on identical matches, horizon 0, RPS headline: A (Dixon-Coles, "
        f"league only), B (Dixon-Coles, all admitted competitions), C (Elo -> two "
        f"lambdas, all admitted competitions), and current (the shipped same-venue-"
        f"average model). `xi={xi}`, arm B refit at `max_iterations={max_iterations}`, "
        f"paired-bootstrap seed {seed}, 10k draws."
    )
    lines.append("")

    lines.append("## Per-season")
    lines.append("")
    header = (
        "| season | matches | RPS A | RPS B | RPS C (Elo) | RPS current | "
        "diff C-B | 95% CI | diff C-current | 95% CI |"
    )
    lines.append(header)
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|---:|---|")
    for _, row in season_df.iterrows():
        label = f"{int(row['season'])}{' (partial)' if row['partial'] else ''}"
        lines.append(
            f"| {label} | {int(row['matches'])} | {row['rps_dixon_coles']:.4f} | "
            f"{row['rps_dixon_coles_all']:.4f} | {row['rps_elo']:.4f} | {row['rps_current']:.4f} | "
            f"{row['diff_c_vs_b']:+.5f} | [{row['ci_low_c_vs_b']:+.5f}, {row['ci_high_c_vs_b']:+.5f}] | "
            f"{row['diff_c_vs_current']:+.5f} | [{row['ci_low_c_vs_current']:+.5f}, "
            f"{row['ci_high_c_vs_current']:+.5f}] |"
        )
    lines.append("")
    lines.append(
        f"**C better than B in {pooled_row['c_better_in_n_of_m_seasons']} full seasons; "
        f"C better than current in {pooled_row['c_better_than_current_in_n_of_m']} full "
        f"seasons** ({', '.join(str(s) for s in seasons)}). 2026 is partial and excluded "
        "from the pooled figure below."
    )
    lines.append("")

    lines.append("## Pooled (full seasons only)")
    lines.append("")
    lines.append(
        f"| model | RPS | Brier | log loss |\n"
        f"|---|---:|---:|---:|\n"
        f"| A (Dixon-Coles, league) | {pooled_row['rps_dixon_coles']:.4f} | "
        f"{pooled_row['brier_dixon_coles']:.4f} | {pooled_row['log_loss_dixon_coles']:.4f} |\n"
        f"| B (Dixon-Coles, all competitions) | {pooled_row['rps_dixon_coles_all']:.4f} | "
        f"{pooled_row['brier_dixon_coles_all']:.4f} | {pooled_row['log_loss_dixon_coles_all']:.4f} |\n"
        f"| C (Elo) | {pooled_row['rps_elo']:.4f} | {pooled_row['brier_elo']:.4f} | "
        f"{pooled_row['log_loss_elo']:.4f} |\n"
        f"| current | {pooled_row['rps_current']:.4f} | {pooled_row['brier_current']:.4f} | "
        f"{pooled_row['log_loss_current']:.4f} |"
    )
    lines.append("")
    lines.append(f"matches pooled: {pooled_row['matches']}")
    lines.append("")

    lines.append("## Arm C (Elo) comparisons")
    lines.append("")
    lines.append(
        f"- **C vs B (data held equal, estimator differs)**: pooled diff (C - B) "
        f"{pooled_row['diff_c_vs_b']:+.5f}, 95% CI [{pooled_row['ci_low_c_vs_b']:+.5f}, "
        f"{pooled_row['ci_high_c_vs_b']:+.5f}]. C better in "
        f"{pooled_row['c_better_in_n_of_m_seasons']} full seasons."
    )
    lines.append(
        f"- **C vs A (Elo, all-competitions data, vs Dixon-Coles, league-only data)**: "
        f"pooled diff (C - A) {pooled_row['diff_c_vs_a']:+.5f}, 95% CI "
        f"[{pooled_row['ci_low_c_vs_a']:+.5f}, {pooled_row['ci_high_c_vs_a']:+.5f}]."
    )
    lines.append(
        f"- **C vs current (Elo vs the shipped model)**: pooled diff (C - current) "
        f"{pooled_row['diff_c_vs_current']:+.5f}, 95% CI [{pooled_row['ci_low_c_vs_current']:+.5f}, "
        f"{pooled_row['ci_high_c_vs_current']:+.5f}]. C better in "
        f"{pooled_row['c_better_than_current_in_n_of_m']} full seasons."
    )
    lines.append("")
    total_dropped_elo = int(season_df["dropped_elo"].sum())
    total_fallbacks = int(season_df["lambda_fallbacks_total"].sum())
    lines.append(
        f"Dropped for arm C (`dropped_elo`, summed across all {len(season_df)} scored "
        f"seasons including partial 2026): {total_dropped_elo}. Elo lambda fallbacks "
        f"(`lambda_fallbacks_total`, summed): {total_fallbacks}."
    )
    lines.append("")

    lines.append("## Reproduction gate (arm A / B / current)")
    lines.append("")
    lines.append(
        f"Arm A, B and current's RPS/Brier/log-loss and match counts - computed BEFORE "
        f"arm C's layering, from `score_data_vs_league`'s own summary, so arm C's drops "
        f"(if any) cannot move them - reproduce `{reference_csv}` exactly (abs diff < "
        f"1e-9 on RPS, exact match counts) on {list(seasons)}. **The run raises and "
        "stops before writing any export otherwise - this line is only reached when "
        "the gate passed.**"
    )
    lines.append("")

    lines.append("## Six-season caveat")
    lines.append("")
    lines.append(
        "**Six full seasons cannot support the eight-of-ten rule; whatever this finds "
        "is provisional**, per the ticket."
    )
    lines.append("")

    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    """The CLI parser, factored out of `__main__` so tests can exercise it
    (`parser.parse_args([...])`) without executing a backtest run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, help="single-season arm A vs current (original mode).")
    parser.add_argument("--xi", type=float, default=dixon_coles.DEFAULT_XI)
    parser.add_argument("--seed", type=int, default=7, help="paired-bootstrap seed.")
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--data-vs-league",
        action="store_true",
        help="run the data-vs-league-backtest: arms A and B, 2020-2025 (+2026 partial).",
    )
    parser.add_argument(
        "--four-arms",
        action="store_true",
        help=(
            "run the four-arm-backtest: arms A, B, C (Elo) and current, "
            "2020-2025 (+2026 partial). Takes priority over --data-vs-league "
            "if both are given."
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    # Defaults are None here (not a hardcoded path) because --pooled-out and
    # --report-out are shared between --data-vs-league and --four-arms, whose
    # default paths differ - each branch below fills in its own default only
    # when the flag was not passed.
    parser.add_argument("--pooled-out", default=None)
    parser.add_argument("--report-out", default=None)
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.four_arms:
        out = args.out or f"{EXPORTS_PATH}/four_arms.csv"
        pooled_out = args.pooled_out or f"{EXPORTS_PATH}/four_arms_pooled.csv"
        report_out = args.report_out or ".superpowers/sdd/four-arm-backtest-report.md"
        run_four_arm_backtest(
            xi=args.xi,
            seed=args.seed,
            max_iterations=args.max_iterations,
            out=out,
            pooled_out=pooled_out,
            report_out=report_out,
        )
    elif args.data_vs_league:
        out = args.out or f"{EXPORTS_PATH}/dixon_coles_all_vs_league.csv"
        pooled_out = args.pooled_out or f"{EXPORTS_PATH}/dixon_coles_all_vs_league_pooled.csv"
        report_out = args.report_out or ".superpowers/sdd/data-vs-league-backtest-report.md"
        run_data_vs_league_backtest(
            xi=args.xi,
            seed=args.seed,
            max_iterations=args.max_iterations,
            out=out,
            pooled_out=pooled_out,
            report_out=report_out,
        )
    else:
        if args.season is None:
            parser.error("--season is required unless --data-vs-league is given")
        out = args.out if args.out is not None else f"{EXPORTS_PATH}/dixon_coles_vs_current.csv"
        matches, summary = score_season(args.season, xi=args.xi, seed=args.seed)
        print_report(summary)

        if out:
            matches.to_csv(out, index=False)
            print(f"\nper-match table written to {out}")

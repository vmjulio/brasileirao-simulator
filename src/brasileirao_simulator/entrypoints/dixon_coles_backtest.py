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
"""

import argparse

import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.dixon_coles_adapter import DixonColesAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH
from brasileirao_simulator.domain import dixon_coles
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--xi", type=float, default=dixon_coles.DEFAULT_XI)
    parser.add_argument("--seed", type=int, default=7, help="paired-bootstrap seed.")
    parser.add_argument("--out", default=f"{EXPORTS_PATH}/dixon_coles_vs_current.csv")
    args = parser.parse_args()

    matches, summary = score_season(args.season, xi=args.xi, seed=args.seed)
    print_report(summary)

    if args.out:
        matches.to_csv(args.out, index=False)
        print(f"\nper-match table written to {args.out}")

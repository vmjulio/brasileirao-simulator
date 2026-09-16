"""Score our horizon-0 match forecasts against chancedegol.com.br's published ones.

chancedegol publishes, for every played match, the probabilities it gave
beforehand alongside the result - so the two models can be scored on identical
matches. Their probabilities sum to 1.000, so this is model against model with
no bookmaker overround to strip.

The headline metric is RPS (Ranked Probability Score), not Brier: home/draw/away
is ordered, and RPS charges less for predicting a home win that ends in a draw
than for one that ends in an away win. Brier treats the three as unrelated
labels and charges the same for both.

One caveat this cannot settle: their forecast timing is unpublished. Ours is
strictly horizon 0 - the last forecast made before kick-off, a median of one day
out. If theirs is issued closer to kick-off, part of any edge is information
rather than model.

Input is the parsed CSV written by their season page (see --source).
"""

import argparse
import csv
import json

import numpy as np

from brasileirao_simulator.config.settings import EXPORTS_PATH
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.variant_sweep import (
    analytic_forecasts_for_date,
    assign_horizon0,
    base_rate_probs,
    played_matches,
)

ONE_HOT = {"home": (1, 0, 0), "draw": (0, 1, 0), "away": (0, 0, 1)}
BOOTSTRAP_DRAWS = 10000


def incumbent_forecasts(season: int, season_data: SeasonData, tables: Tables) -> dict:
    """The fixed-lambda incumbent's horizon-0 forecasts, keyed by as-of date.

    This is the model the committed `benchmark.json` was scored with; it is
    `compare`'s default so that path reproduces byte-for-byte.
    """
    return {
        date: analytic_forecasts_for_date(season, date, tables=tables)
        for date in season_data.dates
    }


def elo_forecasts(match_store=None, line_season_for=None):
    """Build arm C's forecaster - Elo on all admitted competitions.

    `line_season_for(season)` pins the season Elo's goal-difference line is
    fitted on; `None` takes the adapter's default, the season before the one
    forecast.

    Returns a callable with `incumbent_forecasts`'s signature, so `compare`
    scores either model without knowing which it holds. Pass `match_store` to
    reuse one store across seasons; the per-season `EloAdapter` caches its Elo
    replay and difference-map fit, so it is built once per season rather than
    once per as-of date (see `elo_forecasts_for_date`'s docstring).

    Imported lazily: `dixon_coles_backtest` is a heavy module and the default
    incumbent path has no reason to pay for it.
    """
    from brasileirao_simulator.adapters.elo_adapter import EloAdapter
    from brasileirao_simulator.domain.elo_lambda import EloLambdaParams
    from brasileirao_simulator.domain.match_store import MatchStore
    from brasileirao_simulator.entrypoints.dixon_coles_backtest import elo_forecasts_for_date

    store = match_store if match_store is not None else MatchStore()

    def build(season: int, season_data: SeasonData, tables: Tables) -> dict:
        lambda_params = EloLambdaParams()
        if line_season_for is not None:
            lambda_params = EloLambdaParams(burn_in_season=line_season_for(season))
        adapter = EloAdapter("average", season, match_store=store, lambda_params=lambda_params)
        forecasts = {}
        for date in season_data.dates:
            forecasts[date], _ = elo_forecasts_for_date(season, date, tables, store, adapter)
        return forecasts

    return build


def elo_fixed_2019_forecasts():
    """Elo with its line pinned to 2019, as every Elo export was made before
    elo-line-previous-season - `benchmark_elo.json` reproduces with this."""
    from brasileirao_simulator.entrypoints.elo_backtest import fixed_2019

    return elo_forecasts(line_season_for=fixed_2019)


FORECASTERS = {
    "current": lambda: incumbent_forecasts,
    "elo": elo_forecasts,
    "elo-fixed-2019": elo_fixed_2019_forecasts,
}


def rps(probabilities: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    """Per-match Ranked Probability Score, on cumulative probabilities."""
    cumulative_p = np.cumsum(probabilities, axis=1)[:, :-1]
    cumulative_o = np.cumsum(outcomes, axis=1)[:, :-1]
    return ((cumulative_p - cumulative_o) ** 2).sum(axis=1) / (probabilities.shape[1] - 1)


def brier(probabilities: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    """Per-match multiclass Brier, summed over outcomes - range 0 to 2."""
    return ((probabilities - outcomes) ** 2).sum(axis=1)


def log_loss(probabilities: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    return -np.log((probabilities * outcomes).sum(axis=1).clip(1e-12))


def load_theirs(path: str) -> dict:
    with open(path) as f:
        return {
            f"{row['home']} x {row['away']}": (
                [float(row["p_home"]), float(row["p_draw"]), float(row["p_away"])],
                row["outcome"],
            )
            for row in csv.DictReader(f)
        }


def compare(season: int, source: str, forecaster=incumbent_forecasts, model: str = "current") -> dict:
    """Score one season of `forecaster`'s horizon-0 forecasts against
    chancedegol's, on the matches both models cover.

    `forecaster` decides which of our models is scored; `model` only names it
    in the result. Both models see identical matches whichever is passed:
    a match either side is missing, or whose result the two sources disagree
    on, is dropped for both.

    Returns (summary, paired) - the season summary written to the JSON, and
    the per-match paired RPS differences (ours minus theirs) behind it, which
    the caller concatenates across seasons for the pooled figure. Kept out of
    the summary because it is one float per match, not a reportable number.
    """
    theirs_by_match = load_theirs(source)

    season_data = SeasonData(season)
    tables = Tables(season_data)
    played = assign_horizon0(played_matches(season), season_data.dates)
    forecasts = forecaster(season, season_data, tables)
    reference = np.array(base_rate_probs(played_matches(season)["outcome"]), dtype=float)

    ours, theirs, outcomes = [], [], []
    unmatched = disagreements = 0

    for row in played.itertuples(index=False):
        our_probabilities = forecasts.get(row.as_of_date, {}).get(row.match_key)
        their_entry = theirs_by_match.get(row.match_key)
        if our_probabilities is None or their_entry is None:
            unmatched += 1
            continue

        their_probabilities, their_outcome = their_entry
        if their_outcome != row.outcome:
            # Their result contradicts our fixture data: drop it rather than
            # score two models against different truths.
            disagreements += 1
            continue

        ours.append(our_probabilities)
        theirs.append(their_probabilities)
        outcomes.append(ONE_HOT[row.outcome])

    ours = np.array(ours)
    theirs = np.array(theirs)
    outcomes = np.array(outcomes, dtype=float)
    reference = np.tile(reference, (len(outcomes), 1))

    paired = rps(ours, outcomes) - rps(theirs, outcomes)
    # The same paired comparison under Brier. RPS is the verdict - it knows
    # home/draw/away are ordered - but Brier answers "is the win the same
    # under the other proper score", which it should be if the edge is real.
    paired_brier = brier(ours, outcomes) - brier(theirs, outcomes)
    rng = np.random.default_rng(7)
    means = [rng.choice(paired, len(paired), replace=True).mean() for _ in range(BOOTSTRAP_DRAWS)]
    rng_brier = np.random.default_rng(7)
    brier_means = [rng_brier.choice(paired_brier, len(paired_brier), replace=True).mean() for _ in range(BOOTSTRAP_DRAWS)]

    summary = {
        "season": season,
        "model": model,
        "matches": len(outcomes),
        "unmatched": unmatched,
        "result_disagreements": disagreements,
        "models": {
            name: {
                "rps": float(rps(p, outcomes).mean()),
                "brier": float(brier(p, outcomes).mean()),
                "log_loss": float(log_loss(p, outcomes).mean()),
            }
            for name, p in (("ours", ours), ("chancedegol", theirs), ("base_rate", reference))
        },
        "paired_rps_diff": float(paired.mean()),
        "ci_low": float(np.percentile(means, 2.5)),
        "ci_high": float(np.percentile(means, 97.5)),
        "we_win": int((paired < 0).sum()),
        "paired_brier_diff": float(paired_brier.mean()),
        "brier_ci_low": float(np.percentile(brier_means, 2.5)),
        "brier_ci_high": float(np.percentile(brier_means, 97.5)),
    }
    return summary, (paired, paired_brier)


def pooled(per_season: list, paired_by_season: list, seed: int = 7) -> dict:
    """Pool the per-match paired differences of whole seasons.

    One flat bootstrap over every match, not an average of season means, so a
    season contributes in proportion to the matches it actually carries.
    `paired_by_season` holds (RPS, Brier) pairs per season.
    """
    paired = np.concatenate([pair[0] for pair in paired_by_season])
    paired_brier = np.concatenate([pair[1] for pair in paired_by_season])
    rng = np.random.default_rng(seed)
    means = [rng.choice(paired, len(paired), replace=True).mean() for _ in range(BOOTSTRAP_DRAWS)]
    rng_brier = np.random.default_rng(seed)
    brier_means = [rng_brier.choice(paired_brier, len(paired_brier), replace=True).mean() for _ in range(BOOTSTRAP_DRAWS)]
    seasons = [r["season"] for r in per_season]
    better = sum(1 for r in per_season if r["paired_rps_diff"] < 0)
    brier_better = sum(1 for r in per_season if r["paired_brier_diff"] < 0)
    return {
        "model": per_season[0]["model"],
        "seasons": ",".join(str(s) for s in seasons),
        "matches": int(len(paired)),
        "paired_rps_diff": float(paired.mean()),
        "ci_low": float(np.percentile(means, 2.5)),
        "ci_high": float(np.percentile(means, 97.5)),
        "we_win": int((paired < 0).sum()),
        "better_in_n_of_m_seasons": f"{better}/{len(seasons)}",
        "paired_brier_diff": float(paired_brier.mean()),
        "brier_ci_low": float(np.percentile(brier_means, 2.5)),
        "brier_ci_high": float(np.percentile(brier_means, 97.5)),
        "brier_better_in_n_of_m_seasons": f"{brier_better}/{len(seasons)}",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", required=True, help="comma-separated")
    parser.add_argument(
        "--source-template",
        default=f"{EXPORTS_PATH}/chancedegol_{{season}}.csv",
        help="parsed chancedegol forecasts, one CSV per season",
    )
    parser.add_argument("--out", default=f"{EXPORTS_PATH}/benchmark.json")
    parser.add_argument(
        "--model",
        choices=sorted(FORECASTERS),
        default="current",
        help="which of our models to score against theirs (default: the incumbent).",
    )
    parser.add_argument(
        "--partial-seasons",
        default="2026",
        help="comma-separated seasons still in progress, excluded from the pooled figure.",
    )
    parser.add_argument("--pooled-out", default=None)
    args = parser.parse_args()

    forecaster = FORECASTERS[args.model]()
    scored = [
        compare(
            int(season),
            args.source_template.format(season=season),
            forecaster=forecaster,
            model=args.model,
        )
        for season in args.seasons.split(",")
    ]
    results = [summary for summary, _ in scored]
    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)

    # Pooled over whole seasons only: a season still in progress would weight
    # the pool by how far into it we happen to be.
    partial = {int(s) for s in args.partial_seasons.split(",") if s}
    full = [(summary, p) for summary, p in scored if summary["season"] not in partial]
    if args.pooled_out and full:
        pooled_row = pooled([s for s, _ in full], [p for _, p in full])
        with open(args.pooled_out, "w") as f:
            json.dump(pooled_row, f, indent=1)

    print(f"{'season':>7}{'n':>6}{'ours':>9}{'theirs':>9}{'base':>9}{'diff':>9}{'we win':>9}")
    for r in results:
        m = r["models"]
        print(
            f"{r['season']:>7}{r['matches']:>6}{m['ours']['rps']:>9.4f}"
            f"{m['chancedegol']['rps']:>9.4f}{m['base_rate']['rps']:>9.4f}"
            f"{r['paired_rps_diff']:>+9.4f}{r['we_win']:>6}/{r['matches']}"
        )

    if args.pooled_out and full:
        print(
            f"\npooled {pooled_row['seasons']} ({pooled_row['matches']} matches): "
            f"{pooled_row['paired_rps_diff']:+.4f} "
            f"[{pooled_row['ci_low']:+.4f}, {pooled_row['ci_high']:+.4f}], "
            f"better in {pooled_row['better_in_n_of_m_seasons']} seasons"
        )

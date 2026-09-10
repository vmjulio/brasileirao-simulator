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


def compare(season: int, source: str) -> dict:
    theirs_by_match = load_theirs(source)

    season_data = SeasonData(season)
    tables = Tables(season_data)
    played = assign_horizon0(played_matches(season), season_data.dates)
    forecasts = {
        date: analytic_forecasts_for_date(season, date, tables=tables)
        for date in season_data.dates
    }
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
    rng = np.random.default_rng(7)
    means = [rng.choice(paired, len(paired), replace=True).mean() for _ in range(BOOTSTRAP_DRAWS)]

    return {
        "season": season,
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
    args = parser.parse_args()

    results = [
        compare(int(season), args.source_template.format(season=season))
        for season in args.seasons.split(",")
    ]
    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)

    print(f"{'season':>7}{'n':>6}{'ours':>9}{'theirs':>9}{'base':>9}{'diff':>9}{'we win':>9}")
    for r in results:
        m = r["models"]
        print(
            f"{r['season']:>7}{r['matches']:>6}{m['ours']['rps']:>9.4f}"
            f"{m['chancedegol']['rps']:>9.4f}{m['base_rate']['rps']:>9.4f}"
            f"{r['paired_rps_diff']:>+9.4f}{r['we_win']:>6}/{r['matches']}"
        )

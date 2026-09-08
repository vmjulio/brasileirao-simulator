"""Score a simulator's forecasts against a completed season's real outcome.

C2's whole premise is that letting each simulated season draw its own team
strengths (see domain/parameter_uncertainty.py) produces BETTER forecasts than
the fixed-lambda model, not merely different ones. "Better" is checkable only
against a season whose true result is already known - 2025 is complete, 2026
is not - which is why this always backtests a finished season and never the
season in progress.

For each of the season's backfill dates, this replays the season "as of" that
date with a chosen simulator, reads off every team's title and relegation
probability, and compares those forecasts to what the real final table says
actually happened. calibration_curve and brier_score are the two ways that
comparison is scored: the curve shows whether events forecast at roughly X%
happened roughly X% of the time; the Brier score collapses that into one
number a lower-is-better comparison can rank simulators by.

Every backtest run is written through PickleAdapter to a scratch directory
under `files/pkl_calibration/`, never to `files/pkl/` - the real, gitignored,
unrecoverable simulation history lives there, and `load_results=False` is used
throughout so nothing accumulates across runs of this script.
"""

import argparse
import shutil
from dataclasses import dataclass
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.pickle_adapter import PickleAdapter
from brasileirao_simulator.adapters.uncertain_params_adapter import UncertainParamsAdapter
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.simulators import simulator_for
from brasileirao_simulator.service_layer.simulation_service import SimulationService


SCRATCH_ROOT = "files/pkl_calibration"

# team_match_counts.sql caps its window at 19 real matches per venue - the
# same cap team_params_same_venue_average.sql's lookback uses. "The full
# window" means this number, not a value chosen for this script.
FULL_WINDOW_MATCHES = 19


# ---------------------------------------------------------------------------
# Metrics - pure functions of (forecasts, outcomes), no simulator involved.
# ---------------------------------------------------------------------------


def brier_score(forecasts, outcomes) -> float:
    """Mean squared error between a forecast probability and its 0/1 outcome.

    The standard proper scoring rule for probabilistic forecasts: lower is
    better, 0 is a certain-and-correct forecast, 1 is certain-and-wrong.
    Unlike accuracy it rewards HONEST uncertainty - a forecast of 0.5 for a
    coin flip scores better than a confident 0.9 that is wrong half the time.
    """
    forecasts = np.asarray(forecasts, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    if forecasts.shape != outcomes.shape:
        raise ValueError(
            f"forecasts and outcomes must be the same length "
            f"({forecasts.shape} vs {outcomes.shape})"
        )
    return float(np.mean((forecasts - outcomes) ** 2))


def calibration_curve(forecasts, outcomes, bins: int = 10) -> pd.DataFrame:
    """Bucket forecasts into `bins` equal-width bins over [0, 1] and compare
    each bucket's mean forecast to how often the event actually happened.

    A well-calibrated model's rows land on the diagonal (forecast_mean ==
    observed_frequency); a systematically overconfident one does not. Every
    bin is returned even when empty (count == 0, means NaN) so the bin edges
    are the same shape regardless of what data happened to land where -
    a caller comparing two curves side by side needs that alignment.
    """
    forecasts = np.asarray(forecasts, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    if forecasts.shape != outcomes.shape:
        raise ValueError(
            f"forecasts and outcomes must be the same length "
            f"({forecasts.shape} vs {outcomes.shape})"
        )

    edges = np.linspace(0.0, 1.0, bins + 1)
    # searchsorted with side="right" puts a forecast of exactly an interior
    # edge (e.g. 0.3 with edges at ...0.2, 0.3, 0.4...) into the bin ABOVE
    # it, matching the half-open [low, high) convention below; the final bin
    # is closed on the right so a forecast of exactly 1.0 is not pushed past
    # the last bin.
    bin_index = np.searchsorted(edges[1:-1], forecasts, side="right")
    bin_index = np.clip(bin_index, 0, bins - 1)

    rows = []
    for b in range(bins):
        mask = bin_index == b
        count = int(mask.sum())
        rows.append(
            {
                "bin_low": edges[b],
                "bin_high": edges[b + 1],
                "count": count,
                "forecast_mean": float(forecasts[mask].mean()) if count else float("nan"),
                "observed_frequency": float(outcomes[mask].mean()) if count else float("nan"),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# The real 2025 outcome, derived from completed fixtures - never simulated.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeasonOutcome:
    teams: list
    champion: str
    relegated: set


def true_outcomes(season: int) -> SeasonOutcome:
    """The real champion and the four real relegated sides, read off the
    completed season's own results - the ground truth every forecast in this
    module is scored against.

    Reuses standings.sql, the exact query the simulators' own standings come
    from, so "actually happened" and "simulated outcome" are ranked by
    identical tie-break rules.
    """
    tables = Tables(SeasonData(season))
    fixtures = tables.enriched_tidy_fixtures()  # no blanking: the whole real season

    con = duckdb.connect()
    con.register("enriched_tidy_fixtures", fixtures)
    standings = con.sql(Queries(season).standings()).df()

    if standings["rank_"].max() < 20 or standings["g"].min() < 38:
        raise ValueError(
            f"season {season} does not look complete "
            f"(max rank {standings['rank_'].max()}, min games played {standings['g'].min()}); "
            "a calibration backtest needs the true final table, which only exists "
            "for a finished season"
        )

    champion = standings.loc[standings["rank_"] == 1, "team_name"].iloc[0]
    relegated = set(standings.loc[standings["rank_"] >= 17, "team_name"])
    teams = sorted(standings["team_name"].tolist())
    return SeasonOutcome(teams=teams, champion=champion, relegated=relegated)


# ---------------------------------------------------------------------------
# Replaying the season with one simulator configuration.
# ---------------------------------------------------------------------------


def full_window_n_eff_scale(season: int, tables: Tables, date: str) -> float:
    """The n_eff_scale that pushes every team's evidence up to the 19-match
    cap, for one as-of date.

    team_match_counts.sql already caps at 19, and an established side's
    lookback reaches into the previous season, so established teams sit at
    the cap (n_eff = 19) all season - only the newly promoted sides fall
    short. A single scalar cannot set every team's n_eff to exactly 19 (the
    adapter multiplies match_count by one shared scale, and match_count
    itself varies by team), so this uses the THINNEST team's count for the
    date: scale = 19 / min(match_count). That lifts exactly the teams this
    variant exists to test up to the cap; established teams, already at 19,
    end up very slightly ABOVE it (19 * scale) rather than left alone - a
    small overshoot in the direction of MORE certainty, which if anything
    argues against the "ignore real evidence" hypothesis this variant is
    testing, not for it. As the season goes on and promoted teams accumulate
    their own 19 real matches, min(match_count) reaches 19 and scale
    converges to exactly 1.0.
    """
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=date)
    con = duckdb.connect()
    con.register("new_fixtures", fixtures)
    match_counts = con.sql(Queries(season).team_match_counts()).df()

    nonzero = match_counts.loc[match_counts["match_count"] > 0, "match_count"]
    if nonzero.empty:
        # No team has played a lookback-window match yet (the very first
        # date or two). draw_team_rates already treats n_eff == 0 as "no
        # distribution to draw from, repeat the fixed estimate unchanged" -
        # any scale is moot, so 1.0 is as good as any other number here.
        return 1.0
    return FULL_WINDOW_MATCHES / float(nonzero.min())


def _build_simulator(
    name: str,
    strategy: str,
    season: int,
    n_eff_scale: float,
    rng: Optional[np.random.Generator],
):
    """UncertainParamsAdapter needs a per-date n_eff_scale and an injectable
    rng that simulators.simulator_for's fixed (name, strategy, season)
    signature has no room for, so it is built directly here instead - the
    same class simulator_for would have chosen, just constructed by hand.
    loop/batch are unaffected by n_eff_scale and go through simulator_for
    exactly as every other entrypoint uses it.
    """
    if name == "uncertain":
        return UncertainParamsAdapter(strategy, season, rng=rng, n_eff_scale=n_eff_scale)
    if name == "batch":
        return IterationBatchAdapter(strategy, season, rng=rng)
    return simulator_for(name, strategy, season)


def _variant_label(simulator: str, n_eff_scale: float, full_window: bool) -> str:
    if simulator != "uncertain":
        return simulator
    if full_window:
        return "uncertain_full_window"
    return f"uncertain_scale_{n_eff_scale}"


def collect_forecasts(
    season: int,
    simulator: str,
    iterations: int,
    scratch_root: str = SCRATCH_ROOT,
    n_eff_scale: float = 1.0,
    full_window: bool = False,
    rng: Optional[np.random.Generator] = None,
    dates: Optional[list] = None,
) -> pd.DataFrame:
    """Replay `season` date by date with `simulator`, and return one row per
    (date, team): the forecast title/relegation probability as of that date,
    and whether that team actually won the title / was actually relegated.

    Every batch is one shot (max_batch_size == iterations): SimulationRunner
    otherwise chunks a run and re-writes the scratch pickle after every chunk,
    which buys nothing here since load_results=False means nothing is ever
    resumed from disk.
    """
    outcome = true_outcomes(season)
    if dates is None:
        dates = backfill_dates(season)

    variant_dir = f"{scratch_root}/{_variant_label(simulator, n_eff_scale, full_window)}"
    persistence = PickleAdapter(variant_dir, season)
    tables = Tables(SeasonData(season))
    strategy = SimulationParams(season=season).strategy

    rows = []
    for date in dates:
        date_scale = (
            full_window_n_eff_scale(season, tables, date) if full_window else n_eff_scale
        )
        simulator_adapter = _build_simulator(simulator, strategy, season, date_scale, rng)

        params = SimulationParams(
            season=season,
            iterations=iterations,
            max_batch_size=iterations,
            ignore_results_after=date,
            load_results=False,
        )
        SimulationService(
            persistence_adapter=persistence,
            simulator_adapter=simulator_adapter,
            params=params,
        ).run_simulation(print_results=False)

        results = persistence.load_results(strategy=strategy, suffix=date)
        title_counts = results["brasileirao_title"]
        relegation_counts = results["brasileirao_relegation"]

        for team in outcome.teams:
            rows.append(
                {
                    "date": date,
                    "team": team,
                    "title_prob": title_counts.get(team, 0) / iterations,
                    "title_outcome": float(team == outcome.champion),
                    "relegation_prob": relegation_counts.get(team, 0) / iterations,
                    "relegation_outcome": float(team in outcome.relegated),
                    "n_eff_scale": date_scale,
                }
            )

    return pd.DataFrame(rows)


@dataclass(frozen=True)
class BacktestResult:
    forecasts: pd.DataFrame
    title_curve: pd.DataFrame
    relegation_curve: pd.DataFrame
    title_brier: float
    relegation_brier: float
    combined_brier: float


def calibration_backtest(
    season: int,
    simulator: str,
    iterations: int = 20_000,
    scratch_root: str = SCRATCH_ROOT,
    n_eff_scale: float = 1.0,
    full_window: bool = False,
    bins: int = 10,
    rng: Optional[np.random.Generator] = None,
    dates: Optional[list] = None,
) -> BacktestResult:
    forecasts = collect_forecasts(
        season=season,
        simulator=simulator,
        iterations=iterations,
        scratch_root=scratch_root,
        n_eff_scale=n_eff_scale,
        full_window=full_window,
        rng=rng,
        dates=dates,
    )

    combined_forecasts = pd.concat(
        [forecasts["title_prob"], forecasts["relegation_prob"]], ignore_index=True
    )
    combined_outcomes = pd.concat(
        [forecasts["title_outcome"], forecasts["relegation_outcome"]], ignore_index=True
    )

    return BacktestResult(
        forecasts=forecasts,
        title_curve=calibration_curve(forecasts["title_prob"], forecasts["title_outcome"], bins=bins),
        relegation_curve=calibration_curve(
            forecasts["relegation_prob"], forecasts["relegation_outcome"], bins=bins
        ),
        title_brier=brier_score(forecasts["title_prob"], forecasts["title_outcome"]),
        relegation_brier=brier_score(forecasts["relegation_prob"], forecasts["relegation_outcome"]),
        combined_brier=brier_score(combined_forecasts, combined_outcomes),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--simulator",
        choices=["loop", "batch", "uncertain"],
        default="batch",
        help="batch is the fixed-lambda baseline (statistically identical to "
        "loop, far faster at the iteration counts this backtest needs).",
    )
    parser.add_argument("--iterations", type=int, default=20_000)
    parser.add_argument(
        "--n-eff-scale",
        type=float,
        default=1.0,
        help="uncertain only: spread each team's Gamma draw from n_eff = "
        "match_count * this scale.",
    )
    parser.add_argument(
        "--full-window",
        action="store_true",
        help="uncertain only: overrides --n-eff-scale with, per date, the "
        "scale that pushes every team's n_eff up to the 19-match cap.",
    )
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--scratch-root", default=SCRATCH_ROOT)
    parser.add_argument(
        "--keep-scratch",
        action="store_true",
        help="keep the scratch pickle directory instead of deleting it on exit.",
    )
    args = parser.parse_args()

    result = calibration_backtest(
        season=args.season,
        simulator=args.simulator,
        iterations=args.iterations,
        scratch_root=args.scratch_root,
        n_eff_scale=args.n_eff_scale,
        full_window=args.full_window,
        bins=args.bins,
    )

    label = _variant_label(args.simulator, args.n_eff_scale, args.full_window)
    print(f"variant: {label}  season: {args.season}  iterations/date: {args.iterations}")
    print(f"title brier:      {result.title_brier:.5f}")
    print(f"relegation brier: {result.relegation_brier:.5f}")
    print(f"combined brier:   {result.combined_brier:.5f}")
    print()
    print("title calibration curve:")
    print(result.title_curve.to_string(index=False))
    print()
    print("relegation calibration curve:")
    print(result.relegation_curve.to_string(index=False))

    if not args.keep_scratch:
        shutil.rmtree(f"{args.scratch_root}/{label}", ignore_errors=True)

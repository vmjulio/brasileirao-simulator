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
from brasileirao_simulator.domain.batch_simulation import FULL_WINDOW_MATCHES
from brasileirao_simulator.domain.queries import Queries
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.simulation_params import SimulationParams
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.simulators import simulator_for
from brasileirao_simulator.service_layer.simulation_service import SimulationService


SCRATCH_ROOT = "files/pkl_calibration"


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


def _build_simulator(
    name: str,
    strategy: str,
    season: int,
    n_eff_scale: float,
    n_eff_override: Optional[float],
    rng: Optional[np.random.Generator],
):
    """UncertainParamsAdapter needs a per-date rng and an injectable
    n_eff_scale/n_eff_override that simulators.simulator_for's fixed
    (name, strategy, season) signature has no room for, so it is built
    directly here instead - the same class simulator_for would have chosen,
    just constructed by hand. loop is unaffected by either and goes through
    simulator_for exactly as every other entrypoint uses it.

    n_eff_override, when given (the --full-window variant), is a single
    constant (FULL_WINDOW_MATCHES) computed once by the caller - unlike the
    old per-date scalar this replaces, it needs no per-date recomputation,
    because it no longer depends on any team's real match count.
    """
    if name == "uncertain":
        return UncertainParamsAdapter(
            strategy, season, rng=rng, n_eff_scale=n_eff_scale, n_eff_override=n_eff_override
        )
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
    seed: int = 0,
    dates: Optional[list] = None,
) -> pd.DataFrame:
    """Replay `season` date by date with `simulator`, and return one row per
    (date, team): the forecast title/relegation probability as of that date,
    and whether that team actually won the title / was actually relegated.

    Every batch is one shot (max_batch_size == iterations): SimulationRunner
    otherwise chunks a run and re-writes the scratch pickle after every chunk,
    which buys nothing here since load_results=False means nothing is ever
    resumed from disk.

    Each date draws from np.random.default_rng(seed + date_index) - common
    random numbers. A caller comparing two variants (e.g. batch vs uncertain)
    at the same seed gives both the identical stream at every date, which
    cancels most of the Monte Carlo noise shared between them and isolates
    the noise in their DIFFERENCE, the quantity a comparison actually cares
    about. Without this the CLI drew from OS entropy and no published Brier
    pair could ever be reproduced.
    """
    outcome = true_outcomes(season)
    if dates is None:
        dates = backfill_dates(season)

    variant_dir = f"{scratch_root}/{_variant_label(simulator, n_eff_scale, full_window)}"
    persistence = PickleAdapter(variant_dir, season)
    strategy = SimulationParams(season=season).strategy
    # See _build_simulator: a single constant, computed once, replaces the
    # old per-date scalar recomputation entirely.
    n_eff_override = FULL_WINDOW_MATCHES if full_window else None

    rows = []
    for date_index, date in enumerate(dates):
        rng = np.random.default_rng(seed + date_index)
        simulator_adapter = _build_simulator(
            simulator, strategy, season, n_eff_scale, n_eff_override, rng
        )

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

        # outcome.teams comes from the completed-season standings;
        # title_counts/relegation_counts are keyed off the blanked baseline.
        # Any drift between those two name spaces would silently forecast
        # 0.0 for every team below and look like a suspiciously GOOD Brier
        # score rather than an obvious failure - so guard it explicitly
        # instead of trusting the join.
        assert sum(title_counts.values()) == iterations, (date, "title counts do not cover the batch")
        assert sum(relegation_counts.values()) == 4 * iterations, (date, "relegation counts incomplete")
        assert set(title_counts) <= set(outcome.teams), (date, "forecast names not in the final table")

        for team in outcome.teams:
            rows.append(
                {
                    "date": date,
                    "team": team,
                    "title_prob": title_counts.get(team, 0) / iterations,
                    "title_outcome": float(team == outcome.champion),
                    "relegation_prob": relegation_counts.get(team, 0) / iterations,
                    "relegation_outcome": float(team in outcome.relegated),
                }
            )

    return pd.DataFrame(rows)


@dataclass(frozen=True)
class BacktestResult:
    """No combined_brier: pooling title_prob and relegation_prob into one
    Brier score weights two different-base-rate events 1:1 arbitrarily (title
    is ~1-in-20, relegation ~4-in-20) and the pooled number has no
    decision-theoretic meaning - nobody acts on "combined" risk. Score title
    and relegation on their own merits instead.
    """

    forecasts: pd.DataFrame
    title_curve: pd.DataFrame
    relegation_curve: pd.DataFrame
    title_brier: float
    relegation_brier: float


def calibration_backtest(
    season: int,
    simulator: str,
    iterations: int = 20_000,
    scratch_root: str = SCRATCH_ROOT,
    n_eff_scale: float = 1.0,
    full_window: bool = False,
    bins: int = 10,
    seed: int = 0,
    dates: Optional[list] = None,
) -> BacktestResult:
    forecasts = collect_forecasts(
        season=season,
        simulator=simulator,
        iterations=iterations,
        scratch_root=scratch_root,
        n_eff_scale=n_eff_scale,
        full_window=full_window,
        seed=seed,
        dates=dates,
    )

    return BacktestResult(
        forecasts=forecasts,
        title_curve=calibration_curve(forecasts["title_prob"], forecasts["title_outcome"], bins=bins),
        relegation_curve=calibration_curve(
            forecasts["relegation_prob"], forecasts["relegation_outcome"], bins=bins
        ),
        title_brier=brier_score(forecasts["title_prob"], forecasts["title_outcome"]),
        relegation_brier=brier_score(forecasts["relegation_prob"], forecasts["relegation_outcome"]),
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
        help="uncertain only: every drawable team's n_eff is set directly to "
        "the 19-match cap (n_eff_override), regardless of its real match "
        "count.",
    )
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--scratch-root", default=SCRATCH_ROOT)
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="rng seed. Each date draws from default_rng(seed + date_index), "
        "so two variants run at the same --seed see identical per-date "
        "streams (common random numbers) and their Brier DIFFERENCE is "
        "reproducible run to run.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="write BacktestResult.forecasts (one row per date/team, with "
        "each team's title/relegation forecast and real outcome) to this "
        "CSV path, so individual claims are auditable instead of discarded.",
    )
    parser.add_argument(
        "--keep-scratch",
        action="store_true",
        help="keep the scratch pickle directory instead of deleting it on exit.",
    )
    args = parser.parse_args()

    label = _variant_label(args.simulator, args.n_eff_scale, args.full_window)
    try:
        result = calibration_backtest(
            season=args.season,
            simulator=args.simulator,
            iterations=args.iterations,
            scratch_root=args.scratch_root,
            n_eff_scale=args.n_eff_scale,
            full_window=args.full_window,
            bins=args.bins,
            seed=args.seed,
        )

        print(
            f"variant: {label}  season: {args.season}  "
            f"iterations/date: {args.iterations}  seed: {args.seed}"
        )
        print(f"title brier:      {result.title_brier:.5f}")
        print(f"relegation brier: {result.relegation_brier:.5f}")
        print()
        print("title calibration curve:")
        print(result.title_curve.to_string(index=False))
        print()
        print("relegation calibration curve:")
        print(result.relegation_curve.to_string(index=False))

        if args.out:
            result.forecasts.to_csv(args.out, index=False)
            print(f"\nforecasts written to {args.out}")
    finally:
        if not args.keep_scratch:
            shutil.rmtree(f"{args.scratch_root}/{label}", ignore_errors=True)

"""Elo scored on any seasons under any settings, paired against the incumbent
and against other Elo settings on identical matches.

WHY ITS OWN HARNESS. The four-arm backtest scores Elo beside two Dixon-Coles
fits that refit at every as-of date; those dominate its run time and never
change when an Elo setting does. This harness scores only Elo and the
incumbent, builds each season's per-date fixture tables and incumbent
forecasts once, and reuses them for every setting - so one more setting costs
one replay plus a pass over the dates.

THE GOAL-DIFFERENCE LINE. Elo's lambdas need a line from rating gap to goal
difference, fitted on one season (`EloLambdaParams.burn_in_season`). Until
elo-line-previous-season the adapter fitted it on 2019 for every season, which
made 2019 and earlier unscorable. A setting's `line_season_for` picks the fit
season per scored season: `previous_season` (the adapter's default now) never
touches the scored season or later, so every season with a predecessor in the
store can be scored; `fixed_2019` reproduces the exports made before the switch.

MATCH SET. As `dixon_coles_backtest.score_season`: every horizon-0 match over
`backfill_dates`, dropped from every arm if any arm lacks a forecast for it
(counted, never scored on some arms only).

    python -m brasileirao_simulator.entrypoints.elo_backtest --ten-seasons
"""

import argparse
import json
from dataclasses import dataclass, field, replace
from typing import Callable, Mapping, Optional

import numpy as np
import pandas as pd

from brasileirao_simulator.adapters.elo_adapter import EloAdapter
from brasileirao_simulator.config.settings import EXPORTS_PATH
from brasileirao_simulator.domain.competitions import STATE_CHAMPIONSHIPS, with_every_copa_round, with_state_championships
from brasileirao_simulator.domain.elo import EloParams
from brasileirao_simulator.domain.elo_lambda import EloLambdaParams
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.backfill import backfill_dates
from brasileirao_simulator.entrypoints.benchmark_chancedegol import ONE_HOT, brier, rps
from brasileirao_simulator.entrypoints.dixon_coles_backtest import elo_forecasts_from_tables
from brasileirao_simulator.entrypoints.match_brier_backtest import OUTCOMES, paired_bootstrap_ci, skill_score
from brasileirao_simulator.entrypoints.variant_sweep import (
    analytic_forecasts_for_date,
    assign_horizon0,
    base_rate_probs,
    played_matches,
)

TEN_SEASONS = tuple(range(2016, 2026))
FIND_SEASONS = tuple(range(2016, 2021))
CONFIRM_SEASONS = tuple(range(2021, 2026))
PARTIAL_SEASONS = (2026,)
INCUMBENT = "current"


def fixed_2019(season: int) -> int:
    """The fit season every Elo export used before elo-line-previous-season."""
    return 2019


def previous_season(season: int) -> int:
    """Fit on the season before the scored one - never the scored season or later."""
    return season - 1


@dataclass(frozen=True)
class EloSetting:
    """One Elo configuration to score: its replay and lambda parameters, and
    which season its goal-difference line is fitted on for each scored season.

    `name` labels its columns (`rps_<name>`) and must be unique within a run
    and differ from `INCUMBENT`. `competitions`, when set, is the rule set of
    the store this setting reads (ratings, goal line and totals alike) in
    place of the run's shared default store."""

    name: str
    elo_params: EloParams = field(default_factory=EloParams)
    lambda_params: EloLambdaParams = field(default_factory=EloLambdaParams)
    line_season_for: Callable[[int], int] = previous_season
    competitions: Optional[Mapping] = None

    def lambda_params_for(self, season: int) -> EloLambdaParams:
        return replace(self.lambda_params, burn_in_season=self.line_season_for(season))


class SeasonInputs:
    """Everything about one season that no Elo setting changes, built once:
    the horizon-0 matches to score, each as-of date's two fixture tables, and
    the incumbent's forecast for every one of those dates."""

    def __init__(self, season: int):
        self.season = season
        season_data = SeasonData(season)
        tables = Tables(season_data)
        self.played = assign_horizon0(played_matches(season), backfill_dates(season))
        # The Brier-skill reference: the season's own home/draw/away shares,
        # as variant_sweep scores its reference (and so the explorer's tile).
        self.reference = np.array(base_rate_probs(played_matches(season)["outcome"]), dtype=float)
        dates = sorted(self.played["as_of_date"].unique())
        self.tables = {
            date: (
                tables.enriched_tidy_fixtures(blank_from_date=date),
                tables.remaining_games(blank_from_date=date),
            )
            for date in dates
        }
        self.incumbent = {date: analytic_forecasts_for_date(season, date, tables=tables) for date in dates}


def elo_forecasts_by_date(inputs: SeasonInputs, setting: EloSetting, store: MatchStore) -> dict:
    """`{as_of_date: {match_key: (p_home, p_draw, p_away)}}` for one setting
    on one season. One adapter per season (its replay and line fit are cached
    per instance); the fixture tables are copied so no adapter can leave a
    mark on the shared inputs the next setting reads."""
    adapter = EloAdapter(
        "average",
        inputs.season,
        match_store=store,
        elo_params=setting.elo_params,
        lambda_params=setting.lambda_params_for(inputs.season),
    )
    return {
        date: elo_forecasts_from_tables(adapter, fixtures.copy(), remaining.copy())[0]
        for date, (fixtures, remaining) in inputs.tables.items()
    }


def score_season(inputs: SeasonInputs, settings: list, store: MatchStore) -> tuple[pd.DataFrame, int]:
    """One row per horizon-0 match forecast by the incumbent and by every
    setting, with each one's per-match RPS as `rps_<name>`. Returns (frame,
    dropped) - `dropped` counts matches some arm had no forecast for.
    `store` serves every setting without its own `competitions`; the others
    get one store per distinct rule set."""
    stores = {}

    def store_for(setting):
        if setting.competitions is None:
            return store
        key = tuple(sorted(setting.competitions.items()))
        if key not in stores:
            stores[key] = MatchStore(rules=dict(setting.competitions))
        return stores[key]

    by_setting = {s.name: elo_forecasts_by_date(inputs, s, store_for(s)) for s in settings}
    rows, dropped = [], 0
    for match in inputs.played.itertuples(index=False):
        forecasts = {INCUMBENT: inputs.incumbent.get(match.as_of_date, {}).get(match.match_key)}
        for name, by_date in by_setting.items():
            forecasts[name] = by_date.get(match.as_of_date, {}).get(match.match_key)
        if any(p is None for p in forecasts.values()):
            dropped += 1
            continue
        row = {"season": inputs.season, "match_key": match.match_key,
               "as_of_date": match.as_of_date, "outcome": match.outcome}
        for name, p in forecasts.items():
            row.update({f"p_{o}_{name}": v for o, v in zip(OUTCOMES, p)})
        rows.append(row)
    frame = pd.DataFrame(rows)
    outcomes = np.array([ONE_HOT[o] for o in frame["outcome"]], dtype=float)
    for name in [INCUMBENT] + [s.name for s in settings]:
        probabilities = frame[[f"p_{o}_{name}" for o in OUTCOMES]].to_numpy()
        frame[f"rps_{name}"] = rps(probabilities, outcomes)
        frame[f"brier_{name}"] = brier(probabilities, outcomes)
    frame["brier_reference"] = brier(np.tile(inputs.reference, (len(frame), 1)), outcomes)
    return frame, dropped


def compare(frame: pd.DataFrame, a: str, b: str, seasons, seed: int = 7) -> dict:
    """`a` minus `b` in RPS on `seasons` (negative: `a` better): per season,
    pooled flat over matches with a paired bootstrap, how many seasons `a`
    won, and the find/confirm halves of `TEN_SEASONS` where both overlap."""
    subset = frame[frame["season"].isin(seasons)]
    diff = (subset[f"rps_{a}"] - subset[f"rps_{b}"]).to_numpy()
    mean, lo, hi = paired_bootstrap_ci(diff, seed=seed)
    per_season = []
    for season in seasons:
        d = diff[(subset["season"] == season).to_numpy()]
        s_mean, s_lo, s_hi = paired_bootstrap_ci(d, seed=seed)
        per_season.append({"season": season, "matches": int(d.size), "diff": s_mean, "ci_low": s_lo, "ci_high": s_hi})
    halves = {}
    for label, half in (("find", FIND_SEASONS), ("confirm", CONFIRM_SEASONS)):
        mask = subset["season"].isin(half).to_numpy()
        if mask.any() and set(half) <= set(seasons):
            h_mean, h_lo, h_hi = paired_bootstrap_ci(diff[mask], seed=seed)
            halves[label] = {"diff": h_mean, "ci_low": h_lo, "ci_high": h_hi}
    return {
        "a": a, "b": b, "seasons": list(seasons), "matches": int(diff.size),
        "diff": mean, "ci_low": lo, "ci_high": hi,
        "a_better_seasons": sum(1 for s in per_season if s["diff"] < 0),
        "per_season": per_season, **halves,
    }


def _reproduce_four_arms(frame: pd.DataFrame, name: str, seasons, reference_csv: str) -> None:
    """Gate: the match set and the incumbent's score must reproduce the
    committed four-arm run season by season. Raises otherwise.

    WHY ELO'S COLUMN IS NO LONGER CHECKED HERE. The four-arm reference was
    produced when the store held continental matches from 2019 only. On
    2026-09-11 the user admitted the Libertadores and Sudamericana seasons
    2014-2018 rebuilt from Wikipedia, and the completed Copa do Brasil 2025;
    every Elo rating from 2014 on moved with them, so `rps_elo` cannot equal
    the old reference and a gate demanding it would only ever be satisfied by
    reverting the data. What the change must NOT touch is the incumbent, which
    reads no `MatchStore` at all, or the set of matches being scored - both
    are still gated exactly, and both still pass. Elo's own continuity is
    covered by `elo_ten_seasons.csv`, re-baselined in the same commit.
    """
    reference = pd.read_csv(reference_csv).set_index("season")
    for season in seasons:
        got = frame[frame["season"] == season]
        expected = reference.loc[season]
        checks = {
            "matches": (len(got), int(expected["matches"])),
            "rps_current": (got[f"rps_{INCUMBENT}"].mean(), expected["rps_current"]),
        }
        for label, (actual, want) in checks.items():
            if abs(actual - want) > 1e-9:
                raise AssertionError(f"{season} {label}: harness {actual!r} vs four-arm {want!r}")
    print(f"gate: the match set and the incumbent reproduce {reference_csv} on {list(seasons)}", flush=True)


def run_ten_seasons(out_dir: str = EXPORTS_PATH, report_out: str = "../docs/superpowers/elo-ten-seasons-report.md") -> dict:
    """The elo-ten-seasons ticket: Elo with the previous-season line on
    2016-2025 (and 2026 partial), the shipped fixed-2019 line on 2020-2026,
    both against the incumbent, and each against the other where both exist."""
    store = MatchStore()
    rolling = EloSetting("elo")
    fixed = EloSetting("elo_fixed", line_season_for=fixed_2019)
    frames, dropped = [], {}
    for season in TEN_SEASONS + PARTIAL_SEASONS:
        inputs = SeasonInputs(season)
        settings = [rolling, fixed] if season >= 2020 else [rolling]
        frame, dropped[season] = score_season(inputs, settings, store)
        frames.append(frame)
        print(f"{season}: {len(frame)} matches, dropped {dropped[season]}", flush=True)
    matches = pd.concat(frames, ignore_index=True)

    four_arm_seasons = tuple(range(2020, 2026))
    # Full seasons only: 2026 is still being played, so its match set has
    # grown since the four-arm run and is not a fixed reference.
    _reproduce_four_arms(matches, fixed.name, four_arm_seasons, f"{EXPORTS_PATH}/four_arms.csv")

    results = {
        "elo_vs_incumbent": compare(matches, "elo", INCUMBENT, TEN_SEASONS),
        "elo_vs_incumbent_2020_2025": compare(matches, "elo", INCUMBENT, four_arm_seasons),
        "fixed_vs_incumbent_2020_2025": compare(matches, "elo_fixed", INCUMBENT, four_arm_seasons),
        "rolling_vs_fixed_2020_2025": compare(matches, "elo", "elo_fixed", four_arm_seasons),
        "partial_2026": compare(matches, "elo", INCUMBENT, PARTIAL_SEASONS),
        "dropped": dropped,
        "brier_skill": _brier_skill(matches, ("elo", INCUMBENT), TEN_SEASONS),
    }

    per_season = []
    for season in TEN_SEASONS + PARTIAL_SEASONS:
        got = matches[matches["season"] == season]
        row = next(r for c in (results["elo_vs_incumbent"], results["partial_2026"])
                   for r in c["per_season"] if r["season"] == season)
        per_season.append({
            "season": season, "partial": season in PARTIAL_SEASONS, "matches": len(got),
            "rps_elo": got["rps_elo"].mean(), "rps_current": got[f"rps_{INCUMBENT}"].mean(),
            "rps_elo_fixed": got["rps_elo_fixed"].mean(),  # NaN before 2020: not scorable
            "diff_elo_vs_current": row["diff"], "ci_low": row["ci_low"], "ci_high": row["ci_high"],
            "line_fitted_on": previous_season(season),
        })
    pd.DataFrame(per_season).to_csv(f"{out_dir}/elo_ten_seasons.csv", index=False)
    with open(f"{out_dir}/elo_ten_seasons_pooled.json", "w") as f:
        json.dump({k: v for k, v in results.items()}, f, indent=1, default=float)
    _write_ten_seasons_report(results, per_season, report_out)
    return results


def _brier_skill(frame: pd.DataFrame, names, seasons) -> dict:
    """Pooled Brier skill over the base-rate reference on `seasons`, per
    arm: 1 - mean Brier / mean reference Brier - the explorer's skill tile."""
    subset = frame[frame["season"].isin(seasons)]
    reference = float(subset["brier_reference"].mean())
    return {name: skill_score(float(subset[f"brier_{name}"].mean()), reference) for name in names}


def _ci(c: dict) -> str:
    return f"{c['diff']:+.5f} [{c['ci_low']:+.5f}, {c['ci_high']:+.5f}]"


def _chancedegol_section(exports: str = EXPORTS_PATH) -> list:
    """Report lines for Elo against chancedegol, read from the committed
    `benchmark_elo_ten*.json` (written by `benchmark_chancedegol.py --model
    elo`, a separate run); empty if that export does not exist yet."""
    try:
        with open(f"{exports}/benchmark_elo_ten.json") as f:
            rows = json.load(f)
        with open(f"{exports}/benchmark_elo_ten_pooled.json") as f:
            pooled = json.load(f)
    except FileNotFoundError:
        return []
    lines = ["## Against chancedegol (2016-2025)", "",
             "Same Elo, scored on chancedegol's own matches with",
             "`benchmark_chancedegol.py --model elo`. Negative means Elo is better.", "",
             "| season | matches | RPS Elo | RPS chancedegol | Elo − chancedegol | 95% CI |",
             "|---|---:|---:|---:|---:|---|"]
    for r in rows:
        label = f"{r['season']}{' (partial)' if r['season'] in PARTIAL_SEASONS else ''}"
        lines.append(f"| {label} | {r['matches']} | {r['models']['ours']['rps']:.4f} | "
                     f"{r['models']['chancedegol']['rps']:.4f} | {r['paired_rps_diff']:+.4f} | "
                     f"[{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] |")
    lines += ["", f"**Pooled 2016-2025, {pooled['matches']:,} matches: {pooled['paired_rps_diff']:+.5f} "
              f"[{pooled['ci_low']:+.5f}, {pooled['ci_high']:+.5f}]; Elo better in "
              f"{pooled['better_in_n_of_m_seasons']} seasons.**"]
    try:
        with open(f"{exports}/benchmark_pooled.json") as f:
            incumbent = json.load(f)
        wins = incumbent["better_in_n_of_m_seasons"].split("/")[0]
        lines.append(f"For contrast, the incumbent against the same forecaster: {incumbent['paired_rps_diff']:+.5f} "
                     f"[{incumbent['ci_low']:+.5f}, {incumbent['ci_high']:+.5f}], better in {wins} of 10.")
    except FileNotFoundError:
        pass
    return lines + [""]


def _write_ten_seasons_report(results: dict, per_season: list, path: str) -> None:
    ten = results["elo_vs_incumbent"]
    lines = [
        "# elo-ten-seasons report", "",
        "Elo with the goal-difference line fitted on the season before each scored",
        "season, against the incumbent, horizon 0, identical matches. Negative means",
        "Elo is better. Produced by `entrypoints/elo_backtest.py --ten-seasons`.", "",
        "## Gate", "",
        "The shipped fixed-2019 line, run through this harness, reproduces the committed",
        "four-arm `rps_elo`, `rps_current` and match counts on 2020-2025 to 1e-9.", "",
        "## The scheme change on its own (2020-2025)", "",
        f"- fixed-2019 Elo vs incumbent: {_ci(results['fixed_vs_incumbent_2020_2025'])}, "
        f"better in {results['fixed_vs_incumbent_2020_2025']['a_better_seasons']} of 6",
        f"- previous-season Elo vs incumbent: {_ci(results['elo_vs_incumbent_2020_2025'])}, "
        f"better in {results['elo_vs_incumbent_2020_2025']['a_better_seasons']} of 6",
        f"- previous-season minus fixed-2019: {_ci(results['rolling_vs_fixed_2020_2025'])}, "
        f"previous-season better in {results['rolling_vs_fixed_2020_2025']['a_better_seasons']} of 6", "",
        "## Ten seasons (2016-2025)", "",
        "| season | matches | line fitted on | RPS Elo | RPS incumbent | Elo − incumbent | 95% CI |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in per_season:
        label = f"{r['season']}{' (partial)' if r['partial'] else ''}"
        lines.append(f"| {label} | {r['matches']} | {r['line_fitted_on']} | {r['rps_elo']:.4f} | {r['rps_current']:.4f} | "
                     f"{r['diff_elo_vs_current']:+.4f} | [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] |")
    lines += [
        "",
        f"**Pooled 2016-2025, {ten['matches']:,} matches: {_ci(ten)}; Elo better in "
        f"{ten['a_better_seasons']} of 10 seasons.** Find half (2016-2020): {_ci(ten['find'])}; "
        f"confirm half (2021-2025): {_ci(ten['confirm'])}.", "",
        *_chancedegol_section(),
        "## Caveat", "",
        "Before 2019 the store has no Libertadores or Sudamericana, and before 2016 no",
        "Copa do Brasil, so the ratings behind 2016-2019 forecasts come from Série A and",
        "Série B (plus the cup from 2016). The pre-2019 arm is narrower than the one",
        "measured on 2020-2025. Dropped matches per season: "
        + ", ".join(f"{s}: {n}" for s, n in results["dropped"].items()) + ".",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"report written to {path}")


DEFAULT = "default"
CONTINENTAL = (11, 13)
COPA_DO_BRASIL = 73
SERIE_B = 72


def _seeds_for_gap(gap: float) -> dict:
    """The division seeds spaced `gap` apart from Série A's 1500, with
    foreign clubs halfway between Série A and B. `gap=100` is the default."""
    return {1: 1500.0, 2: 1500.0 - gap, 3: 1500.0 - 2 * gap, "foreign": 1500.0 - gap / 2}


def sweep_grid() -> list:
    """`[(knob, level, EloSetting)]` - the elo-sweeps grid, one knob away
    from the default at a time. The default itself comes first. Built fresh
    in every worker: `EloParams` holds mapping proxies, which do not pickle."""
    grid = [(DEFAULT, "default", EloSetting(DEFAULT))]

    def add(knob, level, **elo):
        lam = elo.pop("lambda_params", EloLambdaParams())
        grid.append((knob, str(level), EloSetting(f"{knob}={level}", elo_params=EloParams(**elo), lambda_params=lam)))

    for k in (10, 15, 25, 30, 40):
        add("k", k, k=float(k))
    for h in (50, 70, 100, 120):
        add("home_advantage", h, home_advantage=float(h), lambda_params=EloLambdaParams(home_advantage=float(h)))
    for label, ladder in (("flat 1/1/1", (1.0, 1.0, 1.0)), ("mild 1/1.5/2", (1.0, 1.5, 2.0)), ("steep 1/2/3", (1.0, 2.0, 3.0))):
        add("margin_ladder", label, margin_ladder=ladder)
    for gap in (50, 150):
        add("seed_gap", gap, seeds=_seeds_for_gap(gap))
    for fraction in (0.1, 0.2, 0.33):
        add("season_regression", fraction, season_regression=fraction)
    for w in (0.5, 1.5):
        add("continental_weight", w, competition_weight={league: w for league in CONTINENTAL})
    for w in (0.5, 1.5):
        add("copa_weight", w, competition_weight={COPA_DO_BRASIL: w})
    add("serie_b_weight", 0.5, competition_weight={SERIE_B: 0.5})
    for days in (180, 730):
        grid.append(("totals_window", str(days), EloSetting(
            f"totals_window={days}", lambda_params=EloLambdaParams(totals_window_days=days))))
    return grid


def sudeste_grid() -> list:
    """elo-sudeste-gap: the default plus four levels of `sudeste_gap`."""
    grid = [(DEFAULT, "default", EloSetting(DEFAULT))]
    for points in (25, 50, 75, 100):
        grid.append(("sudeste_gap", str(points), EloSetting(
            f"sudeste_gap={points}", lambda_params=EloLambdaParams(sudeste_gap=float(points)))))
    return grid


STATE_DATA_SEASONS = tuple(range(2020, 2026))  # API-Football has state championships from 2020


def state_grid() -> list:
    """state-championships: the default plus the four arms fixed on the
    board. Each arm reads its own store."""
    state = with_state_championships()
    grid = [(DEFAULT, "default", EloSetting(DEFAULT))]
    grid.append(("copa-all-rounds", "", EloSetting("copa-all-rounds", competitions=with_every_copa_round())))
    grid.append(("state-1.0", "", EloSetting("state-1.0", competitions=state)))
    grid.append(("state-0.5", "", EloSetting(
        "state-0.5", competitions=state,
        elo_params=EloParams(competition_weight={league: 0.5 for league in STATE_CHAMPIONSHIPS}))))
    grid.append(("all-1.0", "", EloSetting("all-1.0", competitions=with_state_championships(with_every_copa_round()))))
    return grid


GRIDS = {"sweep": sweep_grid, "sudeste": sudeste_grid, "state": state_grid}


def run_state_championships(workers: int = 5, out_dir: str = EXPORTS_PATH,
                            report_out: str = "../docs/superpowers/state-championships-report.md") -> pd.DataFrame:
    """The state-championships ticket, with the pass rules fixed on the board:
    `copa-all-rounds` on 2016-2025 (8 of 10, interval clear of zero); state
    arms on 2020-2025 (5 of 6, interval clear of zero), after a gate that they
    equal the default on 2016-2019, before any state data exists."""
    import multiprocessing as mp

    with mp.get_context("spawn").Pool(workers) as pool:
        matches = pd.concat(pool.starmap(_sweep_one_season, [(s, "state") for s in TEN_SEASONS]), ignore_index=True)

    reference = pd.read_csv(f"{EXPORTS_PATH}/elo_ten_seasons.csv").set_index("season")
    for season in TEN_SEASONS:
        got = matches[matches["season"] == season]
        if abs(got[f"rps_{DEFAULT}"].mean() - reference.loc[season, "rps_elo"]) > 1e-9:
            raise AssertionError(f"{season}: default {got[f'rps_{DEFAULT}'].mean()!r} vs elo_ten_seasons")
    early = matches[~matches["season"].isin(STATE_DATA_SEASONS)]
    for arm in ("state-1.0", "state-0.5"):
        gap = (early[f"rps_{arm}"] - early[f"rps_{DEFAULT}"]).abs().max()
        if gap > 1e-12:
            raise AssertionError(f"{arm} differs from the default before 2020 (max {gap!r})")
    print("gates: default reproduces elo_ten_seasons.csv; state arms equal it on 2016-2019", flush=True)

    rows = []
    for knob, _, setting in state_grid():
        if knob == DEFAULT:
            continue
        seasons, needed = (TEN_SEASONS, 8) if knob == "copa-all-rounds" else (STATE_DATA_SEASONS, 5)
        c = compare(matches, setting.name, DEFAULT, seasons)
        ten = compare(matches, setting.name, DEFAULT, TEN_SEASONS)
        rows.append({
            "arm": knob, "tested_on": f"{seasons[0]}-{seasons[-1]}", "matches": c["matches"],
            "diff_vs_default": c["diff"], "ci_low": c["ci_low"], "ci_high": c["ci_high"],
            "better_seasons": c["a_better_seasons"], "seasons": len(seasons), "needed": needed,
            "passes": bool(c["a_better_seasons"] >= needed and c["ci_high"] < 0),
            "ten_season_diff": ten["diff"],
            "per_season": {s["season"]: round(s["diff"], 5) for s in c["per_season"]},
        })
    results = pd.DataFrame(rows)
    results.to_csv(f"{out_dir}/elo_state_championships.csv", index=False)
    lines = [
        "# state-championships report", "",
        "Default Elo against four arms that add matches to the store (ratings, goal line and",
        "totals all read it), on identical horizon-0 Série A matches. Negative favours the arm.",
        "Produced by `entrypoints/elo_backtest.py --state`.", "",
        "**Pass rules, fixed on the board before any data was pulled:** `copa-all-rounds` better",
        "in 8 of 10 seasons (2016-2025) and a pooled 95% interval clear of zero; state arms",
        "better in 5 of 6 (2020-2025) and a pooled interval over 2020-2025 clear of zero.",
        "Gates: the default reproduces `elo_ten_seasons.csv`; the state arms equal it on 2016-2019.", "",
        "| arm | tested on | − default | 95% CI | better in | verdict |", "|---|---|---:|---|---:|---|",
    ]
    for r in results.itertuples():
        lines.append(f"| {r.arm} | {r.tested_on} | {r.diff_vs_default:+.5f} | [{r.ci_low:+.5f}, {r.ci_high:+.5f}] | "
                     f"{r.better_seasons}/{r.seasons} | {'**passes**' if r.passes else 'does not pass'} |")
    lines += ["", "Per season (arm − default):", ""]
    lines += [f"- {r.arm}: {r.per_season}" for r in results.itertuples()]
    lines += ["", "No default is changed by this ticket."]
    with open(report_out, "w") as f:
        f.write("\n".join(lines) + "\n")
    return results


def _sweep_one_season(season: int, grid: str = "sweep") -> pd.DataFrame:
    """Worker: every setting of `grid` on one season, in its own process with
    its own store (nothing shared across processes but the returned frame)."""
    settings = [s for _, _, s in GRIDS[grid]()]
    store = MatchStore()
    frame, dropped = score_season(SeasonInputs(season), settings, store)
    print(f"{season}: {len(frame)} matches x {len(settings)} settings, dropped {dropped}", flush=True)
    return frame[["season", "match_key", f"rps_{INCUMBENT}"] + [f"rps_{s.name}" for s in settings]]


def run_sweeps(workers: int = 5, out_dir: str = EXPORTS_PATH,
               report_out: str = "../docs/superpowers/elo-sweeps-report.md", grid: str = "sweep",
               out_stem: str = "elo_sweeps") -> pd.DataFrame:
    """The elo-sweeps ticket: every grid level against the default on
    `TEN_SEASONS`, with the pass rule fixed in the ticket - better in at
    least 8 of 10 seasons AND a pooled interval clear of zero."""
    import multiprocessing as mp

    with mp.get_context("spawn").Pool(workers) as pool:
        matches = pd.concat(pool.starmap(_sweep_one_season, [(s, grid) for s in TEN_SEASONS]), ignore_index=True)

    # Gate: the default here is the elo-ten-seasons Elo; it must reproduce it.
    reference = pd.read_csv(f"{EXPORTS_PATH}/elo_ten_seasons.csv").set_index("season")
    for season in TEN_SEASONS:
        got = matches[matches["season"] == season]
        for column, want in ((f"rps_{DEFAULT}", reference.loc[season, "rps_elo"]),
                             (f"rps_{INCUMBENT}", reference.loc[season, "rps_current"])):
            if abs(got[column].mean() - want) > 1e-9:
                raise AssertionError(f"{season} {column}: sweep {got[column].mean()!r} vs elo_ten_seasons {want!r}")
    print("gate: the sweep's default reproduces elo_ten_seasons.csv on every season", flush=True)

    rows, per_season = [], []
    for knob, level, setting in GRIDS[grid]():
        if knob == DEFAULT:
            continue
        c = compare(matches, setting.name, DEFAULT, TEN_SEASONS)
        rows.append({
            "knob": knob, "level": level, "matches": c["matches"],
            "rps": matches[f"rps_{setting.name}"].mean(), "rps_default": matches[f"rps_{DEFAULT}"].mean(),
            "diff_vs_default": c["diff"], "ci_low": c["ci_low"], "ci_high": c["ci_high"],
            "better_seasons": c["a_better_seasons"],
            "find_diff": c["find"]["diff"], "find_ci_low": c["find"]["ci_low"], "find_ci_high": c["find"]["ci_high"],
            "confirm_diff": c["confirm"]["diff"], "confirm_ci_low": c["confirm"]["ci_low"], "confirm_ci_high": c["confirm"]["ci_high"],
            "beats_default": bool(c["a_better_seasons"] >= 8 and c["ci_high"] < 0),
            "loses_clearly": bool(c["ci_low"] > 0),
        })
        per_season += [{"knob": knob, "level": level, **s} for s in c["per_season"]]
    results = pd.DataFrame(rows)
    results.to_csv(f"{out_dir}/{out_stem}.csv", index=False)
    pd.DataFrame(per_season).to_csv(f"{out_dir}/{out_stem}_per_season.csv", index=False)
    _write_sweeps_report(results, matches, report_out, title=out_stem.replace("_", "-"))
    return results


def _write_sweeps_report(results: pd.DataFrame, matches: pd.DataFrame, path: str, title: str = "elo-sweeps") -> None:
    winners = results[results["beats_default"]]
    lines = [
        f"# {title} report", "",
        "Every Elo setting moved one at a time away from the default, scored against the",
        "default on identical horizon-0 matches, 2016-2025 (3,760 matches), with the",
        "goal-difference line fitted on the previous season (elo-ten-seasons). Negative",
        "means the level beats the default. Produced by `entrypoints/elo_backtest.py`.", "",
        "**Pass rule, fixed in the ticket before running:** better than the default in at",
        "least 8 of 10 seasons AND a pooled 95% interval clear of zero.", "",
        f"Default Elo, pooled RPS {matches[f'rps_{DEFAULT}'].mean():.5f}; incumbent "
        f"{matches[f'rps_{INCUMBENT}'].mean():.5f}. Gate: the default reproduces "
        "`elo_ten_seasons.csv` on every season.", "",
        f"**Levels that pass: {len(winners)} of {len(results)}.**", "",
        "| setting | level | − default | 95% CI | better in | find 2016-20 | confirm 2021-25 | verdict |",
        "|---|---|---:|---|---:|---:|---:|---|",
    ]
    for r in results.itertuples():
        verdict = "**passes**" if r.beats_default else ("worse, clearly" if r.loses_clearly else "no clear difference")
        lines.append(f"| {r.knob} | {r.level} | {r.diff_vs_default:+.5f} | [{r.ci_low:+.5f}, {r.ci_high:+.5f}] | "
                     f"{r.better_seasons}/10 | {r.find_diff:+.5f} | {r.confirm_diff:+.5f} | {verdict} |")
    lines += ["", "No default is changed by this ticket; adopting a level is a separate decision."]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"report written to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ten-seasons", action="store_true", help="run the elo-ten-seasons ticket")
    mode.add_argument("--sweep", action="store_true", help="run the elo-sweeps grid")
    mode.add_argument("--sudeste", action="store_true", help="run the elo-sudeste-gap grid")
    mode.add_argument("--state", action="store_true", help="run the state-championships arms")
    parser.add_argument("--workers", type=int, default=5, help="processes for --sweep, one season each")
    args = parser.parse_args()
    if args.ten_seasons:
        results = run_ten_seasons()
        for key in ("fixed_vs_incumbent_2020_2025", "elo_vs_incumbent_2020_2025", "rolling_vs_fixed_2020_2025", "elo_vs_incumbent"):
            c = results[key]
            print(f"{key:32s} {_ci(c)}  a better in {c['a_better_seasons']}/{len(c['seasons'])}")
    elif args.state:
        table = run_state_championships(workers=args.workers)
        pd.set_option("display.width", 200)
        print(table.drop(columns="per_season").round(5).to_string(index=False))
    else:
        if args.sudeste:
            table = run_sweeps(workers=args.workers, grid="sudeste", out_stem="elo_sudeste_gap",
                               report_out="../docs/superpowers/elo-sudeste-gap-report.md")
        else:
            table = run_sweeps(workers=args.workers)
        pd.set_option("display.width", 200)
        print(table[["knob", "level", "diff_vs_default", "ci_low", "ci_high", "better_seasons", "beats_default"]].round(5).to_string(index=False))

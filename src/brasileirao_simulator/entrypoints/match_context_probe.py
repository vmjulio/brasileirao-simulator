"""rest-hours-probe and travel-distance-probe: do rest or travel explain what
Elo gets wrong?

Method and pass rule are fixed on the board (kanban, "Shared method and gate
for rest-hours-probe and travel-distance-probe") and were committed before
this ran. In short: Elo's per-match horizon-0 forecasts for Série A 2016-2025
(the `elo_backtest` harness, default settings), each adjusted by a tilt
between home and away,

    p'(outcome) ∝ p(outcome) · exp(s · θ·z),   s = +1 / 0 / -1 for home / draw / away,

with θ fitted by maximum likelihood on 2016-2020 and scored on 2021-2025. A
feature passes if it lowers 2021-2025 RPS against Elo alone with a paired 95%
interval clear of zero and is better in at least 4 of those 5 seasons; travel
is judged on top of region terms (region+travel against region alone), so a
regional bias in Elo cannot pass as a travel effect.

WHY A TILT AND NOT A NEW MODEL. It asks exactly the question "given Elo's
forecast, does this feature move the result toward one side?", it has one
parameter per feature, and it leaves Elo's own judgement of the two clubs in
place - which is what controls for strength (the regional confound).

    PYTHONPATH=. python -m brasileirao_simulator.entrypoints.match_context_probe
"""

import csv
import json

import numpy as np
import pandas as pd

from brasileirao_simulator.config.settings import DATASETS_PATH, EXPORTS_PATH
from brasileirao_simulator.domain.geography import distance_km, home_places, place_of
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.entrypoints.benchmark_chancedegol import ONE_HOT, rps
from brasileirao_simulator.entrypoints.elo_backtest import (
    CONFIRM_SEASONS,
    FIND_SEASONS,
    TEN_SEASONS,
    EloSetting,
    SeasonInputs,
    score_season,
)
from brasileirao_simulator.entrypoints.match_brier_backtest import OUTCOMES, paired_bootstrap_ci

REST_CAP_HOURS = 336
SHORT_REST_HOURS = 72
REGIONS = ("Norte", "Nordeste", "Centro-Oeste", "Sul")  # Sudeste is the reference
CONTINENTAL = (11, 13)
ROTATION_WINDOW_HOURS = 96
ROTATION_SEASONS = tuple(range(2019, 2026))  # the store has continental matches from 2019
S = np.array([1.0, 0.0, -1.0])  # home, draw, away


def rest_hours(store: MatchStore) -> dict:
    """`{(fixture_id, team_id): hours since that club's previous kick-off in
    any competition in the store}`, capped at `REST_CAP_HOURS`; a club's first
    match in the store gets the cap."""
    m = store.matches
    t = pd.to_datetime(m["fixture_date"], utc=True)
    long = pd.concat([
        pd.DataFrame({"fixture_id": m["fixture_id"], "team_id": m["home_id"], "t": t}),
        pd.DataFrame({"fixture_id": m["fixture_id"], "team_id": m["away_id"], "t": t}),
    ]).sort_values(["team_id", "t"])
    gap = (long["t"] - long.groupby("team_id")["t"].shift()).dt.total_seconds() / 3600
    long["rest"] = gap.fillna(REST_CAP_HOURS).clip(upper=REST_CAP_HOURS)
    return dict(zip(zip(long["fixture_id"], long["team_id"]), long["rest"]))


def next_matches(store: MatchStore, extra: pd.DataFrame = None) -> dict:
    """`{(fixture_id, team_id): (hours until that club's next match in any
    competition, capped at REST_CAP_HOURS; the next match's league_id, or None;
    the club's previous kick-off)}`. `extra` adds matches to the calendar
    (e.g. the Wikipedia Libertadores seasons) without them entering the store."""
    m = store.matches if extra is None else pd.concat([store.matches, extra], ignore_index=True)
    t = pd.to_datetime(m["fixture_date"], utc=True)
    long = pd.concat([
        pd.DataFrame({"fixture_id": m["fixture_id"], "team_id": m["home_id"], "t": t, "league": m["league_id"]}),
        pd.DataFrame({"fixture_id": m["fixture_id"], "team_id": m["away_id"], "t": t, "league": m["league_id"]}),
    ]).sort_values(["team_id", "t"])
    grouped = long.groupby("team_id")
    hours = ((grouped["t"].shift(-1) - long["t"]).dt.total_seconds() / 3600).fillna(REST_CAP_HOURS).clip(upper=REST_CAP_HOURS)
    league = grouped["league"].shift(-1)
    previous = grouped["t"].shift()
    return {
        (f, tid): (h, None if pd.isna(lg) else int(lg), prev)
        for f, tid, h, lg, prev in zip(long["fixture_id"], long["team_id"], hours, league, previous)
    }


def match_features(seasons=TEN_SEASONS, extra_calendar: pd.DataFrame = None) -> pd.DataFrame:
    """One row per scored match: Elo's probabilities, the outcome, and the
    context features the probe tests."""
    store = MatchStore()
    rest = rest_hours(store)
    upcoming = next_matches(store, extra_calendar)
    frames = []
    for season in seasons:
        frame, _ = score_season(SeasonInputs(season), [EloSetting("elo")], store)
        with open(f"{DATASETS_PATH}/{season}/fixtures.csv", encoding="utf-8") as f:
            fixtures = {f"{r['teams_home_name']} x {r['teams_away_name']}": r for r in csv.DictReader(f)}
        homes = home_places(season)
        rows = []
        for match in frame.itertuples(index=False):
            fx = fixtures[match.match_key]
            home, away = fx["teams_home_name"], fx["teams_away_name"]
            fid, hid, aid = int(float(fx["fixture_id"])), int(float(fx["teams_home_id"])), int(float(fx["teams_away_id"]))
            venue = place_of(fx.get("fixture_venue_city")) or homes.get(home)
            origin = homes.get(away)
            nxt_home = upcoming.get((fid, hid), (np.nan, None, None))
            nxt_away = upcoming.get((fid, aid), (np.nan, None, None))
            rows.append({
                "next_hours_home": nxt_home[0], "next_league_home": nxt_home[1], "previous_kickoff_home": nxt_home[2],
                "next_hours_away": nxt_away[0], "next_league_away": nxt_away[1], "previous_kickoff_away": nxt_away[2],
                "rest_home": rest.get((fid, hid), np.nan),
                "rest_away": rest.get((fid, aid), np.nan),
                "travel_km": distance_km(origin, venue) if (origin and venue) else np.nan,
                "home_region": homes[home].region if home in homes else None,
                "away_region": origin.region if origin else None,
            })
        frames.append(pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1))
    df = pd.concat(frames, ignore_index=True)
    df["rest_diff_days"] = (df["rest_home"] - df["rest_away"]) / 24
    df["short_home"] = (df["rest_home"] < SHORT_REST_HOURS).astype(float)
    df["short_away"] = (df["rest_away"] < SHORT_REST_HOURS).astype(float)
    df["travel_1000km"] = df["travel_km"] / 1000
    df["next_days_home"] = df["next_hours_home"] / 24
    df["next_days_away"] = df["next_hours_away"] / 24
    for side in ("home", "away"):
        df[f"next_continental_{side}"] = (
            df[f"next_league_{side}"].isin(CONTINENTAL) & (df[f"next_hours_{side}"] <= ROTATION_WINDOW_HOURS)
        ).astype(float)
    df["sudeste_side"] = (df["home_region"] == "Sudeste").astype(float) - (df["away_region"] == "Sudeste").astype(float)
    for region in REGIONS:
        df[f"home_{region}"] = (df["home_region"] == region).astype(float)
        df[f"away_{region}"] = (df["away_region"] == region).astype(float)
    return df


def fit_tilt(p: np.ndarray, y: np.ndarray, z: np.ndarray, iterations: int = 50) -> np.ndarray:
    """Maximum-likelihood θ for p' ∝ p·exp(s·θ·z), by Newton's method. The
    log-likelihood is concave in θ, so this converges from zero."""
    theta = np.zeros(z.shape[1])
    for _ in range(iterations):
        q = tilt(p, z, theta)
        mean_s = q @ S
        var_s = q @ (S ** 2) - mean_s ** 2
        gradient = ((S[y] - mean_s)[:, None] * z).sum(0)
        hessian = (z * var_s[:, None]).T @ z + 1e-9 * np.eye(z.shape[1])
        step = np.linalg.solve(hessian, gradient)
        theta += step
        if np.abs(step).max() < 1e-10:
            break
    return theta


def tilt(p: np.ndarray, z: np.ndarray, theta: np.ndarray) -> np.ndarray:
    w = p * np.exp(np.outer(z @ theta, S))
    return w / w.sum(1, keepdims=True)


def evaluate(df: pd.DataFrame, features: list, baseline: list = ()) -> dict:
    """Fit on FIND_SEASONS, score on CONFIRM_SEASONS. The comparison is the
    model with `baseline + features` against the model with `baseline` alone
    (Elo alone when `baseline` is empty), on identical matches."""
    cols = list(baseline) + list(features)
    data = df.dropna(subset=cols).reset_index(drop=True)
    p = data[[f"p_{o}_elo" for o in OUTCOMES]].to_numpy(float)
    y = data["outcome"].map({"home": 0, "draw": 1, "away": 2}).to_numpy()
    onehot = np.array([ONE_HOT[o] for o in data["outcome"]], dtype=float)
    find = data["season"].isin(FIND_SEASONS).to_numpy()
    confirm = data["season"].isin(CONFIRM_SEASONS).to_numpy()

    def model_rps(columns):
        if not columns:
            return rps(p, onehot), {}
        z = data[list(columns)].to_numpy(float)
        theta = fit_tilt(p[find], y[find], z[find])
        return rps(tilt(p, z, theta), onehot), dict(zip(columns, theta.round(5)))

    rps_base, theta_base = model_rps(list(baseline))
    rps_full, theta_full = model_rps(cols)
    diff = rps_full[confirm] - rps_base[confirm]
    mean, lo, hi = paired_bootstrap_ci(diff, seed=7)
    seasons_c = data.loc[confirm, "season"].to_numpy()
    per_season = {int(s): float(diff[seasons_c == s].mean()) for s in CONFIRM_SEASONS}
    better = sum(v < 0 for v in per_season.values())
    return {
        "features": list(features), "baseline": list(baseline), "matches_confirm": int(confirm.sum()),
        "theta": {k: float(v) for k, v in theta_full.items()},
        "diff": mean, "ci_low": lo, "ci_high": hi, "better_seasons": better, "per_season": per_season,
        "passes": bool(hi < 0 and better >= 4),
    }


def evaluate_loso(df: pd.DataFrame, features: list, seasons, baseline: list = ()) -> dict:
    """Leave one season out: for each season, fit on the others in `seasons`
    and score it. The comparison is `baseline + features` against `baseline`
    alone (Elo alone when empty), on identical matches."""
    cols = list(baseline) + list(features)
    data = df[df["season"].isin(seasons)].dropna(subset=cols).reset_index(drop=True)
    p = data[[f"p_{o}_elo" for o in OUTCOMES]].to_numpy(float)
    y = data["outcome"].map({"home": 0, "draw": 1, "away": 2}).to_numpy()
    onehot = np.array([ONE_HOT[o] for o in data["outcome"]], dtype=float)
    season = data["season"].to_numpy()

    def held_out_rps(columns):
        if not columns:
            return rps(p, onehot), {}
        z = data[list(columns)].to_numpy(float)
        out, thetas = np.empty(len(data)), {}
        for s in seasons:
            train, test = season != s, season == s
            theta = fit_tilt(p[train], y[train], z[train])
            out[test] = rps(tilt(p[test], z[test], theta), onehot[test])
            thetas[int(s)] = dict(zip(columns, theta.round(4)))
        return out, thetas

    rps_base, _ = held_out_rps(list(baseline))
    rps_full, thetas = held_out_rps(cols)
    diff = rps_full - rps_base
    mean, lo, hi = paired_bootstrap_ci(diff, seed=7)
    per_season = {int(s): float(diff[season == s].mean()) for s in seasons}
    better = sum(v < 0 for v in per_season.values())
    return {"features": list(features), "baseline": list(baseline), "seasons": [int(s) for s in seasons],
            "matches": int(len(data)), "diff": mean, "ci_low": lo, "ci_high": hi,
            "better_seasons": better, "per_season": per_season, "theta_by_held_out_season": thetas}


def rotation_leak_check(df: pd.DataFrame) -> dict:
    """For matches flagged as 'continental match within 96 h', was the club's
    previous match - the last result that could decide whether that
    continental fixture exists - played before the forecast was made (horizon
    0 forecasts are made on `as_of_date`)?"""
    counts = {}
    for side in ("home", "away"):
        flagged = df[df[f"next_continental_{side}"] == 1]
        prev = pd.to_datetime(flagged[f"previous_kickoff_{side}"], utc=True)
        cutoff = pd.to_datetime(flagged["as_of_date"]).dt.tz_localize("UTC") + pd.Timedelta(days=1)
        counts[side] = {"flagged": int(len(flagged)), "previous_before_forecast": int((prev < cutoff).sum())}
    return counts


def run_rotation_flagged(df: pd.DataFrame, draws: int = 1000, seed: int = 7, leak_proxy: bool = True,
                         seasons=ROTATION_SEASONS, seasons_needed: int = 5) -> dict:
    """rotation-flagged-probe: the rotation flags scored only on the matches
    they touch.

    `leak_proxy` drops a flag when the club's previous match was played after
    the forecast was made (see the board). The user's domain knowledge
    (2026-09-11) is that continental fixtures are always known at least a
    week ahead, so a club - and a forecaster - always knows about a
    continental match due after a league match; `leak_proxy=False` keeps
    every flag on that basis."""
    data = df[df["season"].isin(seasons)].copy()
    cutoff = pd.to_datetime(data["as_of_date"]).dt.tz_localize("UTC") + pd.Timedelta(days=1)
    for side in ("home", "away"):
        known = pd.to_datetime(data[f"previous_kickoff_{side}"], utc=True) < cutoff if leak_proxy else True
        data[f"rotation_{side}"] = ((data[f"next_continental_{side}"] == 1) & known).astype(float)
    flagged = data[(data["rotation_home"] == 1) | (data["rotation_away"] == 1)].reset_index(drop=True)
    features = ["rotation_home", "rotation_away"]
    result = evaluate_loso(flagged, features, seasons)
    result["passes"] = bool(result["ci_high"] < 0 and result["better_seasons"] >= seasons_needed)

    # Effect size: the fitted tilt's average change in home-win probability on
    # the matches each flag touches, with a bootstrap over flagged matches.
    p = flagged[[f"p_{o}_elo" for o in OUTCOMES]].to_numpy(float)
    y = flagged["outcome"].map({"home": 0, "draw": 1, "away": 2}).to_numpy()
    z = flagged[features].to_numpy(float)

    def effect(idx):
        theta = fit_tilt(p[idx], y[idx], z[idx])
        q = tilt(p[idx], z[idx], theta)
        return [100 * float((q[idx_f, 0] - p[idx][idx_f, 0]).mean()) for idx_f in (z[idx, 0] == 1, z[idx, 1] == 1)]

    point = effect(np.arange(len(flagged)))
    rng = np.random.default_rng(seed)
    boot = np.array([effect(rng.integers(0, len(flagged), len(flagged))) for _ in range(draws)])
    result["effect_points"] = {
        side: {"home_win_change": point[i], "ci_low": float(np.percentile(boot[:, i], 2.5)),
               "ci_high": float(np.percentile(boot[:, i], 97.5))}
        for i, side in enumerate(("home side has a continental match soon", "away side has a continental match soon"))
    }
    result["counts"] = {"flagged_matches": int(len(flagged)), "home_flags": int(flagged["rotation_home"].sum()),
                        "away_flags": int(flagged["rotation_away"].sum()),
                        "dropped_by_leak_proxy": int(((data["next_continental_home"] == 1) | (data["next_continental_away"] == 1)).sum() - len(flagged))}
    return result


def describe(df: pd.DataFrame) -> dict:
    """What the raw residuals look like, all ten seasons: home-win share
    against Elo's home-win probability, by travel band, by rest gap and by
    region pair."""
    df = df.assign(home_won=(df["outcome"] == "home").astype(float))

    def table(group):
        g = df.groupby(group, observed=True).agg(matches=("home_won", "size"), elo_p_home=("p_home_elo", "mean"),
                                                 home_won=("home_won", "mean"))
        g["residual"] = g["home_won"] - g["elo_p_home"]
        return g.round(3).reset_index().astype({group: str}).to_dict("records")

    df["travel_band"] = pd.cut(df["travel_km"], [-1, 150, 500, 1000, 2000, 5000],
                               labels=["< 150 km", "150-500", "500-1,000", "1,000-2,000", "> 2,000 km"])
    df["rest_band"] = pd.cut(df["rest_diff_days"], [-99, -2, -0.5, 0.5, 2, 99],
                             labels=["home 2+ days less", "home 0.5-2 less", "within half a day", "home 0.5-2 more", "home 2+ days more"])
    df["region_pair"] = df["home_region"].fillna("?") + " hosts " + df["away_region"].fillna("?")
    pairs = table("region_pair")
    return {"travel": table("travel_band"), "rest": table("rest_band"),
            "region_pairs": sorted([r for r in pairs if r["matches"] >= 40], key=lambda r: r["residual"])}


def run_loso(df: pd.DataFrame) -> dict:
    """region-loso-probe and rotation-probe, as fixed on the board."""
    region = [f"home_{r}" for r in REGIONS] + [f"away_{r}" for r in REGIONS]
    rotation = ["next_days_home", "next_days_away", "next_continental_home", "next_continental_away"]
    tests = {
        "region, eight terms": (evaluate_loso(df, region, TEN_SEASONS), 8),
        "region, Sudeste against the rest": (evaluate_loso(df, ["sudeste_side"], TEN_SEASONS), 8),
        "rotation (pre-registered: next-match hours + continental flags)": (evaluate_loso(df, rotation, ROTATION_SEASONS), 5),
        "rotation diagnostic: continental flags only": (evaluate_loso(df, rotation[2:], ROTATION_SEASONS), 5),
        "rotation diagnostic: next-match hours only": (evaluate_loso(df, rotation[:2], ROTATION_SEASONS), 5),
    }
    out = {}
    for name, (r, need) in tests.items():
        r["passes"] = bool(r["ci_high"] < 0 and r["better_seasons"] >= need)
        r["seasons_needed"] = need
        out[name] = r
    rot = df[df["season"].isin(ROTATION_SEASONS)]
    out["_rotation_counts"] = {"matches": int(len(rot)),
                               "home_flagged": int(rot["next_continental_home"].sum()),
                               "away_flagged": int(rot["next_continental_away"].sum()),
                               "leak_check": rotation_leak_check(rot)}
    return out


if __name__ == "__main__":
    import sys

    if "--rotation-ten" in sys.argv:
        wikipedia = MatchStore(root=f"{DATASETS_PATH}/wikipedia").matches
        r = run_rotation_flagged(match_features(TEN_SEASONS, extra_calendar=wikipedia), leak_proxy=False,
                                 seasons=TEN_SEASONS, seasons_needed=8)
        with open(f"{EXPORTS_PATH}/match_context_rotation_ten.json", "w") as f:
            json.dump(r, f, indent=1, default=float)
        print(r["counts"])
        print(f"flagged matches, 2016-2025: {r['diff']:+.5f} [{r['ci_low']:+.5f}, {r['ci_high']:+.5f}]  "
              f"better {r['better_seasons']}/10  {'PASS' if r['passes'] else 'flat'}")
        print("per season:", {k: round(v, 5) for k, v in r["per_season"].items()})
        for side, e in r["effect_points"].items():
            print(f"  {side}: home win {e['home_win_change']:+.1f} pts [{e['ci_low']:+.1f}, {e['ci_high']:+.1f}]")
        sys.exit(0)

    if "--rotation-flagged" in sys.argv:
        all_flags = "--all-flags" in sys.argv
        r = run_rotation_flagged(match_features(ROTATION_SEASONS), leak_proxy=not all_flags)
        name = "match_context_rotation_flagged_all.json" if all_flags else "match_context_rotation_flagged.json"
        with open(f"{EXPORTS_PATH}/{name}", "w") as f:
            json.dump(r, f, indent=1, default=float)
        print(r["counts"])
        print(f"flagged matches only: {r['diff']:+.5f} [{r['ci_low']:+.5f}, {r['ci_high']:+.5f}]  better {r['better_seasons']}/7  "
              f"{'PASS' if r['passes'] else 'flat'}")
        print("per season:", {k: round(v, 5) for k, v in r["per_season"].items()})
        for side, e in r["effect_points"].items():
            print(f"  {side}: home win {e['home_win_change']:+.1f} pts [{e['ci_low']:+.1f}, {e['ci_high']:+.1f}]")
        sys.exit(0)

    if "--loso" in sys.argv:
        df = match_features()
        results = run_loso(df)
        with open(f"{EXPORTS_PATH}/match_context_loso.json", "w") as f:
            json.dump(results, f, indent=1, default=float)
        for name, r in results.items():
            if name.startswith("_"):
                print(name, r)
                continue
            print(f"{name:66s} {r['diff']:+.5f} [{r['ci_low']:+.5f}, {r['ci_high']:+.5f}]  better {r['better_seasons']}/{len(r['seasons'])}  "
                  f"{'PASS' if r['passes'] else 'flat'}")
        sys.exit(0)

    df = match_features()
    region = [f"home_{r}" for r in REGIONS] + [f"away_{r}" for r in REGIONS]
    tests = {
        "rest difference": evaluate(df, ["rest_diff_days"]),
        "short rest flags": evaluate(df, ["short_home", "short_away"]),
        "region terms alone": evaluate(df, region),
        "travel alone (no region control)": evaluate(df, ["travel_1000km"]),
        "travel on top of region": evaluate(df, ["travel_1000km"], baseline=region),
    }
    out = {"matches": int(len(df)), "tests": tests, "describe": describe(df)}
    with open(f"{EXPORTS_PATH}/match_context_probe.json", "w") as f:
        json.dump(out, f, indent=1, default=float)
    print(f"{len(df)} matches; missing rest {int(df['rest_home'].isna().sum())}, missing travel {int(df['travel_km'].isna().sum())}")
    for name, r in tests.items():
        print(f"{name:34s} {r['diff']:+.5f} [{r['ci_low']:+.5f}, {r['ci_high']:+.5f}]  better {r['better_seasons']}/5  "
              f"{'PASS' if r['passes'] else 'flat'}  theta {r['theta']}")

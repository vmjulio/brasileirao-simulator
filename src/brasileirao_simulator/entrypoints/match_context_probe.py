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


def match_features(seasons=TEN_SEASONS) -> pd.DataFrame:
    """One row per scored match: Elo's probabilities, the outcome, and the
    context features the probe tests."""
    store = MatchStore()
    rest = rest_hours(store)
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
            rows.append({
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


if __name__ == "__main__":
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

"""Fits the linear Elo-difference -> expected-goal-difference map that
`elo_lambda.lambdas` uses to split a match's expected total goals between the
two sides.

WHY BURN-IN-SEASON ONLY. The map is deliberately fitted once, on a single
season, and never refreshed online - see `DifferenceMap`'s docstring in
`elo_lambda.py`. Fitting it on every season (or incrementally) would let the
map's own slope drift with whatever Elo separation happened to exist that
year, defeating the point of having one fixed, monotone translation from
Elo points to goals.

See `elo_lambda.fit_difference_map`'s docstring for the full contract this
implements; this module only holds the implementation, to keep
`elo_lambda.py` free of the import (`EloHistory`, `MatchStore`) cycle a
direct implementation there would create with `elo.py`.
"""

import numpy as np

from brasileirao_simulator.domain.elo import EloHistory
from brasileirao_simulator.domain.elo_lambda import DifferenceMap, EloLambdaParams
from brasileirao_simulator.domain.match_store import MatchStore

# Below this many matches, an OLS fit on one season is not a fit worth
# trusting - see the ticket's gate.
_MIN_MATCHES = 50


def fit_difference_map(
    history: EloHistory, store: MatchStore, params: EloLambdaParams = EloLambdaParams()
) -> DifferenceMap:
    """Regress observed 90-minute goal difference on Elo difference over
    `params.burn_in_season` only. See `elo_lambda.fit_difference_map` for
    the full contract."""
    season = params.burn_in_season
    season_matches = store.matches[store.matches["season"] == season]

    home_before = history.ratings.loc[history.ratings["is_home"], ["fixture_id", "elo_before"]].rename(
        columns={"elo_before": "elo_before_home"}
    )
    away_before = history.ratings.loc[~history.ratings["is_home"], ["fixture_id", "elo_before"]].rename(
        columns={"elo_before": "elo_before_away"}
    )

    joined = season_matches.merge(home_before, on="fixture_id", how="inner").merge(
        away_before, on="fixture_id", how="inner"
    )

    n_matches = len(joined)
    if n_matches < _MIN_MATCHES:
        raise ValueError(
            f"only {n_matches} matches available to fit the difference map on burn-in "
            f"season {season}, need at least {_MIN_MATCHES}"
        )

    home_advantage = params.home_advantage * (1.0 - joined["is_neutral"].astype(float))
    x = (joined["elo_before_home"] + home_advantage - joined["elo_before_away"]).to_numpy(dtype=float)
    y = (joined["home_goals"] - joined["away_goals"]).to_numpy(dtype=float)

    slope, intercept = np.polyfit(x, y, 1)
    if slope <= 0:
        raise ValueError(
            f"fitted slope {slope} for burn-in season {season} is not positive - "
            "a non-positive slope must stop the pipeline, not flow downstream"
        )

    return DifferenceMap(
        slope=float(slope), intercept=float(intercept), fitted_on_season=season, n_matches=n_matches
    )

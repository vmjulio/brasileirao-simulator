"""The probe's tilt model: p' ∝ p·exp(s·θ·z), s = +1/0/-1 for home/draw/away."""

import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.entrypoints.match_context_probe import REST_CAP_HOURS, fit_tilt, rest_hours, tilt


def test_zero_tilt_leaves_the_forecast_alone():
    p = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    np.testing.assert_allclose(tilt(p, np.ones((2, 1)), np.zeros(1)), p)


def test_positive_tilt_moves_mass_from_away_to_home_and_keeps_a_distribution():
    p = np.array([[0.4, 0.3, 0.3]])
    q = tilt(p, np.ones((1, 1)), np.array([0.5]))
    assert q[0, 0] > p[0, 0] and q[0, 2] < p[0, 2]
    assert q.sum() == pytest.approx(1.0)


def test_fit_recovers_a_known_tilt():
    rng = np.random.default_rng(3)
    n = 20000
    p = rng.dirichlet([4, 3, 3], size=n)
    z = rng.normal(size=(n, 1))
    q = tilt(p, z, np.array([0.3]))
    y = np.array([rng.choice(3, p=row) for row in q])
    theta = fit_tilt(p, y, z)
    assert theta[0] == pytest.approx(0.3, abs=0.04)


def test_fit_finds_nothing_in_noise():
    rng = np.random.default_rng(4)
    n = 20000
    p = rng.dirichlet([4, 3, 3], size=n)
    y = np.array([rng.choice(3, p=row) for row in p])
    theta = fit_tilt(p, y, rng.normal(size=(n, 1)))
    assert abs(theta[0]) < 0.04


def test_rest_is_hours_since_the_previous_kickoff_capped():
    frame = pd.DataFrame({
        "fixture_id": [1, 2, 3], "home_id": [10, 20, 10], "away_id": [20, 30, 30],
        "fixture_date": ["2025-01-01T20:00:00+00:00", "2025-01-04T20:00:00+00:00", "2025-03-01T20:00:00+00:00"],
    })
    store = MatchStore.__new__(MatchStore)
    store._matches = frame
    rest = rest_hours(store)
    assert rest[(1, 10)] == REST_CAP_HOURS            # first appearance
    assert rest[(2, 20)] == 72                        # three days after match 1
    assert rest[(3, 30)] == REST_CAP_HOURS            # 56 days later, capped


def test_next_match_is_hours_until_the_following_kickoff_with_its_competition():
    from brasileirao_simulator.entrypoints.match_context_probe import next_matches

    frame = pd.DataFrame({
        "fixture_id": [1, 2, 3], "home_id": [10, 20, 10], "away_id": [20, 30, 30],
        "league_id": [71, 13, 71], "round": ["Regular Season - 1", "8th Finals", "Regular Season - 2"],
        "fixture_date": ["2025-01-01T20:00:00+00:00", "2025-01-04T20:00:00+00:00", "2025-03-01T20:00:00+00:00"],
    })
    store = MatchStore.__new__(MatchStore)
    store._matches = frame
    nxt = next_matches(store)
    hours, league, _, next_round, at_home = nxt[(1, 20)]
    assert hours == 72 and league == 13        # club 20 plays a Libertadores match three days later
    assert next_round == "8th Finals" and at_home   # ... a last-16 match, at home
    assert nxt[(3, 10)][0] == REST_CAP_HOURS    # no later match: capped
    assert nxt[(3, 10)][1] is None

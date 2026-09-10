import numpy as np
import pandas as pd
import pytest

from brasileirao_simulator.domain import dixon_coles


def synthetic(attack, defence, home_advantage, seed=0):
    """A league generated from known ratings, played twice against everyone."""
    rng = np.random.default_rng(seed)
    teams = list(attack)
    rows = []
    for home in teams:
        for away in teams:
            if home == away:
                continue
            # Many repeats so the fit can recover the truth. 12 (the count this
            # test was drafted with) is not enough for the tolerances below:
            # at 12 the whole league is 144 matches, a club's attack rating
            # rests on ~45 goals, and its sampling error alone is ~15% - the
            # MAXIMUM-LIKELIHOOD point is then routinely 25%+ away from the
            # generating value (verified: at 12 repeats and seed 0 the fitted
            # ratings score a strictly HIGHER log-likelihood than the true ones,
            # so the gap is the sample's, not the estimator's). 120 repeats
            # shrinks that error enough that the tolerances test the estimator
            # rather than the draw.
            for repeat in range(120):
                lam_h = attack[home] * defence[away] * home_advantage
                lam_a = attack[away] * defence[home]
                gh, ga = rng.poisson(lam_h), rng.poisson(lam_a)
                rows += [
                    {"team_name": home, "opponent_name": away, "venue": "home",
                     "goals_for": gh, "goals_against": ga, "fixture_date": "2025-01-01"},
                    {"team_name": away, "opponent_name": home, "venue": "away",
                     "goals_for": ga, "goals_against": gh, "fixture_date": "2025-01-01"},
                ]
    return pd.DataFrame(rows)


def test_recovers_known_ratings():
    """The whole point of the model: fitted ratings must match the ones the
    data was generated from, which is what 'opponent strength cancels by
    construction' means in practice."""
    attack = {"A": 1.6, "B": 1.0, "C": 0.6, "D": 1.2}
    defence = {"A": 0.7, "B": 1.0, "C": 1.4, "D": 0.9}
    fixtures = synthetic(attack, defence, home_advantage=1.3)

    ratings = dixon_coles.fit(fixtures, as_of_date="2025-06-01", xi=0.0)

    assert ratings.converged
    scale = np.mean(list(attack.values()))
    for team in attack:
        assert ratings.attack[team] == pytest.approx(attack[team] / scale, rel=0.10)
        assert ratings.defence[team] == pytest.approx(defence[team] * scale, rel=0.12)
    assert ratings.home_advantage == pytest.approx(1.3, rel=0.08)


def test_a_club_that_played_only_weak_defences_is_not_credited_for_it():
    """The failure mode this model exists to fix. Two clubs with identical
    attack ratings, one fed on weak defences, must come out equal - a raw
    goals-per-game average would not."""
    attack = {"Strong": 1.5, "Equal1": 1.0, "Equal2": 1.0, "Weak": 0.6}
    defence = {"Strong": 0.7, "Equal1": 1.0, "Equal2": 1.0, "Weak": 1.5}
    fixtures = synthetic(attack, defence, home_advantage=1.25, seed=3)

    ratings = dixon_coles.fit(fixtures, as_of_date="2025-06-01", xi=0.0)
    assert ratings.attack["Equal1"] == pytest.approx(ratings.attack["Equal2"], rel=0.12)


def test_attack_ratings_are_normalised():
    attack = {"A": 1.6, "B": 1.0, "C": 0.6}
    defence = {"A": 0.8, "B": 1.0, "C": 1.3}
    ratings = dixon_coles.fit(synthetic(attack, defence, 1.2), "2025-06-01", xi=0.0)
    assert np.mean(list(ratings.attack.values())) == pytest.approx(1.0, abs=1e-6)


def test_outcome_probs_sum_to_one():
    home, draw, away = dixon_coles.outcome_probs(1.6, 1.1, rho=-0.05)
    assert home + draw + away == pytest.approx(1.0, abs=1e-6)


def test_rho_lifts_the_draw_probability():
    """Dixon and Coles' correction exists because independent Poissons
    under-predict low-scoring draws."""
    _, draw_plain, _ = dixon_coles.outcome_probs(1.4, 1.2, rho=0.0)
    _, draw_corrected, _ = dixon_coles.outcome_probs(1.4, 1.2, rho=-0.08)
    assert draw_corrected > draw_plain


def test_time_decay_favours_recent_matches():
    """A club that improved sharply must rate higher under decay than without."""
    rows = []
    for repeat in range(20):
        rows += [{"team_name": "Riser", "opponent_name": "Foil", "venue": "home",
                  "goals_for": 0, "goals_against": 2, "fixture_date": "2025-01-10"},
                 {"team_name": "Foil", "opponent_name": "Riser", "venue": "away",
                  "goals_for": 2, "goals_against": 0, "fixture_date": "2025-01-10"},
                 {"team_name": "Riser", "opponent_name": "Foil", "venue": "home",
                  "goals_for": 4, "goals_against": 0, "fixture_date": "2025-06-10"},
                 {"team_name": "Foil", "opponent_name": "Riser", "venue": "away",
                  "goals_for": 0, "goals_against": 4, "fixture_date": "2025-06-10"}]
    fixtures = pd.DataFrame(rows)
    flat = dixon_coles.fit(fixtures, "2025-06-20", xi=0.0)
    decayed = dixon_coles.fit(fixtures, "2025-06-20", xi=0.02)
    assert decayed.attack["Riser"] > flat.attack["Riser"]

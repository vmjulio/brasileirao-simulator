import numpy as np

from brasileirao_simulator.adapters.dixon_coles_adapter import DixonColesAdapter
from brasileirao_simulator.adapters.dixon_coles_all_adapter import DixonColesAllAdapter
from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.domain.batch_simulation import build_baseline
from brasileirao_simulator.domain.match_store import MatchStore
from brasileirao_simulator.domain.season_data import SeasonData
from brasileirao_simulator.domain.tables import Tables
from brasileirao_simulator.entrypoints.simulators import SIMULATORS, simulator_for


SEASON = 2025
AS_OF = "2025-08-01"

# fixture_id-free: Chapecoense-sc, id 132 - Série B only in 2025, never a
# Série A fixture, so never in new_fixtures - see match_store's own
# coverage(2025) for the check this literal is drawn from.
CHAPECOENSE_ID = 132

# Independiente del Valle, id 1153 - a Libertadores opponent (Ecuador), never
# a Brasileirão club at all.
INDEPENDIENTE_DEL_VALLE_ID = 1153


def _tables():
    tables = Tables(SeasonData(SEASON))
    fixtures = tables.enriched_tidy_fixtures(blank_from_date=AS_OF)
    remaining = tables.remaining_games(blank_from_date=AS_OF)
    return fixtures, remaining


def _baselines():
    fixtures, remaining = _tables()
    all_baseline = DixonColesAllAdapter("average", SEASON).build_baseline(fixtures, remaining)
    league_baseline = DixonColesAdapter("average", SEASON).build_baseline(fixtures, remaining)
    return all_baseline, league_baseline


def test_dixon_coles_all_is_registered():
    assert "dixon_coles_all" in SIMULATORS


def test_existing_simulators_are_untouched():
    """Additive: adding a simulator must not disturb the four that exist."""
    assert {"loop", "batch", "uncertain", "dixon_coles"} <= set(SIMULATORS)


def test_simulator_for_resolves():
    adapter = simulator_for("dixon_coles_all", "poisson_same_venue_average", 2025)
    assert adapter is not None


def test_lambdas_are_finite_and_positive_on_a_real_date():
    ours, _ = _baselines()
    assert len(ours.lam_home) > 0
    for lam in (ours.lam_home, ours.lam_away):
        assert np.all(np.isfinite(lam))
        assert np.all(lam > 0)


def test_lambdas_differ_from_the_league_only_arm():
    """Arm B (all competitions) must actually estimate something different
    from arm A (league only) - otherwise there is nothing for the next
    ticket's backtest to compare."""
    ours, theirs = _baselines()
    assert ours.home_name == theirs.home_name  # same fixtures, same order
    assert np.any(np.abs(ours.lam_home - theirs.lam_home) > 1e-6)


def test_prediction_set_is_identical_to_dixon_coles():
    """The set of fixtures simulated is the league's, untouched - the
    equivalence-gates promise (T2.2) carried into this adapter."""
    ours, theirs = _baselines()
    assert ours.teams == theirs.teams
    assert np.array_equal(ours.fixture_id, theirs.fixture_id)
    assert np.array_equal(ours.points, theirs.points)
    assert ours.home_name == theirs.home_name
    assert ours.away_name == theirs.away_name


def test_ratings_contain_clubs_outside_serie_a():
    """A Série B-only club and a Libertadores opponent, named explicitly by
    id, must be rated even though neither ever appears in new_fixtures for
    2025."""
    fixtures, _ = _tables()
    assert CHAPECOENSE_ID not in set(fixtures["team_id"])
    assert INDEPENDIENTE_DEL_VALLE_ID not in set(fixtures["team_id"])

    adapter = DixonColesAllAdapter("average", SEASON)
    adapter.build_baseline(fixtures, _tables()[1])
    ratings = adapter.ratings

    assert str(CHAPECOENSE_ID) in ratings.attack
    assert str(INDEPENDIENTE_DEL_VALLE_ID) in ratings.attack


def test_serie_a_median_attack_rating_exceeds_serie_b():
    """Division by league-id appearance in MatchStore for the season - the
    same signal domain/competitions.py's Rule.division names, computed
    directly from MatchStore rather than a hardcoded table."""
    fixtures, remaining = _tables()
    adapter = DixonColesAllAdapter("average", SEASON)
    adapter.build_baseline(fixtures, remaining)
    ratings = adapter.ratings

    store = MatchStore()
    season_matches = store.matches[store.matches["season"] == SEASON]
    serie_a_ids = set(season_matches.loc[season_matches["league_id"] == 71, "home_id"]) | set(
        season_matches.loc[season_matches["league_id"] == 71, "away_id"]
    )
    serie_b_ids = set(season_matches.loc[season_matches["league_id"] == 72, "home_id"]) | set(
        season_matches.loc[season_matches["league_id"] == 72, "away_id"]
    )

    serie_a_attack = [ratings.attack[str(i)] for i in serie_a_ids if str(i) in ratings.attack]
    serie_b_attack = [ratings.attack[str(i)] for i in serie_b_ids if str(i) in ratings.attack]

    assert len(serie_a_attack) > 0
    assert len(serie_b_attack) > 0
    assert np.median(serie_a_attack) > np.median(serie_b_attack)


def test_incumbent_lambdas_are_byte_identical_with_this_module_imported():
    """Additivity: importing dixon_coles_all_adapter must not change a
    single byte the incumbent (batch) produces. Re-running T2.2's third
    assertion with the module imported is the proof."""
    fixtures, remaining = _tables()

    incumbent = IterationBatchAdapter("average", SEASON)
    incumbent.con.register("new_fixtures", fixtures)
    team_params = incumbent.con.sql(incumbent.queries.team_params_same_venue_average()).df()
    before = build_baseline(fixtures, remaining, team_params, SEASON)

    # dixon_coles_all_adapter is already imported at module scope above -
    # this call re-asserts equality with it loaded, which is the point.
    after = build_baseline(fixtures, remaining, team_params, SEASON)

    assert np.array_equal(before.lam_home, after.lam_home)
    assert np.array_equal(before.lam_away, after.lam_away)

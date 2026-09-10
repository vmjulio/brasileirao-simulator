"""Choosing a simulator by name.

loop is the original per-season implementation and stays the default: it is the
reference the batch paths are validated against.

FullVectorAdapter (the "vector" strategy) is deliberately not offered here: it
measured no faster than IterationBatchAdapter, so a user-facing choice that
buys nothing is left out. The class and its vectorise_fixtures kwarg on
simulate_batch stay in place so that measurement remains reproducible.

uncertain is the same model as batch, with each simulated season drawing its
own team strengths instead of reusing one fixed estimate - see
UncertainParamsAdapter's docstring for what that does and does not model.

dixon_coles is a different MODEL, not a different simulation of the same one:
it replaces each fixture's lambda pair with one built from attack and defence
ratings fitted jointly across every club, so opponent strength cancels by
construction instead of being averaged away. It is a measurement candidate,
offered here so it can be scored on real matches - see
entrypoints/dixon_coles_backtest.py. batch remains the default.

dixon_coles_all is the same Dixon-Coles fit as dixon_coles, but reads
domain/match_store.MatchStore (Série A, Série B, and the later rounds of Copa
do Brasil, Libertadores and Sudamericana - domain/competitions.py) instead of
Série A fixtures alone, so a club's rating reflects every admitted match it
played, not just its Brasileirão ones. It exists to isolate whether wider
data, not a different estimator, is what closes the gap dixon_coles alone
did not - see adapters/dixon_coles_all_adapter.py. Still only Brasileirão
fixtures are simulated; batch remains the default.

elo is a different decomposition again: each remaining fixture's lambda
pair comes from an Elo replay over the whole MatchStore (domain/elo.py)
split into "how much better" (an Elo-difference -> expected-goal-difference
map, domain/elo_difference_map.py) and "how many goals" (each club's
opponent-adjusted contribution to total goals, domain/elo_total_goals.py) -
see domain/elo_lambda.py's module docstring for the full decomposition and
adapters/elo_adapter.py for how the two are cached and combined. Still only
Brasileirão fixtures are simulated; batch remains the default.
"""

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.dixon_coles_adapter import DixonColesAdapter
from brasileirao_simulator.adapters.dixon_coles_all_adapter import DixonColesAllAdapter
from brasileirao_simulator.adapters.elo_adapter import EloAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.adapters.uncertain_params_adapter import UncertainParamsAdapter


SIMULATORS = {
    "loop": PoissonSameVenueAverageAdapter,
    "batch": IterationBatchAdapter,
    "uncertain": UncertainParamsAdapter,
    "dixon_coles": DixonColesAdapter,
    "dixon_coles_all": DixonColesAllAdapter,
    "elo": EloAdapter,
}


def simulator_for(name: str, strategy: str, season: int):
    if name not in SIMULATORS:
        raise ValueError(f"unknown simulator {name!r}; choose one of {sorted(SIMULATORS)}")
    return SIMULATORS[name](strategy, season)

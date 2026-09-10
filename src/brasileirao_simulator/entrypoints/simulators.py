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
"""

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.dixon_coles_adapter import DixonColesAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.adapters.uncertain_params_adapter import UncertainParamsAdapter


SIMULATORS = {
    "loop": PoissonSameVenueAverageAdapter,
    "batch": IterationBatchAdapter,
    "uncertain": UncertainParamsAdapter,
    "dixon_coles": DixonColesAdapter,
}


def simulator_for(name: str, strategy: str, season: int):
    if name not in SIMULATORS:
        raise ValueError(f"unknown simulator {name!r}; choose one of {sorted(SIMULATORS)}")
    return SIMULATORS[name](strategy, season)

"""Choosing a simulator by name.

loop is the original per-season implementation and stays the default: it is the
reference the batch paths are validated against.

FullVectorAdapter (the "vector" strategy) is deliberately not offered here: it
measured no faster than IterationBatchAdapter, so a user-facing choice that
buys nothing is left out. The class and its vectorise_fixtures kwarg on
simulate_batch stay in place so that measurement remains reproducible.
"""

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)


SIMULATORS = {
    "loop": PoissonSameVenueAverageAdapter,
    "batch": IterationBatchAdapter,
}


def simulator_for(name: str, strategy: str, season: int):
    if name not in SIMULATORS:
        raise ValueError(f"unknown simulator {name!r}; choose one of {sorted(SIMULATORS)}")
    return SIMULATORS[name](strategy, season)

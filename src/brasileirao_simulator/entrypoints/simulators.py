"""Choosing a simulator by name.

loop is the original per-season implementation and stays the default: it is the
reference the batch paths are validated against.
"""

from brasileirao_simulator.adapters.batch_poisson_adapter import (
    FullVectorAdapter,
    IterationBatchAdapter,
)
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)


SIMULATORS = {
    "loop": PoissonSameVenueAverageAdapter,
    "batch": IterationBatchAdapter,
    "vector": FullVectorAdapter,
}


def simulator_for(name: str, strategy: str, season: int):
    if name not in SIMULATORS:
        raise ValueError(f"unknown simulator {name!r}; choose one of {sorted(SIMULATORS)}")
    return SIMULATORS[name](strategy, season)

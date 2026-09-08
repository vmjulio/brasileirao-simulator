"""Choosing a simulator from the command line."""

import pytest

from brasileirao_simulator.adapters.batch_poisson_adapter import (
    FullVectorAdapter,
    IterationBatchAdapter,
)
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.entrypoints.simulators import simulator_for


def test_the_default_is_the_reference_implementation():
    assert isinstance(simulator_for("loop", "average", 2026), PoissonSameVenueAverageAdapter)


def test_batch_and_vector_select_their_adapters():
    assert isinstance(simulator_for("batch", "average", 2026), IterationBatchAdapter)
    assert isinstance(simulator_for("vector", "average", 2026), FullVectorAdapter)


def test_every_simulator_carries_the_season():
    """SimulationService raises if the adapter's season disagrees with params."""
    for name in ("loop", "batch", "vector"):
        assert simulator_for(name, "average", 2026).season == 2026


def test_an_unknown_name_names_the_valid_ones():
    with pytest.raises(ValueError) as excinfo:
        simulator_for("turbo", "average", 2026)

    assert "loop" in str(excinfo.value)

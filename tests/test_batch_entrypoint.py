"""Choosing a simulator from the command line."""

import pytest

from brasileirao_simulator.adapters.batch_poisson_adapter import IterationBatchAdapter
from brasileirao_simulator.adapters.poisson_same_venue_average_adapter import (
    PoissonSameVenueAverageAdapter,
)
from brasileirao_simulator.entrypoints.simulators import simulator_for


def test_the_default_is_the_reference_implementation():
    assert isinstance(simulator_for("loop", "average", 2026), PoissonSameVenueAverageAdapter)


def test_batch_selects_its_adapter():
    assert isinstance(simulator_for("batch", "average", 2026), IterationBatchAdapter)


def test_every_simulator_carries_the_season():
    """SimulationService raises if the adapter's season disagrees with params."""
    for name in ("loop", "batch"):
        assert simulator_for(name, "average", 2026).season == 2026


def test_an_unknown_name_names_the_valid_ones():
    with pytest.raises(ValueError) as excinfo:
        simulator_for("turbo", "average", 2026)

    assert "loop" in str(excinfo.value)


def test_vector_is_not_a_cli_choice():
    """FullVectorAdapter measured no faster than IterationBatchAdapter (see
    README), so it is kept as a class for the reproducible comparison test in
    test_batch_adapter.py but dropped from the CLI's valid choices."""
    with pytest.raises(ValueError) as excinfo:
        simulator_for("vector", "average", 2026)

    assert "loop" in str(excinfo.value)
    assert "batch" in str(excinfo.value)

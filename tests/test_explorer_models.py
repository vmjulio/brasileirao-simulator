"""The explorer's model registry and the report's multi-model payload."""

import json
import shutil
from pathlib import Path

import pytest

from brasileirao_simulator.config.explorer_models import (
    DEFAULT_EXPLORER_MODEL,
    EXPLORER_MODELS,
    MODEL_RESULTS_DIRECTORY,
    explorer_model,
)
from brasileirao_simulator.config.settings import RESULTS_DIRECTORY
from brasileirao_simulator.entrypoints.report import build_report

EXPORTS = Path(__file__).resolve().parents[1] / "src" / "files" / "exports"


def test_only_the_incumbent_writes_to_the_original_pickle_directory():
    assert EXPLORER_MODELS["incumbent"].results_directory == RESULTS_DIRECTORY
    for key, model in EXPLORER_MODELS.items():
        if key != "incumbent":
            assert model.results_directory != RESULTS_DIRECTORY
            assert model.results_directory.startswith(MODEL_RESULTS_DIRECTORY + "/")


def test_each_model_has_its_own_files():
    for field in ("results_directory", "dataset_file", "benchmark_file", "benchmark_pooled_file"):
        values = [getattr(m, field) for m in EXPLORER_MODELS.values()]
        assert len(values) == len(set(values)), field


def test_default_model_is_registered_and_is_elo():
    assert DEFAULT_EXPLORER_MODEL == "elo" and DEFAULT_EXPLORER_MODEL in EXPLORER_MODELS


def test_unknown_model_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown explorer model 'dc'"):
        explorer_model("dc")


def _exports(tmp_path, with_elo: bool) -> Path:
    """A minimal exports directory: the incumbent's real files, and optionally
    an Elo dataset (a copy of the incumbent's, only to exercise the loader)."""
    out = tmp_path / "exports"
    out.mkdir()
    for name in ("forecast_dataset.json", "benchmark.json", "benchmark_pooled.json",
                 "recency_weights_sweep_pooled.csv", "benchmark_elo_ten.json",
                 "benchmark_elo_ten_pooled.json", "elo_ten_seasons_pooled.json"):
        shutil.copy(EXPORTS / name, out / name)
    if with_elo:
        shutil.copy(EXPORTS / "forecast_dataset.json", out / "forecast_dataset.elo.json")
    return out


ALL_MODELS = ["elo", "incumbent"]


def test_payload_without_an_elo_dataset_opens_on_the_incumbent(tmp_path):
    data = build_report.load_data(_exports(tmp_path, with_elo=False), display_names={}, models=ALL_MODELS)
    assert data["model_order"] == ["incumbent"]
    assert data["default_model"] == "incumbent"


def test_payload_with_both_models_opens_on_elo_in_registry_order(tmp_path):
    data = build_report.load_data(_exports(tmp_path, with_elo=True), display_names={}, models=ALL_MODELS)
    assert data["model_order"] == ["elo", "incumbent"]
    assert data["default_model"] == "elo"
    for key in ("elo", "incumbent"):
        model = data["models"][key]
        assert set(model) == {"archive", "seasons", "calibration", "benchmark", "benchmark_pooled", "meta"}
        assert model["meta"]["skill"] is not None
    assert data["historical_cutoffs"], "shared cut-offs are hoisted out of the per-model entries"


def test_each_model_carries_its_own_benchmark(tmp_path):
    exports = _exports(tmp_path, with_elo=True)
    data = build_report.load_data(exports, display_names={}, models=ALL_MODELS)
    for key in ("elo", "incumbent"):
        with open(exports / EXPLORER_MODELS[key].benchmark_file) as f:
            assert data["models"][key]["benchmark"] == json.load(f)


def test_a_published_page_carries_only_the_live_model(tmp_path):
    """The default bundle is what `build_report` ships: Elo alone. The older
    model is frozen - still in the registry and in the backtest, no longer
    simulated for the running season."""
    data = build_report.load_data(_exports(tmp_path, with_elo=True), display_names={})
    assert data["model_order"] == ["elo"]
    assert data["default_model"] == "elo"


def test_seasons_argument_bundles_a_subset_and_still_describes_the_archive(tmp_path):
    """A published page carries the running season; the header still needs to
    say how much history stands behind the calibration below it."""
    exports = _exports(tmp_path, with_elo=True)
    everything = build_report.load_data(exports, display_names={})
    latest = max(everything["models"]["elo"]["seasons"])

    data = build_report.load_data(exports, display_names={}, seasons=[latest])

    assert list(data["models"]["elo"]["seasons"]) == [latest]
    assert data["archive"]["seasons"] == len(everything["models"]["elo"]["seasons"])
    assert data["archive"]["bundled"] == [latest]
    assert data["archive"]["dates"] > len(data["models"]["elo"]["seasons"][latest]["dates"])


def test_models_argument_pins_the_bundle(tmp_path):
    data = build_report.load_data(_exports(tmp_path, with_elo=True), display_names={}, models=["incumbent"])
    assert data["model_order"] == ["incumbent"]

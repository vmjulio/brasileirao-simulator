"""The models the forecast explorer can show, and where each one's files live.

One entry per model is the single place that ties together the simulator that
produces its season forecasts, the directory its Monte Carlo pickles go to, the
dataset the exporter writes from them, and the chancedegol benchmark the page
shows beside them. `backfill`, `topup_iterations`, `export_forecast_dataset`
and the report build all look models up here, so `--model elo` means the same
files everywhere.

The incumbent's pickles stay where they have always been, `RESULTS_DIRECTORY`
(`files/pkl`); every other model gets its own directory under
`MODEL_RESULTS_DIRECTORY`, so no run of another model can add iterations to
the incumbent's archive.
"""

from dataclasses import dataclass

from brasileirao_simulator.config.settings import RESULTS_DIRECTORY

MODEL_RESULTS_DIRECTORY = "files/pkl_models"


@dataclass(frozen=True)
class ExplorerModel:
    key: str
    simulator: str
    results_directory: str
    dataset_file: str
    benchmark_file: str
    benchmark_pooled_file: str


EXPLORER_MODELS = {
    model.key: model
    for model in (
        ExplorerModel(
            key="elo",
            simulator="elo",
            results_directory=f"{MODEL_RESULTS_DIRECTORY}/elo",
            dataset_file="forecast_dataset.elo.json",
            benchmark_file="benchmark_elo_ten.json",
            benchmark_pooled_file="benchmark_elo_ten_pooled.json",
        ),
        ExplorerModel(
            key="incumbent",
            simulator="batch",
            results_directory=RESULTS_DIRECTORY,
            dataset_file="forecast_dataset.json",
            benchmark_file="benchmark.json",
            benchmark_pooled_file="benchmark_pooled.json",
        ),
    )
}

# Shown first when the page opens; the dropdown lists models in the order above.
DEFAULT_EXPLORER_MODEL = "elo"


def explorer_model(key: str) -> ExplorerModel:
    if key not in EXPLORER_MODELS:
        raise ValueError(f"unknown explorer model {key!r}; choose from {sorted(EXPLORER_MODELS)}")
    return EXPLORER_MODELS[key]

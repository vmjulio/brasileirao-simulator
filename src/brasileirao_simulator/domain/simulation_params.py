from dataclasses import dataclass
from typing import Optional


@dataclass
class SimulationParams:
    # season has no default and so must come first: a dataclass cannot place a
    # defaulted field before a required one. It is required because a default is
    # how a 2026 run ends up reading 2025 data unnoticed.
    season: int
    iterations: int = 500
    max_batch_size: int = 50
    load_results: bool = True
    ignore_results_after: str = None
    strategy: Optional[str] = "average"

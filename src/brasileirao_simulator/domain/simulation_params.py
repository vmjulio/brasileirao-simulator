from dataclasses import dataclass
from typing import Optional


@dataclass
class SimulationParams:
    iterations:int = 500
    max_batch_size: int = 50
    load_results: bool = True
    ignore_results_after: str = None
    strategy: Optional[str] = "average"

from dataclasses import dataclass
from typing import Optional


@dataclass
class SimulationParams:
    iterations:int = 100
    max_batch_size: int = 50
    load_results: bool = True
    strategy: Optional[str] = "average"
from dataclasses import dataclass


@dataclass
class SimulationParams:
    iterations:int = 100
    max_batch_size: int = 10
    load_results: bool = True
    strategy: str = "average"

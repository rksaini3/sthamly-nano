from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class GridConfig:
    """
    One configuration ("frame") of the grid.
    The chip is reconfigured between layers: each layer gets its own frame
    in which some PEs are active (assigned to that layer) and the rest idle.
    This is the 'polymorphic' idea: same grid, different circuit per layer.
    """
    layer_index: int
    layer_name: str
    kind: str
    active_pes: int
    cells: List[Optional[int]]   # length rows*cols; layer_index or None (idle)


class GridModel:
    """2D grid of identical processing elements (PEs), each one MAC unit."""

    def __init__(self, rows: int, cols: int):
        if rows < 1 or cols < 1:
            raise ValueError("rows and cols must be >= 1")
        self.rows = rows
        self.cols = cols

    @property
    def size(self) -> int:
        return self.rows * self.cols

    def index(self, r: int, c: int) -> int:
        return r * self.cols + c

    def position(self, i: int):
        return divmod(i, self.cols)  # (row, col)

    def make_config(self, layer_index: int, layer_name: str, kind: str,
                    pes_needed: int) -> GridConfig:
        """Fill PEs row by row (row-major) for one layer; the rest stay idle."""
        n = max(0, min(pes_needed, self.size))
        cells: List[Optional[int]] = [None] * self.size
        for i in range(n):
            cells[i] = layer_index
        return GridConfig(layer_index, layer_name, kind, n, cells)

    def utilization(self, cfg: GridConfig) -> float:
        return cfg.active_pes / self.size

    def to_dict(self) -> Dict:
        return {"rows": self.rows, "cols": self.cols, "size": self.size}
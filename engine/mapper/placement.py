import math
from dataclasses import dataclass
from typing import List

from engine.ir.ir_builder import ModelIR
from engine.mapper.grid_model import GridModel, GridConfig


@dataclass
class Placement:
    grid: GridModel
    frames: List[GridConfig]   # one frame per compute layer
    passes: List[int]          # how many times each frame is reused (tiling)

    @property
    def total_passes(self) -> int:
        return sum(self.passes)

    def avg_utilization(self) -> float:
        if not self.frames:
            return 0.0
        return sum(self.grid.utilization(f) for f in self.frames) / len(self.frames)

    def to_dict(self) -> dict:
        return {
            "grid": self.grid.to_dict(),
            "avg_utilization": self.avg_utilization(),
            "frames": [
                {
                    "layer_index": f.layer_index,
                    "layer_name": f.layer_name,
                    "kind": f.kind,
                    "active_pes": f.active_pes,
                    "passes": p,
                    "cells": f.cells,
                }
                for f, p in zip(self.frames, self.passes)
            ],
        }


def place(ir: ModelIR, grid: GridModel) -> Placement:
    """
    Map every compute layer (macs > 0) onto the grid, one frame per layer.
    If a layer needs more MACs than the grid has PEs, the same frame is
    reused several times (tiling): passes = ceil(macs / active_pes).
    Activations like ReLU are not mapped here; they are assumed to be done
    by a small post-processing unit (not modeled yet).
    """
    frames: List[GridConfig] = []
    passes: List[int] = []

    for layer in ir.layers:
        if layer.macs == 0:
            continue
        pes = min(layer.macs, grid.size)
        frame = grid.make_config(layer.index, layer.name, layer.kind, pes)
        frames.append(frame)
        passes.append(math.ceil(layer.macs / frame.active_pes))

    return Placement(grid=grid, frames=frames, passes=passes)
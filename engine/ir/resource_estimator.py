from dataclasses import dataclass, asdict
from typing import List

from engine.ir.ir_builder import ModelIR
from engine.tech import get_tech


@dataclass
class LayerEstimate:
    name: str
    kind: str
    macs: int
    units_used: int
    energy_uj: float
    time_us: float


@dataclass
class Estimate:
    tech: str
    bits: int
    units: int                  # MAC units available on the chip
    layers: List[LayerEstimate]
    total_energy_uj: float
    total_time_us: float
    area_mm2: float
    weight_bytes: int
    units_for_full_parallel: int  # biggest layer's MACs (upper bound, info only)
    assumptions: str
    label: str = "simulated estimate"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def estimate(ir: ModelIR, bits: int, tech_name: str = "silicon_fpga",
             units: int = 64) -> Estimate:
    """
    Estimate cost of ONE inference.
    Layers run one after another. Each layer uses up to `units` MAC units
    (never more than it has MACs). Only MAC energy is counted; memory and
    data-movement energy are NOT included, so real numbers will be higher.
    """
    tech = get_tech(tech_name)
    rows: List[LayerEstimate] = []
    total_energy = 0.0
    total_time = 0.0

    for layer in ir.layers:
        if layer.macs == 0:
            continue
        used = min(units, layer.macs)
        c = tech.cost(layer.macs, bits, used)
        rows.append(LayerEstimate(layer.name, layer.kind, layer.macs, used,
                                  c.energy_uj, c.time_us))
        total_energy += c.energy_uj
        total_time += c.time_us

    peak_units = max((r.units_used for r in rows), default=1)

    return Estimate(
        tech=tech.name,
        bits=bits,
        units=peak_units,
        layers=rows,
        total_energy_uj=total_energy,
        total_time_us=total_time,
        area_mm2=tech.area_mm2(peak_units, bits),
        weight_bytes=(ir.total_params * bits + 7) // 8,
        units_for_full_parallel=max((l.macs for l in ir.layers), default=0),
        assumptions=tech.assumptions,
    )
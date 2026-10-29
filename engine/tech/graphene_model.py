from engine.tech.base import TechModel
from engine.tech.silicon_fpga import SiliconFPGA


class GrapheneWhatIf(TechModel):
    """
    HYPOTHETICAL graphene model. NOT measured, NOT from a real device.

    Reliable graphene logic transistors do not exist yet (bandgap problem),
    so this is a what-if: it takes the silicon estimate and applies scaling
    factors that are PLACEHOLDERS. Replace them with values from published
    research (Phase 5) and write the paper/source in `assumptions`.

    Meaning of the factors: graphene_value = silicon_value * factor
    """

    name = "graphene_what_if"

    # PLACEHOLDERS: change these only after reading real papers.
    ENERGY_FACTOR = 0.5   # assumed: half the energy per MAC
    LATENCY_FACTOR = 0.2  # assumed: 5x faster switching
    AREA_FACTOR = 0.5     # assumed: half the area per MAC

    assumptions = (
        "HYPOTHETICAL what-if. Silicon FPGA estimate scaled by placeholder "
        "factors (energy x0.5, latency x0.2, area x0.5). Not measured, not "
        "from a published device. Simulated estimate."
    )

    def __init__(self):
        self._si = SiliconFPGA()

    def energy_per_mac_pj(self, bits: int) -> float:
        return self._si.energy_per_mac_pj(bits) * self.ENERGY_FACTOR

    def mac_latency_ns(self, bits: int) -> float:
        return self._si.mac_latency_ns(bits) * self.LATENCY_FACTOR

    def area_per_mac_um2(self, bits: int) -> float:
        return self._si.area_per_mac_um2(bits) * self.AREA_FACTOR
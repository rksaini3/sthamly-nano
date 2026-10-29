from engine.tech.base import TechModel


class SiliconFPGA(TechModel):
    """
    Silicon FPGA estimate.

    Numbers are ASSUMPTIONS, not measurements. Base values are the commonly
    quoted 45nm ASIC figures (Horowitz, ISSCC 2014: 8-bit int mult ~0.2 pJ,
    add ~0.03 pJ, mult area ~282 um2, add ~36 um2), multiplied by rough
    FPGA-vs-ASIC overhead factors. Verify the sources before quoting them,
    and replace with measured values after the FPGA run (Phase 4).
    """

    name = "silicon_fpga"
    assumptions = (
        "45nm ASIC base (Horowitz 2014) x FPGA overhead factors "
        "(energy x12, area x35, delay x3.4); clock 200 MHz. Simulated estimate."
    )

    ENERGY_FACTOR = 12.0
    AREA_FACTOR = 35.0
    CLOCK_MHZ = 200.0

    @staticmethod
    def _scale(bits: int) -> float:
        return bits / 8.0

    def energy_per_mac_pj(self, bits: int) -> float:
        s = self._scale(bits)
        asic = 0.2 * s * s + 0.03 * s
        return asic * self.ENERGY_FACTOR

    def mac_latency_ns(self, bits: int) -> float:
        # one MAC per clock cycle, pipelined
        return 1000.0 / self.CLOCK_MHZ

    def area_per_mac_um2(self, bits: int) -> float:
        s = self._scale(bits)
        asic = 282.0 * s * s + 36.0 * s
        return asic * self.AREA_FACTOR
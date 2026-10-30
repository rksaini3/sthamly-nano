import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from engine.ir.ir_builder import ModelIR
from engine.ir.resource_estimator import estimate
from engine.tech.silicon_fpga import SiliconFPGA

# name -> (weight_bits, mac_bits, note)
#   weight_bits : bits stored per weight (decides weight size)
#   mac_bits    : operand width the cost model uses for the MAC unit
# W8A8 / W4A8 come from the integer inference model (what the generated
# Verilog really computes). INT8 / INT4 are the older weight-only tests.
PRECISIONS = {
    "FP32": (32, 32, "float32 baseline"),
    "W8A8": (8, 8, "8-bit weights, 8-bit activations (integer inference)"),
    "W4A8": (4, 8, "4-bit weights, 8-bit activations (integer inference); "
                   "MAC cost modelled at 8-bit because activations are 8-bit"),
    "INT8": (8, 8, "weight-only INT8; activations stay FP32 in the accuracy test"),
    "INT4": (4, 4, "weight-only INT4; activations stay FP32 in the accuracy test; "
                   "MAC cost modelled at 4-bit"),
}
ORDER = ["FP32", "W8A8", "INT8", "W4A8", "INT4"]


def _pct_saving(base: float, new: float) -> Optional[float]:
    if base <= 0:
        return None
    return (1.0 - new / base) * 100.0


def _times(base: float, new: float) -> Optional[float]:
    if new <= 0:
        return None
    return base / new


def load_sim_result(log_path: str, lanes: Optional[int] = None) -> Optional[Dict]:
    """
    Read the simulation log written by hardware/testbench/run_infer.sh
    (hardware/testbench/build_infer/sim.log). Returns None if the log is
    missing or incomplete.
    """
    p = Path(log_path)
    if not p.exists():
        return None
    text = p.read_text()

    m = re.search(r"images=(\d+) logit_mismatches=(\d+) pred_mismatches=(\d+)", text)
    if not m:
        return None
    out: Dict = {
        "lanes": lanes,
        "images": int(m.group(1)),
        "logit_mismatches": int(m.group(2)),
        "pred_mismatches": int(m.group(3)),
        "passed": bool(re.search(r"^PASS\b", text, re.M)),
    }
    m = re.search(r"CYCLES_PER_IMAGE=(\d+)", text)
    if m:
        out["cycles_per_image"] = int(m.group(1))
    m = re.search(r"HW_CORRECT=(\d+)/(\d+)", text)
    if m:
        out["hw_correct"] = int(m.group(1))
        out["hw_total"] = int(m.group(2))
    return out


def _sim_block(sim: Optional[Dict]) -> Optional[Dict]:
    if not sim:
        return None
    clock = SiliconFPGA.CLOCK_MHZ
    cycles = sim.get("cycles_per_image")
    lm = sim.get("logit_mismatches")
    pm = sim.get("pred_mismatches")
    exact = bool(sim.get("passed")) and lm == 0 and pm == 0
    return {
        "cycles_per_image": cycles,
        "clock_mhz_assumed": clock,
        "latency_us_at_assumed_clock": (cycles / clock) if cycles else None,
        "lanes": sim.get("lanes"),
        "images_checked": sim.get("images"),
        "logit_mismatches": lm,
        "pred_mismatches": pm,
        "hw_correct": sim.get("hw_correct"),
        "hw_total": sim.get("hw_total"),
        "bit_exact": exact,
        "label": "Simulation result. The clock frequency is assumed, "
                 "not measured on a board.",
    }


def _not_claimed(results: List[Dict], sim_block: Optional[Dict]) -> List[str]:
    lines = [
        "Accuracy numbers are measured (MNIST test set, software simulation of "
        "quantization). Everything else here is a simulated estimate.",
        "Energy counts only MAC operations. Memory and data movement are not "
        "included, and on real chips they are often the larger part.",
        "Silicon numbers are assumptions scaled from published ASIC figures, not "
        "measured on a board. Replace them after the FPGA run.",
        "The graphene column is a hypothetical what-if using placeholder factors. "
        "It is not from a real device.",
        "FP32 baseline cost is extrapolated with the same simple bit-width formula; "
        "a real FP32 unit differs.",
    ]
    if any(r["weight_bits"] == 4 for r in results):
        lines.append(
            "4-bit weights: the generated Verilog stores every weight in one byte "
            "and does not pack 4-bit values yet, so the 4-bit size reduction is "
            "theoretical, not realised in the generated hardware.")
    if any(r["precision"] in ("INT8", "INT4") for r in results):
        lines.append(
            "INT8 / INT4 rows are weight-only tests with FP32 activations; the "
            "W8A8 / W4A8 rows are what the integer hardware actually computes.")
    if sim_block:
        lines.append(
            "Cycle count comes from a simulation of the generated core; the clock "
            "frequency is assumed. No board measurement exists yet.")
    return lines


def build_report(model_name: str, acc_rows: List[Dict], ir: ModelIR,
                 units: int = 64,
                 techs=("silicon_fpga", "graphene_what_if"),
                 sim: Optional[Dict] = None) -> Dict:
    """
    Combine measured accuracy with estimated cost into one report dict.
    acc_rows : [{"name", "acc", "se", "verdict"}], names from PRECISIONS
    sim      : optional dict from load_sim_result (simulation of the Verilog)
    Baseline = FP32 on silicon_fpga. Every ratio is computed from the
    estimates, nothing is hardcoded.
    """
    acc_by_name = {r["name"]: r for r in acc_rows}
    unknown = [n for n in acc_by_name if n not in PRECISIONS]
    if unknown:
        raise ValueError("Unknown precision name(s): %s. Known: %s"
                         % (unknown, list(PRECISIONS)))

    results = []
    for name in ORDER:
        if name not in acc_by_name:
            continue
        wbits, mbits, note = PRECISIONS[name]
        a = acc_by_name[name]
        for tech in techs:
            if name == "FP32" and tech != "silicon_fpga":
                continue  # FP32 only as the silicon baseline
            est = estimate(ir, mbits, tech, units)
            results.append({
                "precision": name,
                "bits": mbits,
                "weight_bits": wbits,
                "mac_bits": mbits,
                "precision_note": note,
                "tech": tech,
                "accuracy": a["acc"],
                "accuracy_se": a["se"],
                "accuracy_verdict": a["verdict"],
                "energy_uj": est.total_energy_uj,
                "time_us": est.total_time_us,
                "area_mm2": est.area_mm2,
                "weight_bytes": (ir.total_params * wbits + 7) // 8,
                "assumptions": est.assumptions,
                "label": est.label,
            })

    base = next((r for r in results
                 if r["precision"] == "FP32" and r["tech"] == "silicon_fpga"), None)
    if base:
        for r in results:
            r["energy_saving_pct"] = _pct_saving(base["energy_uj"], r["energy_uj"])
            r["speedup_x"] = _times(base["time_us"], r["time_us"])
            r["size_reduction_x"] = _times(base["weight_bytes"], r["weight_bytes"])
            r["area_saving_pct"] = _pct_saving(base["area_mm2"], r["area_mm2"])

    sim_block = _sim_block(sim)
    return {
        "model": model_name,
        "grid_units": units,
        "total_macs": ir.total_macs,
        "total_params": ir.total_params,
        "baseline": "FP32 on silicon_fpga",
        "results": results,
        "hardware_simulation": sim_block,
        "not_claimed": _not_claimed(results, sim_block),
    }


def save_report(report: Dict, out_path: str) -> str:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2))
    return str(p)


def print_table(report: Dict) -> None:
    print(f"Model: {report['model']}  |  baseline: {report['baseline']}")
    print(f"{'precision':9} {'tech':17} {'acc%':>7} {'energy uJ':>10} "
          f"{'time us':>9} {'save%':>7} {'speedx':>7} {'sizex':>6}")
    for r in report["results"]:
        sv = r.get("energy_saving_pct")
        sp = r.get("speedup_x")
        sz = r.get("size_reduction_x")
        print(f"{r['precision']:9} {r['tech']:17} {r['accuracy'] * 100:7.2f} "
              f"{r['energy_uj']:10.2f} {r['time_us']:9.1f} "
              f"{(sv if sv is not None else 0):7.1f} "
              f"{(sp if sp is not None else 0):7.2f} "
              f"{(sz if sz is not None else 0):6.1f}")

    sim = report.get("hardware_simulation")
    if sim:
        print()
        print("Hardware simulation (generated Verilog):")
        print(f"  images checked   : {sim['images_checked']}")
        print(f"  bit-exact vs Python integer model: {sim['bit_exact']}")
        if sim.get("hw_correct") is not None:
            print(f"  correct on those : {sim['hw_correct']}/{sim['hw_total']}")
        if sim.get("cycles_per_image"):
            print(f"  cycles per image : {sim['cycles_per_image']}  "
                  f"(~{sim['latency_us_at_assumed_clock']:.1f} us at an assumed "
                  f"{sim['clock_mhz_assumed']:.0f} MHz)")

    print("\nNOTE: energy/time/area are simulated estimates. "
          "Accuracy is measured. See not_claimed in the JSON.")
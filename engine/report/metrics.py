import json
from pathlib import Path
from typing import Dict, List, Optional

from engine.ir.ir_builder import ModelIR
from engine.ir.resource_estimator import estimate

BITS_BY_NAME = {"FP32": 32, "INT8": 8, "INT4": 4}

NOT_CLAIMED = [
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


def _pct_saving(base: float, new: float) -> Optional[float]:
    if base <= 0:
        return None
    return (1.0 - new / base) * 100.0


def _times(base: float, new: float) -> Optional[float]:
    if new <= 0:
        return None
    return base / new


def build_report(model_name: str, acc_rows: List[Dict], ir: ModelIR,
                 units: int = 64,
                 techs=("silicon_fpga", "graphene_what_if")) -> Dict:
    """
    Combine measured accuracy with estimated cost into one report dict.
    Baseline = FP32 on silicon_fpga. Every ratio is computed from the
    estimates, nothing is hardcoded.
    """
    acc_by_name = {r["name"]: r for r in acc_rows}

    scenarios = []
    for name in ("FP32", "INT8", "INT4"):
        if name not in acc_by_name:
            continue
        bits = BITS_BY_NAME[name]
        for tech in techs:
            if name == "FP32" and tech != "silicon_fpga":
                continue  # FP32 only as the silicon baseline
            scenarios.append((name, bits, tech))

    results = []
    for name, bits, tech in scenarios:
        est = estimate(ir, bits, tech, units)
        a = acc_by_name[name]
        results.append({
            "precision": name,
            "bits": bits,
            "tech": tech,
            "accuracy": a["acc"],
            "accuracy_se": a["se"],
            "accuracy_verdict": a["verdict"],
            "energy_uj": est.total_energy_uj,
            "time_us": est.total_time_us,
            "area_mm2": est.area_mm2,
            "weight_bytes": est.weight_bytes,
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

    return {
        "model": model_name,
        "grid_units": units,
        "total_macs": ir.total_macs,
        "total_params": ir.total_params,
        "baseline": "FP32 on silicon_fpga",
        "results": results,
        "not_claimed": NOT_CLAIMED,
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
    print("\nNOTE: energy/time/area are simulated estimates. "
          "Accuracy is measured. See not_claimed in the JSON.")
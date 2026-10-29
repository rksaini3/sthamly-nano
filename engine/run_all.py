import argparse
import json
import sys
from pathlib import Path

import torch
from torchvision import datasets, transforms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from samples.sample_model import MLP, CKPT, train  # noqa: E402
from engine.quantizer.quantize import quantize_model  # noqa: E402
from engine.quantizer.accuracy_check import evaluate, verdict  # noqa: E402
from engine.ir.ir_builder import build_ir  # noqa: E402
from engine.mapper.grid_model import GridModel  # noqa: E402
from engine.mapper.placement import place  # noqa: E402
from engine.verilog_gen.config_bits import build_config  # noqa: E402
from engine.verilog_gen.verilog_writer import write_verilog  # noqa: E402
from engine.report.metrics import build_report, save_report, print_table  # noqa: E402


def measure_accuracy(model):
    ds = datasets.MNIST(str(ROOT / "data" / "mnist"), train=False,
                        download=True, transform=transforms.ToTensor())
    loader = torch.utils.data.DataLoader(ds, batch_size=500)

    acc0, se0 = evaluate(model, loader)
    rows = [{"name": "FP32", "acc": acc0, "se": se0, "verdict": "baseline"}]
    for bits in (8, 4):
        qm, _ = quantize_model(model, bits)
        acc, se = evaluate(qm, loader)
        rows.append({"name": f"INT{bits}", "acc": acc, "se": se,
                     "verdict": verdict(acc0, se0, acc, se)})
    return rows


def main():
    ap = argparse.ArgumentParser(description="Sthamly Engine: full pipeline")
    ap.add_argument("--rows", type=int, default=8, help="grid rows")
    ap.add_argument("--cols", type=int, default=8, help="grid cols")
    ap.add_argument("--bits", type=int, default=8, choices=[4, 8],
                    help="precision written into the Verilog")
    args = ap.parse_args()

    # 1. model
    if not CKPT.exists():
        print("No checkpoint found, training the sample MLP (about 1-2 min)...")
        train()
    model = MLP()
    model.load_state_dict(torch.load(CKPT))

    # 2. measured accuracy (FP32 / INT8 / INT4)
    acc_rows = measure_accuracy(model)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "engine_mnist.json").write_text(json.dumps(acc_rows, indent=2))

    # 3. IR, then cost report (accuracy measured, cost estimated)
    ir = build_ir(model, (1, 1, 28, 28))
    grid = GridModel(args.rows, args.cols)
    report = build_report("mnist_mlp", acc_rows, ir, units=grid.size)
    report_path = save_report(report, str(ROOT / "results" / "engine_report.json"))

    # 4. mapping and Verilog
    placement = place(ir, grid)
    configs = build_config(placement)
    v_path = write_verilog(configs, args.rows, args.cols, args.bits,
                           str(ROOT / "hardware" / "verilog" / "sthamly_top.v"),
                           "mnist_mlp")

    # 5. summary
    print()
    print_table(report)
    print(f"\nGrid {args.rows}x{args.cols}, frames: {len(configs)}, "
          f"avg utilization: {placement.avg_utilization():.2f}")
    print("Report :", report_path)
    print("Verilog:", v_path)
    if (args.rows, args.cols, args.bits) != (8, 8, 8):
        print("NOTE: tb_top.v is written for an 8x8 grid with 8 bits, 2 frames. "
              "Edit N and BITS at its top before running the testbench.")
    else:
        print("Next: bash hardware/testbench/run_verilator.sh")


if __name__ == "__main__":
    main()
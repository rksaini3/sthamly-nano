import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from samples.sample_model import MLP, CKPT, train  # noqa: E402
from engine.quantizer.int_infer import (  # noqa: E402
    build_int_model, forward_int, load_mnist, quantize_input)
from engine.verilog_gen.weights_export import export_weights  # noqa: E402
from engine.verilog_gen.infer_writer import write_infer_verilog  # noqa: E402
from engine.verilog_gen.tb_writer import write_tb_verilog  # noqa: E402


def acc_se(correct: float, n: int):
    se = float(np.sqrt(correct * (1 - correct) / n))
    return correct, se


def main():
    ap = argparse.ArgumentParser(
        description="Sthamly Engine: integer model -> hex files -> Verilog + testbench")
    ap.add_argument("--bits", type=int, default=8, choices=[4, 8],
                    help="weight bits (activations are always 8-bit)")
    ap.add_argument("--lanes", type=int, default=16, help="MAC lanes per cycle")
    ap.add_argument("--images", type=int, default=100,
                    help="how many test images the testbench runs")
    ap.add_argument("--data-dir", default="hardware/verilog/data",
                    help="hex folder, relative to the repo root")
    args = ap.parse_args()

    if not (1 <= args.images <= 10000):
        raise SystemExit("--images must be between 1 and 10000")

    # 1. model
    if not CKPT.exists():
        print("No checkpoint found, training the sample MLP (about 1-2 min)...")
        train()
    model = MLP()
    model.load_state_dict(torch.load(CKPT))
    model.eval()

    # 2. data
    x_train, _ = load_mnist(train=True)
    calib = torch.from_numpy(x_train[:2000])
    x_test, y_test = load_mnist(train=False)

    # 3. FP32 reference accuracy (full test set)
    with torch.no_grad():
        fp_pred = model(torch.from_numpy(x_test).view(-1, 1, 28, 28)).argmax(1).numpy()
    fp_acc, fp_se = acc_se(float((fp_pred == y_test).mean()), len(y_test))

    # 4. integer model, full test set (this is the reference the hardware must match)
    im = build_int_model(model, args.bits, calib)
    x_q = quantize_input(x_test)
    logits = forward_int(im, x_q)
    int_pred = logits.argmax(axis=1)
    int_acc, int_se = acc_se(float((int_pred == y_test).mean()), len(y_test))

    # 5. hex files for the first N images
    n = args.images
    out_dir = ROOT / args.data_dir
    manifest = export_weights(im, str(out_dir), args.lanes, x_q[:n], y_test[:n], logits[:n])

    # 6. Verilog core and testbench
    v_path = write_infer_verilog(manifest, args.data_dir,
                                 str(ROOT / "hardware" / "verilog" / "sthamly_infer.v"))
    tb_path = write_tb_verilog(manifest, args.data_dir,
                               str(ROOT / "hardware" / "testbench" / "tb_infer.v"))

    # 7. summary
    summary = {
        "weight_bits": args.bits,
        "activation_bits": 8,
        "lanes": args.lanes,
        "testbench_images": n,
        "fp32_accuracy": fp_acc,
        "fp32_se": fp_se,
        "integer_model_accuracy": int_acc,
        "integer_model_se": int_se,
        "accuracy_label": "measured, full 10000-image MNIST test set, Python integer model",
        "hardware_note": "Verilog must reproduce the integer model bit for bit; "
                         "run hardware/testbench/run_infer.sh to check.",
    }
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "engine_infer.json").write_text(json.dumps(summary, indent=2))

    print()
    print(f"FP32          : {fp_acc * 100:.2f}% +/- {fp_se * 100:.2f}")
    print(f"W{args.bits}A8 integer : {int_acc * 100:.2f}% +/- {int_se * 100:.2f}"
          f"   (this is what the chip must match)")
    print(f"Hex files     : {out_dir}")
    print(f"Verilog core  : {v_path}")
    print(f"Testbench     : {tb_path}  ({n} images)")
    print("Next: bash hardware/testbench/run_infer.sh")


if __name__ == "__main__":
    main()
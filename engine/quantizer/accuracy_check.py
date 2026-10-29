import json
import math
import sys
from pathlib import Path

import torch
from torchvision import datasets, transforms

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.quantizer.quantize import quantize_model  # noqa: E402
from samples.sample_model import MLP, CKPT  # noqa: E402


def evaluate(model, loader):
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for x, y in loader:
            correct += (model(x).argmax(1) == y).sum().item()
            total += len(y)
    acc = correct / total
    se = math.sqrt(acc * (1 - acc) / total)
    return acc, se


def verdict(acc_ref, se_ref, acc_q, se_q):
    """Same rule as NanoBrain: saaf fark only if gap > 2x combined error."""
    gap = acc_ref - acc_q
    combined = math.sqrt(se_ref ** 2 + se_q ** 2)
    return "saaf fark" if gap > 2 * combined else "saaf fark nahi"


def main():
    ds = datasets.MNIST(str(ROOT / "data" / "mnist"), train=False,
                        download=True, transform=transforms.ToTensor())
    loader = torch.utils.data.DataLoader(ds, batch_size=500)

    model = MLP()
    model.load_state_dict(torch.load(CKPT))

    acc0, se0 = evaluate(model, loader)
    rows = [{"name": "FP32", "acc": acc0, "se": se0, "verdict": "baseline"}]
    for bits in (8, 4):
        qm, _ = quantize_model(model, bits)
        acc, se = evaluate(qm, loader)
        rows.append({"name": f"INT{bits}", "acc": acc, "se": se,
                     "verdict": verdict(acc0, se0, acc, se)})

    print(f"{'model':8} {'accuracy':>10} {'± se':>8}  verdict")
    for r in rows:
        print(f"{r['name']:8} {r['acc'] * 100:9.2f}% {r['se'] * 100:7.2f}%  {r['verdict']}")

    out = ROOT / "results" / "engine_mnist.json"
    out.write_text(json.dumps(rows, indent=2))
    print("saved", out)


if __name__ == "__main__":
    main()
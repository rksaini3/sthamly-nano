import copy
import torch


def quantize_tensor(w: torch.Tensor, bits: int):
    """Symmetric per-tensor quantization. Returns (int tensor, scale)."""
    qmax = 2 ** (bits - 1) - 1
    scale = w.abs().max().clamp(min=1e-8) / qmax
    q = torch.clamp(torch.round(w / scale), -qmax - 1, qmax).to(torch.int8)
    return q, scale


def quantize_model(model: torch.nn.Module, bits: int):
    """
    Returns (fake-quant model for accuracy testing, packed int weights for hardware).
    Weights (2D+) are quantized; biases stay FP32.
    """
    qmodel = copy.deepcopy(model)
    packed = {}
    for name, p in qmodel.named_parameters():
        if p.dim() < 2:
            continue
        q, s = quantize_tensor(p.data, bits)
        packed[name] = {"q": q, "scale": float(s), "bits": bits}
        p.data = q.float() * s
    return qmodel, packed
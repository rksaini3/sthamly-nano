from dataclasses import dataclass, asdict
from typing import List, Tuple

import torch
import torch.nn as nn


@dataclass
class LayerIR:
    index: int
    name: str
    kind: str                 # "linear" | "conv2d" | "relu" | "other"
    in_shape: Tuple[int, ...]
    out_shape: Tuple[int, ...]
    macs: int                 # multiply-accumulates per sample
    params: int               # weight count (bias excluded)
    elementwise_ops: int = 0  # for activations like ReLU


@dataclass
class ModelIR:
    layers: List[LayerIR]

    @property
    def total_macs(self) -> int:
        return sum(l.macs for l in self.layers)

    @property
    def total_params(self) -> int:
        return sum(l.params for l in self.layers)

    def to_dict(self) -> dict:
        return {
            "total_macs": self.total_macs,
            "total_params": self.total_params,
            "layers": [asdict(l) for l in self.layers],
        }


def _numel_per_sample(shape) -> int:
    n = 1
    for d in shape[1:]:
        n *= int(d)
    return n


def build_ir(model: nn.Module, input_shape: Tuple[int, ...]) -> ModelIR:
    """
    Run one dummy sample through the model with hooks and record each
    leaf layer. input_shape example for MNIST: (1, 1, 28, 28).
    Counts are exact for Linear and Conv2d; everything else gets 0 MACs.
    """
    layers: List[LayerIR] = []
    hooks = []

    def make_hook(name: str):
        def hook(module, inputs, output):
            x = inputs[0]
            in_shape = tuple(x.shape)
            out_shape = tuple(output.shape)
            idx = len(layers)

            if isinstance(module, nn.Linear):
                macs = module.in_features * module.out_features
                params = module.weight.numel()
                layers.append(LayerIR(idx, name, "linear", in_shape, out_shape,
                                      macs, params))
            elif isinstance(module, nn.Conv2d):
                kh, kw = module.kernel_size
                out_c, out_h, out_w = out_shape[1], out_shape[2], out_shape[3]
                macs = (out_h * out_w * out_c
                        * (module.in_channels // module.groups) * kh * kw)
                params = module.weight.numel()
                layers.append(LayerIR(idx, name, "conv2d", in_shape, out_shape,
                                      macs, params))
            elif isinstance(module, nn.ReLU):
                layers.append(LayerIR(idx, name, "relu", in_shape, out_shape,
                                      0, 0, elementwise_ops=_numel_per_sample(out_shape)))
            else:
                layers.append(LayerIR(idx, name, "other", in_shape, out_shape, 0, 0))
        return hook

    for name, module in model.named_modules():
        if len(list(module.children())) == 0:  # leaf layers only
            hooks.append(module.register_forward_hook(make_hook(name)))

    model.eval()
    with torch.no_grad():
        model(torch.zeros(*input_shape))

    for h in hooks:
        h.remove()

    return ModelIR(layers=layers)
import json
from pathlib import Path
from typing import Dict

import numpy as np


def _chunks(n: int, lanes: int) -> int:
    return (n + lanes - 1) // lanes


def _lane_line(values: np.ndarray, lanes: int) -> str:
    """
    One memory line = `lanes` signed/unsigned bytes. Lane j sits in bits
    [8*j +: 8], so the text is written lane (lanes-1) first, lane 0 last.
    """
    padded = np.zeros(lanes, dtype=np.int64)
    padded[: len(values)] = values
    return "".join(f"{int(v) & 0xFF:02x}" for v in padded[::-1])


def _write_lines(path: Path, lines) -> None:
    path.write_text("\n".join(lines) + "\n")


def export_weights(im, out_dir: str, lanes: int, x_q: np.ndarray,
                   y: np.ndarray, logits: np.ndarray) -> Dict:
    """
    Write everything the Verilog simulation needs, as hex files for $readmemh.

    im      : IntModel from int_infer.build_int_model (only its fields are read)
    lanes   : MAC lanes per cycle (P). Weights/activations are stored P per line.
    x_q     : int images [N, in_features], values 0..127 (quantize_input output)
    y       : true labels [N]
    logits  : expected integer logits [N, out_last] from forward_int
              (the testbench must reproduce them bit for bit)

    Files (in out_dir):
      layer{i}_w.hex   one line per (neuron, chunk); address = neuron*chunks + chunk
      layer{i}_b.hex   one 8-digit int32 two's-complement line per neuron
      test_x.hex       one line per (image, chunk); address = image*chunks0 + chunk
      test_y.hex       one 2-digit label per image
      test_logits.hex  one 8-digit int32 line per (image, class)
      manifest.json    sizes, requant constants, file names
    """
    if lanes < 1:
        raise ValueError("lanes must be >= 1")
    x_q = np.asarray(x_q)
    y = np.asarray(y)
    logits = np.asarray(logits)
    n_img = x_q.shape[0]
    first, last = im.layers[0], im.layers[-1]

    if x_q.ndim != 2 or x_q.shape[1] != first.in_features:
        raise ValueError("x_q must have shape [N, %d]" % first.in_features)
    if x_q.min() < 0 or x_q.max() > im.input_max:
        raise ValueError("x_q must be in 0..%d" % im.input_max)
    if y.shape != (n_img,):
        raise ValueError("y must have shape [N]")
    if logits.shape != (n_img, last.out_features):
        raise ValueError("logits must have shape [N, %d]" % last.out_features)
    if np.abs(logits).max() >= 2 ** 31:
        raise ValueError("logits do not fit in int32")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    layer_info = []
    for i, L in enumerate(im.layers):
        ch = _chunks(L.in_features, lanes)
        w_lines = []
        for o in range(L.out_features):
            row = L.w_q[o].astype(np.int64)
            for c in range(ch):
                w_lines.append(_lane_line(row[c * lanes:(c + 1) * lanes], lanes))
        _write_lines(out / f"layer{i}_w.hex", w_lines)

        b_lines = [f"{int(b) & 0xFFFFFFFF:08x}" for b in L.b_q.astype(np.int64)]
        _write_lines(out / f"layer{i}_b.hex", b_lines)

        layer_info.append({
            "index": i,
            "name": L.name,
            "in_features": int(L.in_features),
            "out_features": int(L.out_features),
            "chunks": ch,
            "in_pad": ch * lanes,
            "m": int(L.m),
            "shift": int(L.shift),
            "relu": bool(L.relu),
            "weights_file": f"layer{i}_w.hex",
            "bias_file": f"layer{i}_b.hex",
        })

    ch0 = _chunks(first.in_features, lanes)
    x_lines = []
    for n in range(n_img):
        row = x_q[n].astype(np.int64)
        for c in range(ch0):
            x_lines.append(_lane_line(row[c * lanes:(c + 1) * lanes], lanes))
    _write_lines(out / "test_x.hex", x_lines)
    _write_lines(out / "test_y.hex", [f"{int(v) & 0xFF:02x}" for v in y])
    _write_lines(out / "test_logits.hex",
                 [f"{int(v) & 0xFFFFFFFF:08x}" for v in logits.reshape(-1)])

    manifest = {
        "lanes": lanes,
        "w_bits": int(im.w_bits),
        "act_bits": 8,
        "input_max": int(im.input_max),
        "n_images": int(n_img),
        "num_classes": int(last.out_features),
        "layers": layer_info,
        "test_x_file": "test_x.hex",
        "test_y_file": "test_y.hex",
        "test_logits_file": "test_logits.hex",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
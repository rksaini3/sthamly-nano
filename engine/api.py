import json
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from samples.sample_model import MLP, CKPT, train  # noqa: E402
from engine.quantizer.accuracy_check import verdict  # noqa: E402
from engine.quantizer.int_infer import (  # noqa: E402
    build_int_model, forward_int, load_mnist, quantize_input)
from engine.ir.ir_builder import build_ir  # noqa: E402
from engine.mapper.grid_model import GridModel  # noqa: E402
from engine.mapper.placement import place  # noqa: E402
from engine.verilog_gen.config_bits import build_config  # noqa: E402
from engine.verilog_gen.verilog_writer import generate_verilog  # noqa: E402
from engine.report.metrics import build_report, load_sim_result  # noqa: E402
from engine.report.pdf_report import make_pdf  # noqa: E402

SIM_LOG = ROOT / "hardware" / "testbench" / "build_infer" / "sim.log"
MANIFEST = ROOT / "hardware" / "verilog" / "data" / "manifest.json"

_cache = {}


def _acc_se(pred: np.ndarray, y: np.ndarray):
    acc = float((pred == y).mean())
    return acc, float(np.sqrt(acc * (1 - acc) / len(y)))


def _measure():
    """Train if needed, then measure FP32 / W8A8 / W4A8 on the full MNIST test set."""
    if not CKPT.exists():
        print("No checkpoint found, training the sample MLP (about 1-2 min)...")
        train()
    model = MLP()
    model.load_state_dict(torch.load(CKPT))
    model.eval()

    x_train, _ = load_mnist(train=True)
    calib = torch.from_numpy(x_train[:2000])
    x_test, y_test = load_mnist(train=False)

    with torch.no_grad():
        fp_pred = model(torch.from_numpy(x_test).view(-1, 1, 28, 28)).argmax(1).numpy()
    acc0, se0 = _acc_se(fp_pred, y_test)
    rows = [{"name": "FP32", "acc": acc0, "se": se0, "verdict": "baseline"}]

    x_q = quantize_input(x_test)
    for wb in (8, 4):
        im = build_int_model(model, wb, calib)
        pred = forward_int(im, x_q).argmax(axis=1)
        acc, se = _acc_se(pred, y_test)
        rows.append({"name": f"W{wb}A8", "acc": acc, "se": se,
                     "verdict": verdict(acc0, se0, acc, se)})
    return model, rows


def _load():
    """Measure once, reuse for every request."""
    if "model" not in _cache:
        _cache["model"], _cache["acc"] = _measure()
    return _cache["model"], _cache["acc"]


def _sim():
    """Latest hardware simulation result (from run_infer.sh), if any."""
    lanes = None
    wbits = None
    if MANIFEST.exists():
        try:
            m = json.loads(MANIFEST.read_text())
            lanes = m.get("lanes")
            wbits = m.get("w_bits")
        except (OSError, ValueError):
            pass
    return load_sim_result(str(SIM_LOG), lanes), wbits


def _make_report(rows: int, cols: int):
    model, acc_rows = _load()
    ir = build_ir(model, (1, 1, 28, 28))
    grid = GridModel(rows, cols)
    sim, wbits = _sim()
    report = build_report("mnist_mlp", acc_rows, ir, units=grid.size, sim=sim)
    hs = report.get("hardware_simulation")
    if hs and wbits:
        hs["label"] += f" Simulated design: W{wbits}A8."
    return report, ir, grid


def _check_grid(rows: int, cols: int):
    if not (1 <= rows <= 32 and 1 <= cols <= 32):
        raise HTTPException(400, "rows and cols must be between 1 and 32")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Warming up: measuring accuracy on the full test set (takes a while)...")
    _load()
    print("Ready.")
    yield


app = FastAPI(title="Sthamly Engine API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/run")
def run(rows: int = 8, cols: int = 8, bits: int = 8):
    _check_grid(rows, cols)
    if bits not in (4, 8):
        raise HTTPException(400, "bits must be 4 or 8")

    report, ir, grid = _make_report(rows, cols)
    placement = place(ir, grid)
    configs = build_config(placement)

    return {
        "report": report,
        "placement": placement.to_dict(),
        "verilog": generate_verilog(configs, rows, cols, bits, "mnist_mlp"),
    }


@app.get("/report.pdf")
def report_pdf(rows: int = 8, cols: int = 8):
    _check_grid(rows, cols)
    report, _, _ = _make_report(rows, cols)

    with tempfile.TemporaryDirectory() as d:
        path = make_pdf(report, str(Path(d) / "report.pdf"))
        data = Path(path).read_bytes()

    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="sthamly_engine_report.pdf"'},
    )
import sys
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from samples.sample_model import MLP, CKPT, train  # noqa: E402
from engine.run_all import measure_accuracy  # noqa: E402
from engine.ir.ir_builder import build_ir  # noqa: E402
from engine.mapper.grid_model import GridModel  # noqa: E402
from engine.mapper.placement import place  # noqa: E402
from engine.verilog_gen.config_bits import build_config  # noqa: E402
from engine.verilog_gen.verilog_writer import generate_verilog  # noqa: E402
from engine.report.metrics import build_report  # noqa: E402

app = FastAPI(title="Sthamly Engine API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_cache = {}


def _load():
    """Load the model and measure accuracy once; reuse on later calls."""
    if "model" not in _cache:
        if not CKPT.exists():
            train()
        model = MLP()
        model.load_state_dict(torch.load(CKPT))
        _cache["model"] = model
        _cache["acc"] = measure_accuracy(model)
    return _cache["model"], _cache["acc"]


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/run")
def run(rows: int = 8, cols: int = 8, bits: int = 8):
    if not (1 <= rows <= 32 and 1 <= cols <= 32):
        raise HTTPException(400, "rows and cols must be between 1 and 32")
    if bits not in (4, 8):
        raise HTTPException(400, "bits must be 4 or 8")

    model, acc_rows = _load()
    ir = build_ir(model, (1, 1, 28, 28))
    grid = GridModel(rows, cols)
    placement = place(ir, grid)
    configs = build_config(placement)

    return {
        "report": build_report("mnist_mlp", acc_rows, ir, units=grid.size),
        "placement": placement.to_dict(),
        "verilog": generate_verilog(configs, rows, cols, bits, "mnist_mlp"),
    }
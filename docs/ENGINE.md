# Sthamly Engine

**Polymorphic AI Accelerator Explorer, graphene-ready.**

Takes a small neural network, quantizes it to integers, maps it onto a 2D grid
of processing elements, generates Verilog for an inference core, and checks that
the Verilog gives the same answers as the Python integer model, bit for bit.

This is a working prototype for exploring accelerator designs. It is not a
graphene chip and it does not claim to be one.

## What is measured and what is estimated

| Item | Status |
|---|---|
| Accuracy of FP32 / W8A8 / W4A8 on the MNIST test set | Measured (software) |
| Verilog output equals the Python integer model | Checked in simulation, bit for bit |
| Cycles per image of the generated core | Simulation result |
| Energy, time, area per inference | Simulated estimate (assumptions listed in the report) |
| Silicon numbers | Assumptions scaled from published ASIC figures, not measured on a board |
| Graphene numbers | Hypothetical what-if with placeholder factors, not a real device |

W8A8 = 8-bit weights, 8-bit activations. W4A8 = 4-bit weights, 8-bit activations.

## Quick start

```
pip install -r requirements.txt fastapi uvicorn
python samples/sample_model.py            # trains the sample MNIST MLP (about 1-2 min)
python engine/run_infer.py                # integer model, hex files, Verilog, testbench
sudo apt-get install -y iverilog          # or Verilator 5+
bash hardware/testbench/run_infer.sh      # must end with PASS
```

Web app (two terminals, from the repo root):

```
uvicorn engine.api:app --port 8000        # wait for "Ready."
npm run dev                               # then open http://localhost:3000/engine
```

Set `NEXT_PUBLIC_ENGINE_API_URL=http://localhost:8000` in `.env.local`.

## How it works

```
sample MLP (PyTorch)
   -> integer model (weights 4/8-bit, activations 8-bit)      engine/quantizer/
   -> IR: layers, MACs, parameters                            engine/ir/
   -> placement on a 2D PE grid, one frame per layer          engine/mapper/
   -> cost estimate from a swappable technology model         engine/tech/
   -> hex files + Verilog core + self-checking testbench      engine/verilog_gen/
   -> report (JSON, PDF) and web page                         engine/report/, app/engine/
```

The technology model is a separate class (`engine/tech/`). The silicon and the
graphene what-if are two implementations of the same interface, so replacing
the graphene placeholders with published device data later touches one file.

## Folder map

```
engine/
  api.py                    FastAPI: /run, /report.pdf, /health
  run_all.py                masked MAC array + cost report in one command
  run_infer.py              integer model -> hex files -> Verilog + testbench
  quantizer/                quantize.py, accuracy_check.py, int_infer.py
  ir/                       ir_builder.py, resource_estimator.py
  mapper/                   grid_model.py, placement.py
  tech/                     base.py, silicon_fpga.py, graphene_model.py
  verilog_gen/              config_bits.py, verilog_writer.py, weights_export.py,
                            infer_writer.py, tb_writer.py
  report/                   metrics.py, pdf_report.py
hardware/
  verilog/                  generated: sthamly_top.v, sthamly_infer.v, data/*.hex
  testbench/                tb_top.v, run_verilator.sh, tb_infer.v, run_infer.sh
samples/                    sample_model.py
app/engine/page.tsx         web page
components/EngineRunner.tsx web UI
```

## Three-minute demo

1. Before the meeting: run the four commands in Quick start so the simulation result exists.
2. Open `/engine`, press **Compile & Simulate**.
3. Show the results table: accuracy is measured; read the precision key under it.
4. Show **Hardware simulation**: bit-exact YES, cycles per image.
5. Show the grid mapping: pick a layer, see which PEs are active.
6. Download the Verilog and the PDF. Point at the "What this report does NOT claim" list.

Say: "This explores accelerator designs for a small model and verifies the
generated hardware in simulation. Next step is measuring on an FPGA board."

Do not say: that this is a graphene chip, that numbers are measured on hardware,
or any fixed speed-up or power-saving figure.

## Known limits

- One model only (a small MNIST MLP). Model upload is not built yet.
- Energy counts only MAC operations; memory and data movement are not included.
- The FP32 baseline cost is extrapolated with a simple formula, so the "energy
  save %" against FP32 is not a result to quote.
- The generated core stores each weight in one byte; 4-bit weights are not
  packed in hardware yet, so the W4A8 size reduction is theoretical.
- Memories are plain arrays for simulation, not FPGA block RAM.
- The clock frequency (200 MHz) is an assumption, not measured.
- No FPGA run has been done yet.

## Roadmap

1. Run on an FPGA board, replace estimates with measured power, speed and area.
2. Pack 4-bit weights; map memories to block RAM.
3. Upload your own small model (ONNX / PyTorch).
4. Replace graphene placeholder factors with published device data, with sources.
5. Larger models, then device-level simulation with a research partner.
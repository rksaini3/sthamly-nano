from pathlib import Path
from typing import List

from engine.verilog_gen.config_bits import FrameConfig


def generate_verilog(configs: List[FrameConfig], rows: int, cols: int,
                     bits: int, model_name: str = "model",
                     acc_bits: int = 32) -> str:
    n = rows * cols
    frames = len(configs)
    fs = max(1, (frames - 1).bit_length())

    cases = []
    for idx, c in enumerate(configs):
        cases.append(
            f"      {fs}'d{idx}: begin mask = {c.verilog_literal()}; "
            f"passes = 32'd{c.passes}; end  // {c.layer_name} ({c.kind}), "
            f"{c.active_pes} PEs"
        )
    case_text = "\n".join(cases)

    return f"""// ============================================================
// Sthamly Engine - generated Verilog (do not edit by hand)
// Model : {model_name}
// Grid  : {rows} x {cols} = {n} PEs, {bits}-bit weights and activations
// Frames: {frames} (one per compute layer)
//
// What this file IS: a masked MAC array. Each frame turns on a subset of
// PEs (the mask). Active PEs multiply weight*activation, the products are
// summed, and the sum is added to an accumulator every enabled cycle.
//
// What this file is NOT (not implemented yet): weight/activation memory,
// the pass-sequencing controller (the 'passes' output tells how many times
// a frame must be repeated), and activation functions such as ReLU.
// No power or speed numbers live here; those come from the report.
// ============================================================

module sthamly_config (
    input  wire [{fs - 1}:0] frame_sel,
    output reg  [{n - 1}:0] mask,
    output reg  [31:0] passes
);
    always @* begin
        mask   = {n}'d0;
        passes = 32'd0;
        case (frame_sel)
{case_text}
            default: begin mask = {n}'d0; passes = 32'd0; end
        endcase
    end
endmodule

module sthamly_pe_array #(
    parameter N    = {n},
    parameter BITS = {bits},
    parameter ACC  = {acc_bits}
) (
    input  wire                     clk,
    input  wire                     rst,
    input  wire                     clear,
    input  wire                     en,
    input  wire [N-1:0]             mask,
    input  wire [N*BITS-1:0]        w_flat,
    input  wire [N*BITS-1:0]        a_flat,
    output reg  signed [ACC-1:0]    acc
);
    integer i;
    reg signed [ACC-1:0] sum;

    always @* begin
        sum = 0;
        for (i = 0; i < N; i = i + 1) begin
            if (mask[i])
                sum = sum + $signed(w_flat[i*BITS +: BITS]) * $signed(a_flat[i*BITS +: BITS]);
        end
    end

    always @(posedge clk) begin
        if (rst || clear)
            acc <= 0;
        else if (en)
            acc <= acc + sum;
    end
endmodule

module sthamly_top (
    input  wire                     clk,
    input  wire                     rst,
    input  wire                     clear,
    input  wire                     en,
    input  wire [{fs - 1}:0]        frame_sel,
    input  wire [{n * bits - 1}:0]  w_flat,
    input  wire [{n * bits - 1}:0]  a_flat,
    output wire signed [{acc_bits - 1}:0] acc,
    output wire [31:0]              passes
);
    wire [{n - 1}:0] mask;

    sthamly_config u_cfg (
        .frame_sel(frame_sel),
        .mask(mask),
        .passes(passes)
    );

    sthamly_pe_array #(.N({n}), .BITS({bits}), .ACC({acc_bits})) u_array (
        .clk(clk), .rst(rst), .clear(clear), .en(en),
        .mask(mask), .w_flat(w_flat), .a_flat(a_flat),
        .acc(acc)
    );
endmodule
"""


def write_verilog(configs: List[FrameConfig], rows: int, cols: int, bits: int,
                  out_path: str, model_name: str = "model") -> str:
    text = generate_verilog(configs, rows, cols, bits, model_name)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return str(p)
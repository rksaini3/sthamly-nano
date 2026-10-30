from pathlib import Path
from typing import Dict

TEMPLATE = r"""// ============================================================
// Sthamly Engine - generated inference core (do not edit by hand)
// W%%WBITS%%A8 integer MLP, %%NLAY%% layers, %%P%% MAC lanes per cycle.
//
// How it works: for every output neuron the core loads the bias, then
// walks the input in chunks of %%P%% values (FETCH, then MAC), then
// requantizes (FIN) and stores the result. One neuron at a time.
// Weights and biases are read from hex files with $readmemh, so the
// simulator must run from the repo root (paths are relative to it).
//
// Meant for simulation (Verilator / Icarus). Memories are plain arrays;
// mapping them to real FPGA block RAM is not done yet.
// Cycle count comes from simulation. Power numbers are NOT produced here.
// ============================================================

module sthamly_infer (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 x_we,
    input  wire [15:0]          x_addr,
    input  wire [%%WBM1%%:0]    x_data,
    input  wire                 start,
    output reg                  done,
    output reg  [31:0]          cycles,
    output wire [%%LOGBM1%%:0]  logits_flat,
    output wire [7:0]           pred
);
    localparam P    = %%P%%;
    localparam WB   = 8 * P;
    localparam NCLS = %%NCLS%%;
    localparam BUFB = %%BUFBYTES%% * 8;

    localparam S_IDLE  = 3'd0;
    localparam S_ZERO  = 3'd1;
    localparam S_INIT  = 3'd2;
    localparam S_FETCH = 3'd3;
    localparam S_MAC   = 3'd4;
    localparam S_FIN   = 3'd5;
    localparam S_DONE  = 3'd6;

%%ROMS%%
    reg [BUFB-1:0]      actA;
    reg [BUFB-1:0]      actB;
    reg [2:0]           state;
    reg [7:0]           layer;
    reg [15:0]          o;
    reg [15:0]          c;
    reg signed [31:0]   acc;
    reg [WB-1:0]        w_q;
    reg [WB-1:0]        a_q;
    reg [32*NCLS-1:0]   logits_r;

    assign logits_flat = logits_r;

    // per-layer constants
    reg [15:0]          cur_ch;
    reg [15:0]          cur_out;
    reg                 cur_last;
    reg signed [63:0]   cur_m;
    reg signed [63:0]   cur_rnd;
    reg [5:0]           cur_sh;

    always @* begin
        cur_ch   = 16'd1;
        cur_out  = 16'd1;
        cur_last = 1'b1;
        cur_m    = 64'sd0;
        cur_rnd  = 64'sd0;
        cur_sh   = 6'd0;
        case (layer)
%%CUR_CASES%%
            default: begin
                cur_ch   = 16'd1;
                cur_out  = 16'd1;
                cur_last = 1'b1;
            end
        endcase
    end

    wire [31:0] waddr = o * cur_ch + c;

    // dot product of the P lanes: signed weight x unsigned (0..127) activation
    integer k;
    reg signed [31:0] dot;
    always @* begin
        dot = 32'sd0;
        for (k = 0; k < P; k = k + 1)
            dot = dot + $signed(w_q[8*k +: 8]) * $signed({1'b0, a_q[8*k +: 8]});
    end

    // argmax over logits, lowest index wins ties (same as numpy argmax)
    integer j;
    reg signed [31:0] best;
    reg [7:0]         best_i;
    always @* begin
        best   = $signed(logits_r[31:0]);
        best_i = 8'd0;
        for (j = 1; j < NCLS; j = j + 1) begin
            if ($signed(logits_r[32*j +: 32]) > best) begin
                best   = $signed(logits_r[32*j +: 32]);
                best_i = j;
            end
        end
    end
    assign pred = best_i;

    // requantization temporaries (blocking, used only inside S_FIN)
    reg signed [63:0] prod;
    reg signed [63:0] rnd;
    reg signed [63:0] v;
    reg [7:0]         val;

    always @(posedge clk) begin
        if (rst) begin
            state    <= S_IDLE;
            done     <= 1'b0;
            layer    <= 8'd0;
            o        <= 16'd0;
            c        <= 16'd0;
            acc      <= 32'sd0;
            cycles   <= 32'd0;
            logits_r <= {(32*NCLS){1'b0}};
        end else begin
            if (x_we)
                actA[x_addr * WB +: WB] <= x_data;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        done   <= 1'b0;
                        cycles <= 32'd0;
                        layer  <= 8'd0;
                        o      <= 16'd0;
                        c      <= 16'd0;
                        state  <= S_ZERO;
                    end
                end

                S_ZERO: begin
                    cycles <= cycles + 32'd1;
                    if (!cur_last) begin
                        if (layer[0] == 1'b0) actB <= {BUFB{1'b0}};
                        else                  actA <= {BUFB{1'b0}};
                    end
                    o     <= 16'd0;
                    c     <= 16'd0;
                    state <= S_INIT;
                end

                S_INIT: begin
                    cycles <= cycles + 32'd1;
                    case (layer)
%%BIAS_CASES%%
                        default: acc <= 32'sd0;
                    endcase
                    c     <= 16'd0;
                    state <= S_FETCH;
                end

                S_FETCH: begin
                    cycles <= cycles + 32'd1;
                    case (layer)
%%FETCH_CASES%%
                        default: w_q <= {WB{1'b0}};
                    endcase
                    if (layer[0] == 1'b0) a_q <= actA[c * WB +: WB];
                    else                  a_q <= actB[c * WB +: WB];
                    state <= S_MAC;
                end

                S_MAC: begin
                    cycles <= cycles + 32'd1;
                    acc    <= acc + dot;
                    if (c == cur_ch - 16'd1) begin
                        state <= S_FIN;
                    end else begin
                        c     <= c + 16'd1;
                        state <= S_FETCH;
                    end
                end

                S_FIN: begin
                    cycles <= cycles + 32'd1;
                    if (cur_last) begin
                        logits_r[o * 32 +: 32] <= acc;
                    end else begin
                        prod = acc * cur_m;
                        rnd  = prod + cur_rnd;
                        v    = rnd >>> cur_sh;
                        if (v < 0)        val = 8'd0;
                        else if (v > 127) val = 8'd127;
                        else              val = v[7:0];
                        if (layer[0] == 1'b0) actB[o * 8 +: 8] <= val;
                        else                  actA[o * 8 +: 8] <= val;
                    end

                    if (o == cur_out - 16'd1) begin
                        if (cur_last) begin
                            state <= S_DONE;
                        end else begin
                            layer <= layer + 8'd1;
                            state <= S_ZERO;
                        end
                    end else begin
                        o     <= o + 16'd1;
                        state <= S_INIT;
                    end
                end

                S_DONE: begin
                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
"""


def _validate(manifest: Dict) -> None:
    layers = manifest["layers"]
    if not layers:
        raise ValueError("manifest has no layers")
    for i, L in enumerate(layers):
        last = i == len(layers) - 1
        if last and L["relu"]:
            raise ValueError("last layer must not have ReLU")
        if not last and not L["relu"]:
            raise ValueError("hidden layers must have ReLU")
        if L["relu"]:
            if not (1 <= L["shift"] <= 62):
                raise ValueError("bad shift in layer %d" % i)
            if not (0 < L["m"] < 2 ** 31):
                raise ValueError("bad requant multiplier in layer %d" % i)
        if i > 0 and L["in_features"] != layers[i - 1]["out_features"]:
            raise ValueError("layer %d input does not match previous output" % i)
    if manifest["lanes"] < 1:
        raise ValueError("lanes must be >= 1")


def generate_infer_verilog(manifest: Dict, data_dir: str) -> str:
    """
    manifest : dict returned by weights_export.export_weights
    data_dir : folder holding the hex files, as the simulator will see it
               (relative to where the simulator runs, normally the repo root)
    """
    _validate(manifest)
    layers = manifest["layers"]
    P = manifest["lanes"]
    d = data_dir.rstrip("/")

    roms = []
    cur_cases = []
    bias_cases = []
    fetch_cases = []
    for L in layers:
        i = L["index"]
        w_depth = L["out_features"] * L["chunks"]
        b_depth = L["out_features"]
        roms.append(
            f"    reg [WB-1:0] wrom{i} [0:{w_depth - 1}];\n"
            f"    reg [31:0]   brom{i} [0:{b_depth - 1}];\n"
            f"    initial begin\n"
            f'        $readmemh("{d}/{L["weights_file"]}", wrom{i});\n'
            f'        $readmemh("{d}/{L["bias_file"]}", brom{i});\n'
            f"    end\n"
        )
        last = not L["relu"]
        m = 0 if last else L["m"]
        sh = 0 if last else L["shift"]
        rnd = 0 if last else (1 << (sh - 1))
        cur_cases.append(
            f"            {i}: begin\n"
            f"                cur_ch   = 16'd{L['chunks']};\n"
            f"                cur_out  = 16'd{L['out_features']};\n"
            f"                cur_last = 1'b{1 if last else 0};\n"
            f"                cur_m    = 64'sd{m};\n"
            f"                cur_rnd  = 64'sd{rnd};\n"
            f"                cur_sh   = 6'd{sh};\n"
            f"            end"
        )
        bias_cases.append(f"                        {i}: acc <= $signed(brom{i}[o]);")
        fetch_cases.append(f"                        {i}: w_q <= wrom{i}[waddr];")

    buf_bytes = max(L["in_pad"] for L in layers)
    text = TEMPLATE
    repl = {
        "%%WBITS%%": str(manifest["w_bits"]),
        "%%NLAY%%": str(len(layers)),
        "%%P%%": str(P),
        "%%WBM1%%": str(8 * P - 1),
        "%%LOGBM1%%": str(32 * manifest["num_classes"] - 1),
        "%%NCLS%%": str(manifest["num_classes"]),
        "%%BUFBYTES%%": str(buf_bytes),
        "%%ROMS%%": "\n".join(roms),
        "%%CUR_CASES%%": "\n".join(cur_cases),
        "%%BIAS_CASES%%": "\n".join(bias_cases),
        "%%FETCH_CASES%%": "\n".join(fetch_cases),
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    if "%%" in text:
        raise RuntimeError("template token left unreplaced")
    return text


def write_infer_verilog(manifest: Dict, data_dir: str, out_path: str) -> str:
    text = generate_infer_verilog(manifest, data_dir)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return str(p)
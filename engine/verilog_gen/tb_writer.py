from pathlib import Path
from typing import Dict

TEMPLATE = r"""`timescale 1ns/1ps

// ============================================================
// Sthamly Engine - generated self-checking testbench
// Runs %%N%% real test images through sthamly_infer and compares
// every logit with the Python integer model (bit for bit).
// Run from the repo root: hex paths are relative to it.
// ============================================================

module tb_infer;
    localparam P       = %%P%%;
    localparam WB      = 8 * P;
    localparam N       = %%N%%;
    localparam CH0     = %%CH0%%;
    localparam NCLS    = %%NCLS%%;
    localparam TIMEOUT = 5000000;

    reg                  clk   = 1'b0;
    reg                  rst   = 1'b1;
    reg                  x_we  = 1'b0;
    reg  [15:0]          x_addr = 16'd0;
    reg  [WB-1:0]        x_data = {WB{1'b0}};
    reg                  start = 1'b0;
    wire                 done;
    wire [31:0]          cycles;
    wire [32*NCLS-1:0]   logits_flat;
    wire [7:0]           pred;

    sthamly_infer dut (
        .clk(clk), .rst(rst),
        .x_we(x_we), .x_addr(x_addr), .x_data(x_data),
        .start(start), .done(done), .cycles(cycles),
        .logits_flat(logits_flat), .pred(pred)
    );

    always #5 clk = ~clk;

    reg [WB-1:0]  tx [0:N*CH0-1];
    reg [7:0]     ty [0:N-1];
    reg [31:0]    tl [0:N*NCLS-1];

    initial begin
        $readmemh("%%DIR%%/%%XFILE%%", tx);
        $readmemh("%%DIR%%/%%YFILE%%", ty);
        $readmemh("%%DIR%%/%%LFILE%%", tl);
    end

    integer n, c, k, wait_cycles;
    integer mismatches, pred_mismatches, hw_correct, first_cycles, exp_pred;
    reg signed [31:0] got, exp_v, best;

    initial begin
        mismatches      = 0;
        pred_mismatches = 0;
        hw_correct      = 0;
        first_cycles    = 0;

        repeat (4) @(posedge clk);
        #1 rst = 1'b0;
        @(posedge clk);
        #1;

        for (n = 0; n < N; n = n + 1) begin
            // load one image, one chunk per clock
            for (c = 0; c < CH0; c = c + 1) begin
                x_we   = 1'b1;
                x_addr = c;
                x_data = tx[n*CH0 + c];
                @(posedge clk);
                #1;
            end
            x_we = 1'b0;

            // start and wait for done
            start = 1'b1;
            @(posedge clk);
            #1;
            start = 1'b0;

            wait_cycles = 0;
            while (!done && wait_cycles < TIMEOUT) begin
                @(posedge clk);
                #1;
                wait_cycles = wait_cycles + 1;
            end
            if (!done) begin
                $display("TIMEOUT on image %0d", n);
                $display("FAIL");
                $finish;
            end

            // compare logits bit for bit
            for (k = 0; k < NCLS; k = k + 1) begin
                got   = logits_flat[32*k +: 32];
                exp_v = tl[n*NCLS + k];
                if (got !== exp_v) begin
                    mismatches = mismatches + 1;
                    if (mismatches <= 10)
                        $display("MISMATCH image %0d class %0d: got %0d expected %0d",
                                 n, k, got, exp_v);
                end
            end

            // expected class from expected logits (lowest index wins ties)
            best     = tl[n*NCLS];
            exp_pred = 0;
            for (k = 1; k < NCLS; k = k + 1) begin
                exp_v = tl[n*NCLS + k];
                if (exp_v > best) begin
                    best     = exp_v;
                    exp_pred = k;
                end
            end
            if (pred !== exp_pred[7:0]) begin
                pred_mismatches = pred_mismatches + 1;
                if (pred_mismatches <= 10)
                    $display("PRED MISMATCH image %0d: got %0d expected %0d", n, pred, exp_pred);
            end
            if (pred == ty[n]) hw_correct = hw_correct + 1;

            if (n == 0) first_cycles = cycles;
        end

        $display("images=%0d logit_mismatches=%0d pred_mismatches=%0d",
                 N, mismatches, pred_mismatches);
        $display("HW_CORRECT=%0d/%0d", hw_correct, N);
        $display("CYCLES_PER_IMAGE=%0d", first_cycles);
        if (mismatches == 0 && pred_mismatches == 0) $display("PASS");
        else                                         $display("FAIL");
        $finish;
    end
endmodule
"""


def generate_tb_verilog(manifest: Dict, data_dir: str) -> str:
    """
    manifest : dict returned by weights_export.export_weights
    data_dir : folder of the hex files as the simulator sees it (from repo root)
    """
    layers = manifest["layers"]
    if not layers:
        raise ValueError("manifest has no layers")
    repl = {
        "%%P%%": str(manifest["lanes"]),
        "%%N%%": str(manifest["n_images"]),
        "%%CH0%%": str(layers[0]["chunks"]),
        "%%NCLS%%": str(manifest["num_classes"]),
        "%%DIR%%": data_dir.rstrip("/"),
        "%%XFILE%%": manifest["test_x_file"],
        "%%YFILE%%": manifest["test_y_file"],
        "%%LFILE%%": manifest["test_logits_file"],
    }
    text = TEMPLATE
    for k, v in repl.items():
        text = text.replace(k, v)
    if "%%" in text:
        raise RuntimeError("template token left unreplaced")
    return text


def write_tb_verilog(manifest: Dict, data_dir: str, out_path: str) -> str:
    text = generate_tb_verilog(manifest, data_dir)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return str(p)
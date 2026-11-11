# First real run on Raspberry Pi 5 (8 GB)

Goal: one real `llama-bench` record in `results/bench/`, then one HTML report.
Nothing here is a claim yet. The point is to check the pipeline on real output.

Run everything from the `hindicc/` root unless a step says otherwise.

## 0. Before you start

- Use the official 27W USB-C power supply and active cooling (fan or heatsink).
  Under-powered or hot runs throttle, and the numbers will reflect that.
- Close other heavy programs. Note what is still running.

Check the throttling state now (Raspberry Pi OS):

    vcgencmd get_throttled

`0x0` means no throttling has happened. Save the output with the run notes.

## 1. Build llama.cpp (llama-bench only)

    sudo apt update && sudo apt install -y git cmake build-essential
    git clone https://github.com/ggml-org/llama.cpp
    cd llama.cpp
    git rev-parse HEAD            # save this commit hash in your run notes
    cmake -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build --config Release -j4 --target llama-bench

The binary is at `llama.cpp/build/bin/llama-bench`.

## 2. Get one quantized model

Use Qwen2.5-1.5B-Instruct, Q4_K_M GGUF. Download it from the Hugging Face
GGUF repository for that model, and check the exact file name on the page.
Save the file path. The runner hashes it automatically.

## 3. Check llama-bench's JSON field names (important)

The runner reads these keys from llama-bench's JSON output: `n_prompt`,
`n_gen`, `avg_ts`, `stddev_ts`. These names were written from memory, not
checked against a real build. Check them once:

    ./llama.cpp/build/bin/llama-bench -m /path/to/model.gguf -t 4 -p 512 -n 128 -r 1 -o json

Look at the keys in the output. If they differ from the four names above,
stop and send me the output. Do not run the runner until the names match.

## 4. Run the runner

From `hindicc/`:

    PYTHONPATH=. python3 bench/runner.py \
        --binary /home/pi/llama.cpp/build/bin/llama-bench \
        --model /path/to/model.gguf \
        --threads 4 --prompt 512 --gen 128 --reps 5

Expected: one JSON file under `results/bench/`, and three printed lines:
`returncode=0`, a prefill tok/s line, and a decode tok/s line.

If `returncode` is not 0, or `parse_error` is set, check the `stderr_tail`
field in the JSON file, and send me that section.

## 5. Build the report

    PYTHONPATH=. python3 report/build.py

Open `results/report.html` in a browser. Check that the device, the model
hash and the numbers match what you saw in step 4.

## 6. Send back

- Output of `vcgencmd get_throttled` (step 0).
- The llama.cpp commit hash (step 1).
- The four-line output from step 4.
- Whether the key names in step 3 matched.

With that I can check the record, and then decide the next step with real data.

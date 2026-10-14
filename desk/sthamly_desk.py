#!/usr/bin/env python3
"""Sthamly Desk: user ka software. Model aur PDF dono sirf user ke computer par rehte hain.

    python desk/sthamly_desk.py --check                            # kya taiyaar hai, kya kam hai
    python desk/sthamly_desk.py --model dist/X/X-Q4_K_M-hindi60.gguf --no-license    # apne test ke liye
    python desk/sthamly_desk.py --model path/to/model.gguf         # asli: activation key maangega
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from desk_app import Desk, make_server  # noqa: E402
from desk_errors import DeskError  # noqa: E402
from desk_llama import LlamaServer, find_llama_server  # noqa: E402

HOME = Path(os.environ.get("STHAMLY_HOME") or Path.home() / ".sthamly")


def load_config() -> dict:
    """desk_config.json (is file ke saath) < ~/.sthamly/config.json < environment."""
    cfg: dict = {}
    for p in (HERE / "desk_config.json", HOME / "config.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if v})
        except (OSError, ValueError):
            pass
    env_map = {"supabase_url": ("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"),
               "supabase_anon_key": ("SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY")}
    for key, names in env_map.items():
        for n in names:
            if os.environ.get(n):
                cfg[key] = os.environ[n]
                break
    return cfg


def find_model(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg)
        return p if p.is_file() else None
    for d in (HERE / "models", HOME / "models", HERE, HERE.parent / "dist"):
        found = sorted(d.rglob("*.gguf")) if d.is_dir() else []
        if found:
            return found[0]
    return None


def check(args: argparse.Namespace, cfg: dict) -> int:
    bad = 0

    def line(ok: bool, what: str, fix: str = "") -> None:
        nonlocal bad
        print(f"[{'OK  ' if ok else 'KAM '}] {what}" + ("" if ok else f"  ->  {fix}"))
        if not ok:
            bad += 1

    try:
        import pypdf  # noqa: F401
        line(True, "pypdf (PDF padhne ke liye)")
    except ImportError:
        line(False, "pypdf", "pip install pypdf")
        
    server = args.llama_server or find_llama_server(HERE)
    line(bool(server), f"llama-server{': ' + str(server) if server else ''}",
         "llama.cpp build karo (bash scripts/setup.sh) ya release se llama-server download karke is folder me rakho")
         
    model = find_model(args.model)
    line(bool(model), f"model (.gguf){': ' + str(model) if model else ''}",
         "python scripts/engine.py build --model ... chalao, ya --model path/to/file.gguf do")
         
    if args.no_license:
        print("[note] --no-license: testing mode, activation key nahi maangi jaayegi")
    else:
        line(bool(cfg.get("supabase_url") and cfg.get("supabase_anon_key")), "server ka pata (Supabase URL + anon key)",
             "desk/desk_config.example.json ko desk_config.json naam se copy karke bharo")
             
    print("\nSab taiyaar hai." if bad == 0 else f"\n{bad} cheez(en) kam hain.")
    return 0 if bad == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sthamly Desk", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="GGUF model file")
    ap.add_argument("--llama-server", type=Path, help="llama-server ka path")
    ap.add_argument("--llama-url", help="pehle se chal rahe llama-server ka URL (tab hum use start nahi karte)")
    ap.add_argument("--port", type=int, default=8765, help="software ka port (default 8765)")
    ap.add_argument("--llama-port", type=int, default=8081)
    ap.add_argument("--ctx", type=int, default=4096, help="model ki yaaddasht (tokens)")
    
    # --- HARDWARE COMPLIANCE MULTITHREADING OVERHAUL ---
    # Lock computational thread pools strictly to physical performance execution blocks 
    # to protect processing pipelines from CPU thrashing.
    system_cores = os.cpu_count() or 4
    optimal_threads = max(2, min(4, system_cores // 2))
    ap.add_argument("--threads", type=int, default=optimal_threads, help="model processing core units allocation")
    
    ap.add_argument("--no-license", action="store_true", help="sirf testing: activation key mat maango")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()
    
    if args.check:
        return check(args, cfg)

    runner: LlamaServer | None = None
    llama_url = args.llama_url
    if not llama_url:
        server, model = args.llama_server or find_llama_server(HERE), find_model(args.model)
        if not server or not model:
            print("llama-server ya model nahi mila. Pehle chalao:  python desk/sthamly_desk.py --check")
            return 1
        runner = LlamaServer(server, model, args.llama_port, args.ctx, args.threads, HOME / "llama.log")
        atexit.register(runner.stop)
        print(f"Model load ho raha hai: {model.name} (pehli baar 1-2 minute lag sakte hain)...", flush=True)
        try:
            runner.start()
        except DeskError as e:
            print(e.message)
            return 1
        llama_url = runner.url

    desk = Desk(cfg, llama_url, HOME, dev=args.no_license)
    httpd = None
    for port in range(args.port, args.port + 10):
        try:
            httpd = make_server(desk, port)
            break
        except OSError:
            continue
            
    if httpd is None:
        print("Koi port khaali nahi mila. --port se doosra number do.")
        return 1
        
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Sthamly Desk chalu: {url}   (band karne ke liye Ctrl+C)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBand ho raha hai...")
    finally:
        httpd.server_close()
        if runner:
            runner.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

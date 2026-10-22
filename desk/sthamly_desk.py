#!/usr/bin/env python3
"""Sthamly Desk: user ka software. Model aur PDF dono sirf user ke computer par rehte hain.

    python desk/sthamly_desk.py --check                                     # kya taiyaar hai, kya kam hai
    python desk/sthamly_desk.py --model path/to/model.gguf                  # asli: activation key maangega
    STHAMLY_DEV=1 python desk/sthamly_desk.py --model X.gguf --no-license   # sirf developer ke test ke liye
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
DEV_ENV = "STHAMLY_DEV"  # sirf developer ke computer par "1" hota hai. Shipped exe par nahi.


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


def dev_mode_allowed() -> bool:
    return os.environ.get(DEV_ENV) == "1"


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
         "--model path/to/file.gguf do, ya model ko desk/models folder me rakho")

    if args.no_license:
        print(f"[note] --no-license: testing mode (sirf {DEV_ENV}=1 ke saath chalta hai)")
    else:
        line(bool(cfg.get("supabase_url") and cfg.get("supabase_anon_key")),
             "server ka pata (Supabase URL + anon key)",
             "desk/desk_config.json mein supabase_url aur supabase_anon_key bharo")

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
    ap.add_argument("--threads", type=int, default=max(2, min(4, (os.cpu_count() or 4) // 2)),
                    help="model processing threads")
    ap.add_argument("--no-license", action="store_true", help=f"sirf developer test: {DEV_ENV}=1 chahiye")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()

    if args.check:
        return check(args, cfg)

    dev = False
    if args.no_license:
        if not dev_mode_allowed():
            print(f"--no-license sirf developer mode me chalta hai ({DEV_ENV}=1). Asli software me ye kaam nahi karta.")
            return 2
        dev = True
    elif not (cfg.get("supabase_url") and cfg.get("supabase_anon_key")):
        print("Server ka pata nahi mila (desk_config.json). Software shuru nahi ho sakta.")
        return 1

    model = find_model(args.model)
    if model is None:
        print("Model (.gguf) nahi mila. --model path/to/file.gguf do.")
        return 1

    llama: LlamaServer | None = None
    if args.llama_url:
        llama_url = args.llama_url.rstrip("/")
    else:
        binary = args.llama_server or find_llama_server(HERE)
        if binary is None or not Path(binary).is_file():
            print("llama-server nahi mila. --llama-server path do, ya llama-server ko desk folder me rakho.")
            return 1
        llama = LlamaServer(Path(binary), model, port=args.llama_port, ctx=args.ctx,
                            threads=args.threads, log=HOME / "llama.log")
        atexit.register(llama.stop)
        print("Model load ho raha hai (pehli baar me kuch minute lag sakte hain)...", flush=True)
        try:
            llama.start()
        except (DeskError, OSError) as exc:
            print(f"Model start nahi hua: {getattr(exc, 'message', exc)}")
            return 1
        llama_url = llama.url

    desk = Desk(cfg, llama_url, HOME, dev=dev)
    try:
        server = make_server(desk, args.port)
    except OSError:
        print(f"Port {args.port} pehle se use ho raha hai. --port se dusra port do.")
        return 1

    url = f"http://localhost:{args.port}"
    print(f"Sthamly Desk chal raha hai: {url}\nBand karne ke liye Ctrl+C dabao.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBand ho raha hai.")
    finally:
        server.server_close()
        if llama is not None:
            llama.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
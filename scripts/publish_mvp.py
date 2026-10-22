#!/usr/bin/env python3
"""
Sthamly NanoBrain: publish a verified quantized model to Hugging Face.

Refuses to publish anything that is not backed by a real, complete audit report.
Numbers come only from reports/<slug>/report.json. Nothing is invented.

Usage:
    python scripts/publish.py --model Qwen/Qwen2.5-1.5B-Instruct            # dry run (default)
    python scripts/publish.py --model Qwen/Qwen2.5-1.5B-Instruct --go       # real upload
    python scripts/publish.py --model Qwen/Qwen2.5-1.5B-Instruct --go --private

Environment:
    HF_WRITE_TOKEN                   Hugging Face token with write access (needed for --go)
    NEXT_PUBLIC_SUPABASE_URL         optional, for logging the publish
    SUPABASE_SERVICE_ROLE_KEY        optional, server-side only

Steps:
    1. Load and validate report.json (schema 2, status complete, real results)
    2. Run engine.decide() to pick the variant
    3. Verify the license from Hugging Face metadata (no default guess)
    4. Verify the GGUF file exists and its sha256 matches the manifest
    5. Build the model card from real numbers only
    6. Upload (only with --go). Dry run prints the plan and changes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sthamly_common import normalize_model_id, Supabase, SupabaseError, scrub, TABLE_JOBS  # noqa: E402
from engine import decide, load_report, report_path_for, gguf_for_variant, EngineError  # noqa: E402

DIST_DIR = ROOT / "dist"
README_NAME = "README.md"


class PublishError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ============================================================
# LICENSE: from Hugging Face metadata only, never guessed
# ============================================================

def fetch_license(model: str, token: str | None) -> str:
    from huggingface_hub import HfApi

    try:
        info = HfApi(token=token).model_info(model)
    except Exception as exc:
        raise PublishError(f"HF se model info nahi mili ({type(exc).__name__}).") from exc

    value = None
    card = getattr(info, "card_data", None)
    if card is not None:
        value = getattr(card, "license", None)
    if not value:
        for tag in info.tags or []:
            if tag.startswith("license:"):
                value = tag.split(":", 1)[1]
                break

    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if not value:
        raise PublishError("License HF metadata mein nahi mila. Publish band.")
    return str(value).strip().lower()


# ============================================================
# MODEL CARD: real numbers only
# ============================================================

def build_model_card(
    *,
    model: str,
    license_id: str,
    report: dict[str, Any],
    decision: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    repo_title = f"{model.split('/', 1)[1]}-hindi-imatrix-Q4_K_M-GGUF"
    rows: list[str] = []

    table: dict[str, dict[str, dict[str, Any]]] = {}
    for item in report["results"]:
        table.setdefault(item["domain"], {})[item["variant"]] = item

    for domain, by_variant in table.items():
        for variant, item in sorted(by_variant.items()):
            label = "Plain Q4_K_M" if variant.endswith("standard") else f"{variant} imatrix Q4_K_M"
            kld = f"{item['kld']:.4f} ± {item['kld_error']:.4f}"
            top = (f"{item['same_top_p']:.2f} ± {item['same_top_p_error']:.2f}"
                   if "same_top_p" in item and "same_top_p_error" in item else "-")
            rows.append(f"| {domain} | {label} | {kld} | {top} |")

    verdict_lines = []
    if decision["decision"] == "improved":
        verdict_lines.append(
            f"Is repo mein `{decision['best']}` imatrix variant hai. "
            "Ye plain Q4_K_M se har test set par KLD mein kam hai, aur gain error se saaf bada hai."
        )
    else:
        verdict_lines.append(
            "Is repo mein plain Q4_K_M hai. Imatrix variant ne plain Q4 se saaf behtar result nahi diya."
        )

    front = (
        "---\n"
        f"license: {license_id}\n"
        f"base_model: {model}\n"
        "tags: [gguf, imatrix, quantized]\n"
        "---\n"
    )
    body = (
        f"# {repo_title}\n\n"
        f"Ye {model} ka GGUF quantized version hai, FP16 reference se naapa gaya.\n\n"
        + " ".join(verdict_lines) + "\n\n"
        "## Measured results (KLD vs FP16, kam = behtar)\n\n"
        "| Test set | Variant | KLD | Same top token (%) |\n"
        "|---|---|---|---|\n"
        + "\n".join(rows) + "\n\n"
        "## Limits\n\n"
        "- KLD FP16 se doori ka proxy hai, task accuracy nahi.\n"
        "- Test sets chhote hain; ± ko dekhe bina chhote fark par bharosa mat karo.\n"
        "- Ye card koi compliance ya certification claim nahi karti.\n\n"
        f"Report generated: {report.get('timestamp', 'unknown')}  \n"
        f"File sha256: `{manifest['sha256']}`\n"
    )
    return front + body


# ============================================================
# VERIFICATION
# ============================================================

def verify_package(model: str, decision: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Make sure the file we will upload exists, is non-empty, and matches a fresh hash."""
    chosen = decision["best"] if decision["decision"] == "improved" else decision["baseline"]
    src = gguf_for_variant(model, chosen)
    if not src.is_file() or src.stat().st_size == 0:
        raise PublishError(f"GGUF file nahi mili: {src}")

    manifest_path = DIST_DIR / model.replace("/", "__") / "manifest.json"
    if not manifest_path.is_file():
        raise PublishError(f"Pehle engine.py build chalao: {manifest_path} nahi mila.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest.get("variant") != chosen:
        raise PublishError(f"Manifest variant {manifest.get('variant')!r} != decision {chosen!r}.")

    actual = sha256_file(src)
    if actual != manifest.get("sha256"):
        raise PublishError("File ka sha256 manifest se match nahi karta. Dobara build karo.")

    return src, manifest


# ============================================================
# UPLOAD (only with --go)
# ============================================================

def upload_to_hub(repo_id: str, gguf: Path, card: str, token: str, private: bool) -> str:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, private=private, exist_ok=True, repo_type="model")
    api.upload_file(path_or_fileobj=str(gguf), path_in_repo=gguf.name,
                    repo_id=repo_id, repo_type="model")
    api.upload_file(path_or_fileobj=card.encode("utf-8"), path_in_repo=README_NAME,
                    repo_id=repo_id, repo_type="model",
                    commit_message="Add model card from Sthamly report")
    return f"https://huggingface.co/{repo_id}"


def log_publish(model: str, url: str) -> None:
    """Optional. Logs a publish event; does NOT change any job status or metric."""
    try:
        db = Supabase.from_env()
    except SupabaseError:
        print("[info] Supabase config nahi mili; publish log skip.")
        return
    try:
        db.insert(TABLE_JOBS, {
            "hf_model_identifier": model,
            "requester_hash": "publish",
            "status": "published",
            "step": f"Published to {url}",
            "progress": 100,
        })
    except SupabaseError as exc:
        print(f"[warn] Publish log fail: {scrub(exc, limit=200)}")


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Publish a verified model. Dry run by default.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--go", action="store_true", help="real upload to Hugging Face")
    ap.add_argument("--private", action="store_true", help="create the repo as private")
    args = ap.parse_args()

    model = normalize_model_id(args.model)
    if model is None:
        print("Model ID sahi nahi hai.", file=sys.stderr)
        return 1

    try:
        report = load_report(report_path_for(model))
        decision = decide(report)
        license_id = fetch_license(model, os.getenv("HF_WRITE_TOKEN") or None)
        gguf, manifest = verify_package(model, decision)
        card = build_model_card(model=model, license_id=license_id, report=report,
                                decision=decision, manifest=manifest)

        repo_id = f"{os.getenv('HF_USERNAME', 'UNSET').strip()}/{model.split('/', 1)[1]}-hindi-imatrix-Q4_K_M-GGUF"
        card_path = DIST_DIR / model.replace("/", "__") / README_NAME
        card_path.write_text(card, encoding="utf-8")

        print("=" * 60)
        print("PUBLISH PLAN")
        print("=" * 60)
        print(f"Model:    {model}")
        print(f"License:  {license_id} (from HF metadata)")
        print(f"Variant:  {decision.get('best') or decision['baseline']} ({decision['decision']})")
        print(f"File:     {gguf.name}  sha256 {manifest['sha256'][:16]}...")
        print(f"Repo:     {repo_id}")
        print(f"Card:     {card_path}")
        print("=" * 60)

        if not args.go:
            print("DRY RUN: kuch upload nahi hua. Asli upload ke liye --go chalao.")
            return 0

        token = os.getenv("HF_WRITE_TOKEN", "").strip()
        if not token:
            raise PublishError("HF_WRITE_TOKEN set nahi hai.")
        if repo_id.startswith("UNSET/"):
            raise PublishError("HF_USERNAME set nahi hai.")

        url = upload_to_hub(repo_id, gguf, card, token, args.private)
        print(f"PUBLISHED: {url}")
        log_publish(model, url)
        return 0

    except (PublishError, EngineError) as exc:
        print(f"PUBLISH REFUSED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"PUBLISH FAILED: {type(exc).__name__}: {scrub(exc, limit=300)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
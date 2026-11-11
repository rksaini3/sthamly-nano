"""hindicc command line entry point.

Current commands (estimates only, nothing is measured here):

    python cli.py plan    --params 1543000000 --layers 28 --kv-heads 2 \
                          --head-dim 128 --hidden 1536 --quant q4_k_m \
                          --ctx 2048 --budget-mb 2048

    python cli.py max-ctx --params 1543000000 --layers 28 --kv-heads 2 \
                          --head-dim 128 --hidden 1536 --quant q4_k_m \
                          --budget-mb 2048

`plan` exits with status 1 if the configuration does not fit the budget.
"""

from __future__ import annotations

import argparse
import sys

from memory.budget import (
    BITS_PER_WEIGHT,
    DEFAULT_OVERHEAD_BYTES,
    MB,
    ModelShape,
    max_context_tokens,
    plan,
)

ESTIMATE_NOTE = "ESTIMATE ONLY: not measured. Compare with peak RSS before claiming."


def _mb(value: int) -> str:
    return f"{value / MB:,.1f} MB"


def _add_shape_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--params", type=int, required=True, help="total parameter count")
    parser.add_argument("--layers", type=int, required=True, help="number of transformer layers")
    parser.add_argument("--kv-heads", type=int, required=True, help="number of KV heads")
    parser.add_argument("--head-dim", type=int, required=True, help="dimension per head")
    parser.add_argument("--hidden", type=int, required=True, help="hidden size")
    parser.add_argument(
        "--quant",
        required=True,
        choices=sorted(BITS_PER_WEIGHT),
        help="weight quantization",
    )
    parser.add_argument("--budget-mb", type=float, required=True, help="memory budget in MB")
    parser.add_argument("--batch", type=int, default=1, help="batch size (default 1)")
    parser.add_argument(
        "--overhead-mb",
        type=float,
        default=DEFAULT_OVERHEAD_BYTES / MB,
        help="fixed runtime overhead in MB (ASSUMPTION, default 128)",
    )


def _shape_from_args(args: argparse.Namespace) -> ModelShape:
    return ModelShape(
        n_params=args.params,
        n_layers=args.layers,
        n_kv_heads=args.kv_heads,
        head_dim=args.head_dim,
        hidden_size=args.hidden,
    )


def _budget_bytes(args: argparse.Namespace) -> int:
    if args.budget_mb <= 0:
        raise SystemExit("error: --budget-mb must be positive")
    return int(args.budget_mb * MB)


def _cmd_plan(args: argparse.Namespace) -> int:
    shape = _shape_from_args(args)
    report = plan(
        shape,
        args.quant,
        args.ctx,
        _budget_bytes(args),
        batch=args.batch,
        overhead_bytes=int(args.overhead_mb * MB),
    )
    status = "FITS" if report.fits else "DOES NOT FIT"
    print(f"Configuration: quant={args.quant}, ctx={args.ctx}, batch={args.batch}")
    print(f"  weights      {_mb(report.weights)}")
    print(f"  kv cache     {_mb(report.kv_cache)}")
    print(f"  activations  {_mb(report.activations)}")
    print(f"  overhead     {_mb(report.overhead)}")
    print(f"  total        {_mb(report.total)}")
    print(f"  budget       {_mb(report.budget)}")
    print(f"  headroom     {_mb(report.headroom)}")
    print(f"Result: {status}")
    print(ESTIMATE_NOTE)
    return 0 if report.fits else 1


def _cmd_max_ctx(args: argparse.Namespace) -> int:
    shape = _shape_from_args(args)
    ctx = max_context_tokens(
        shape,
        args.quant,
        _budget_bytes(args),
        batch=args.batch,
        overhead_bytes=int(args.overhead_mb * MB),
    )
    if ctx == 0:
        print("Weights and fixed costs alone do not fit this budget. Max context: 0")
    else:
        print(f"Max context within budget (quant={args.quant}, batch={args.batch}): {ctx:,} tokens")
    print(ESTIMATE_NOTE)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hindicc", description="hindicc memory planning")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="check one configuration against a budget")
    _add_shape_args(p_plan)
    p_plan.add_argument("--ctx", type=int, required=True, help="context length in tokens")
    p_plan.set_defaults(func=_cmd_plan)

    p_ctx = sub.add_parser("max-ctx", help="largest context that fits the budget")
    _add_shape_args(p_ctx)
    p_ctx.set_defaults(func=_cmd_max_ctx)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
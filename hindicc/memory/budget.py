"""Memory budget estimator for hindicc.

All values are estimates in bytes. Each estimate must be checked against a
measured peak RSS (bench/) before any claim is made. Constants marked
ASSUMPTION are planning values, not measurements.
"""

from __future__ import annotations

from dataclasses import dataclass

MB = 1024 * 1024

# Effective bits per weight, including block scales and metadata.
# ASSUMPTION: planning values. Replace with (GGUF file size * 8 / param count)
# measured on the real artifact.
BITS_PER_WEIGHT = {
    "fp16": 16.0,
    "q8_0": 8.5,
    "q4_k_m": 4.85,
    "q2_k": 2.63,
    "ternary": 1.58,
}

# ASSUMPTION: bytes per KV cache element. 2 = fp16 KV cache.
DEFAULT_KV_BYTES = 2

# ASSUMPTION: bytes per activation element during compute (fp32 scratch).
DEFAULT_ACT_BYTES = 4

# ASSUMPTION: number of hidden-sized buffers alive at one time inside a layer
# (residual, norm output, attention scratch, FFN scratch). Rough.
LIVE_HIDDEN_BUFFERS = 8

# ASSUMPTION: fixed runtime overhead (code, allocator, tokenizer tables, stack).
DEFAULT_OVERHEAD_BYTES = 128 * MB


@dataclass(frozen=True)
class ModelShape:
    """Architecture numbers needed for memory planning."""

    n_params: int
    n_layers: int
    n_kv_heads: int
    head_dim: int
    hidden_size: int

    def __post_init__(self) -> None:
        for name in ("n_params", "n_layers", "n_kv_heads", "head_dim", "hidden_size"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive int, got {value!r}")


@dataclass(frozen=True)
class BudgetReport:
    """Result of planning one configuration against one budget."""

    weights: int
    kv_cache: int
    activations: int
    overhead: int
    budget: int

    @property
    def total(self) -> int:
        return self.weights + self.kv_cache + self.activations + self.overhead

    @property
    def fits(self) -> bool:
        return self.total <= self.budget

    @property
    def headroom(self) -> int:
        return self.budget - self.total


def _check_quant(quant: str) -> float:
    if quant not in BITS_PER_WEIGHT:
        known = ", ".join(sorted(BITS_PER_WEIGHT))
        raise ValueError(f"unknown quant {quant!r}; known: {known}")
    return BITS_PER_WEIGHT[quant]


def _check_positive(name: str, value: int) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int, got {value!r}")


def weight_bytes(n_params: int, quant: str) -> int:
    """Bytes needed to store all weights at the given quantization."""
    _check_positive("n_params", n_params)
    bits = _check_quant(quant)
    return -(-int(n_params * bits) // 8)  # ceiling division on bits, then bytes


def kv_cache_bytes(shape: ModelShape, ctx_len: int, kv_bytes: int = DEFAULT_KV_BYTES) -> int:
    """Bytes for keys and values across all layers for ctx_len tokens."""
    _check_positive("ctx_len", ctx_len)
    _check_positive("kv_bytes", kv_bytes)
    per_token = 2 * shape.n_layers * shape.n_kv_heads * shape.head_dim * kv_bytes
    return per_token * ctx_len


def activation_bytes(shape: ModelShape, batch: int = 1, act_bytes: int = DEFAULT_ACT_BYTES) -> int:
    """Rough peak scratch memory for activations during one forward pass."""
    _check_positive("batch", batch)
    _check_positive("act_bytes", act_bytes)
    return LIVE_HIDDEN_BUFFERS * batch * shape.hidden_size * act_bytes


def plan(
    shape: ModelShape,
    quant: str,
    ctx_len: int,
    budget_bytes: int,
    *,
    batch: int = 1,
    kv_bytes: int = DEFAULT_KV_BYTES,
    act_bytes: int = DEFAULT_ACT_BYTES,
    overhead_bytes: int = DEFAULT_OVERHEAD_BYTES,
) -> BudgetReport:
    """Estimate total memory for one configuration and compare to the budget."""
    _check_positive("budget_bytes", budget_bytes)
    return BudgetReport(
        weights=weight_bytes(shape.n_params, quant),
        kv_cache=kv_cache_bytes(shape, ctx_len, kv_bytes),
        activations=activation_bytes(shape, batch, act_bytes),
        overhead=overhead_bytes,
        budget=budget_bytes,
    )


def max_context_tokens(
    shape: ModelShape,
    quant: str,
    budget_bytes: int,
    *,
    batch: int = 1,
    kv_bytes: int = DEFAULT_KV_BYTES,
    act_bytes: int = DEFAULT_ACT_BYTES,
    overhead_bytes: int = DEFAULT_OVERHEAD_BYTES,
) -> int:
    """Largest context length whose estimated total memory fits the budget.

    Returns 0 if weights and fixed costs alone do not fit.
    """
    _check_positive("budget_bytes", budget_bytes)
    fixed = (
        weight_bytes(shape.n_params, quant)
        + activation_bytes(shape, batch, act_bytes)
        + overhead_bytes
    )
    remaining = budget_bytes - fixed
    if remaining <= 0:
        return 0
    per_token = kv_cache_bytes(shape, 1, kv_bytes)
    return remaining // per_token
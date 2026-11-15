"""Deterministic pseudo-randomness derived from stable hashes (no global RNG state)."""

from __future__ import annotations

import hashlib
import random


def stable_unit(*parts: object) -> float:
    """Map ``parts`` to a float in [0, 1) deterministically across processes and platforms."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def stable_int(*parts: object, modulo: int) -> int:
    """Deterministic integer in ``[0, modulo)``."""
    if modulo <= 0:
        raise ValueError("modulo must be positive")
    return int(stable_unit(*parts) * modulo)


def seeded_rng(*parts: object) -> random.Random:
    """A private :class:`random.Random` seeded from ``parts`` (never the global RNG)."""
    return random.Random(int(stable_unit(*parts) * 2**53))

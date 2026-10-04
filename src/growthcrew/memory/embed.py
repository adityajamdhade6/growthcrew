"""Text embeddings for content memory.

The default embedder hashes words and character n-grams into a fixed-size vector. It needs no
model download and no network, and it matches on shared wording and topic vocabulary. It is a
lexical embedder, not a semantic one: it will not know that "cheap" and "affordable" are
close. Swap in a model-backed `Embedder` when one is available.
"""

import hashlib
import math
import re
from typing import Protocol

DIMENSIONS = 512


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
    return int.from_bytes(digest[:4], "big") % DIMENSIONS, 1.0 if digest[4] & 1 else -1.0


class HashingEmbedder:
    def embed(self, text: str) -> list[float]:
        words = re.findall(r"[a-z0-9']+", text.lower())
        tokens = list(words)
        tokens += [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]
        joined = " ".join(words)
        tokens += [joined[i : i + 4] for i in range(max(0, len(joined) - 3))]
        vector = [0.0] * DIMENSIONS
        for token in tokens:
            index, sign = _bucket(token)
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))

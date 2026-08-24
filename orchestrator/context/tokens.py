"""Token estimation.

An approximation, and labelled as one. tiktoken's o200k_base is a reasonable
proxy across providers but is not the tokenizer most of them actually use, so
every turn records our estimate alongside the provider's reported input_tokens
and logs the drift. Without that comparison, budget enforcement would be an
unfalsifiable claim.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=4)
def _encoder(name: str):
    """Load a tiktoken encoder, or None if tiktoken is unavailable.

    Cached because construction reads a vocabulary file, which is far too slow
    to repeat per context item.
    """
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(name)
    except Exception:
        return None


class TokenEstimator:
    def __init__(self, encoder: str = "o200k_base", chars_per_token: float = 3.6):
        self.encoder_name = encoder
        self.chars_per_token = chars_per_token
        self._enc = _encoder(encoder)

    @property
    def exact(self) -> bool:
        """False when falling back to character division."""
        return self._enc is not None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            return len(self._enc.encode(text, disallowed_special=()))
        # Round up: under-counting would let a package silently exceed budget.
        return int(len(text) / self.chars_per_token) + 1

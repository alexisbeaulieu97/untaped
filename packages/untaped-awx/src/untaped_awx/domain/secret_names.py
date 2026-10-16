"""Variable names that read as secrets, and redacting the values they hold.

One list serves every reader: the launch preview redacts with it, and any
rule that looks for a plaintext secret in launch variables must use the same
words, so a name one hides the other never misses.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

_SECRET_WORDS = frozenset({"pass", "passwd", "password", "passphrase", "pwd", "secret", "token"})
_SECRET_PAIRS = frozenset({"apikey", "accesskey", "privatekey", "secretkey", "sshkey"})
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def looks_secret(name: str) -> bool:
    """Whether a variable name reads as a secret, in any case or separator style.

    Its words (split on ``_``, ``-``, ``.`` and camelCase) are checked:
    ``pass``/``password``/``passphrase``/``pwd``/``secret``/``token``, any
    word ending in ``password``/``passphrase``/``secret``/``token``
    (``dbpassword``), or two adjacent words forming ``api_key``,
    ``access_key``, ``private_key``, ``secret_key`` or ``ssh_key`` (also
    written as one word).
    """
    words = [word.lower() for word in _WORD.findall(name)]
    candidates = [*words, *(a + b for a, b in pairwise(words))]
    return any(
        word in _SECRET_WORDS
        or word in _SECRET_PAIRS
        or word.endswith(("password", "passphrase", "secret", "token"))
        for word in candidates
    )


def redact_secret_names(key: str, value: Any, *, replacement: str) -> Any:
    """``value`` (held under ``key``) with every secret-named value, at any depth, replaced."""
    if looks_secret(key):
        return replacement
    if isinstance(value, dict):
        return {
            k: redact_secret_names(str(k), v, replacement=replacement) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_names("", v, replacement=replacement) for v in value]
    return value


__all__ = ["looks_secret", "redact_secret_names"]

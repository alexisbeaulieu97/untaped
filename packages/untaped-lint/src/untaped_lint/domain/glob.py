"""The one glob lint matches with: ``*`` stops at ``/``, ``**`` crosses it.

``JobTemplate/*/legacy-*`` and ``lint.error-case:tests/**`` read the way they
look; a leading dot is matched like any other character. Every match in
the package goes through :func:`matches`.
"""

from __future__ import annotations

import glob
import re
from functools import cache


@cache
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(glob.translate(pattern, recursive=True, include_hidden=True))


def matches(pattern: str, text: str) -> bool:
    """Whether ``text`` matches the glob ``pattern`` whole."""
    return _compiled(pattern).match(text) is not None

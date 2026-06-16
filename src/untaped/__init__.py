"""The untaped SDK — a batteries-included CLI framework built on cyclopts.

This package root re-exports the public surface defined in :mod:`untaped.api`,
so ``from untaped import X`` and ``from untaped.api import X`` are equivalent.
"""

from untaped import api as _api
from untaped.api import *  # noqa: F403

__all__ = list(_api.__all__)

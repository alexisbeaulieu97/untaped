"""Recipe capability exception hierarchy.

Every error the capability raises on purpose derives from :class:`RecipeError`
(itself an :class:`~untaped.capability_api.UntapedError`), so ``report_errors``
prints it as a clean ``error: ...`` line instead of a traceback. Errors that
older code catches as ``ValueError`` keep that base too.
"""

from __future__ import annotations

from untaped.capability_api import UntapedError


class RecipeError(UntapedError):
    """Base for recipe capability errors."""

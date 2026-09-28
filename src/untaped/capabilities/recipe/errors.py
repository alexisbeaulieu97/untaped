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


class RecipeNotFoundError(RecipeError, ValueError):
    """A recipe ref names no recipe in the library (or in an explicit pack)."""


class RecipeFileNotFoundError(RecipeError, ValueError):
    """An explicit recipe path does not exist on disk."""


class HookNotFoundError(RecipeError, ValueError):
    """A hook ref names no library, project, or built-in hook."""


class AmbiguousRefError(RecipeError, ValueError):
    """A bare recipe or hook ref matches entries in several installed packs."""

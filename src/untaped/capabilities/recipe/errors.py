"""Recipe capability exception hierarchy.

Every error the capability raises on purpose derives from :class:`RecipeError`
(itself an :class:`~untaped.capability_api.UntapedError`), so ``report_errors``
prints it as a clean ``error: ...`` line instead of a traceback. Recipe, pack
and hook files and the inputs a command reads are local input, so an error is
``invalid`` in ``local`` unless its class (or the raise) says otherwise.
Errors that older code catches as ``ValueError`` keep that base too.
"""

from __future__ import annotations

from untaped.capability_api import ErrorCategory, UntapedError


class RecipeError(UntapedError):
    """Base for recipe capability errors: invalid local input unless a subclass says otherwise."""

    category = ErrorCategory.INVALID
    system = "local"


class RecipeNotFoundError(RecipeError, ValueError):
    """A recipe ref names no recipe in the library (or in an explicit pack)."""

    category = ErrorCategory.NOT_FOUND


class RecipeFileNotFoundError(RecipeError, ValueError):
    """An explicit recipe path does not exist on disk."""

    category = ErrorCategory.NOT_FOUND


class HookNotFoundError(RecipeError, ValueError):
    """A hook ref names no library, project, or built-in hook."""

    category = ErrorCategory.NOT_FOUND


class PackNotFoundError(RecipeError, ValueError):
    """A pack name or ref names no installed pack."""

    category = ErrorCategory.NOT_FOUND


class BackupNotFoundError(RecipeError, ValueError):
    """A backup id, prefix, or ``latest`` names no backup bundle."""

    category = ErrorCategory.NOT_FOUND


class PathNotFoundError(RecipeError, ValueError):
    """A file or directory a recipe, pack, or option names does not exist."""

    category = ErrorCategory.NOT_FOUND


class AmbiguousRefError(RecipeError, ValueError):
    """A bare recipe or hook ref matches entries in several installed packs."""

    category = ErrorCategory.INVALID


class LocalChangesError(RecipeError, ValueError):
    """Writing would overwrite local changes (library edits, files changed since a backup)."""

    category = ErrorCategory.CONFLICT


class HookFailedError(RecipeError, ValueError):
    """Hook code ran and failed: it raised, or a validate hook returned a ``fail`` verdict."""

    category = ErrorCategory.FAILED


class PackFetchError(RecipeError, ValueError):
    """A git command fetching a pack source failed; raise it with the git error's attribution."""

    category = ErrorCategory.FAILED
    system = "git"


class UvMissingError(RecipeError, ValueError):
    """The ``uv`` executable hook projects need is not installed (fix the environment)."""

    category = ErrorCategory.CONFIG

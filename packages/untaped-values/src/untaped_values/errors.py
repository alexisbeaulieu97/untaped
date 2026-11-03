"""The values plugin's one error: a value or a declaration that doesn't fit."""

from __future__ import annotations

from untaped.sdk import ErrorCategory, System, UntapedError, experimental


@experimental
class InputError(UntapedError):
    """An input value that doesn't fit its declaration.

    Invalid local input (exit 1): the values come from flags, files and
    answers on this machine; a name nothing declares is a usage error
    instead (exit 2). The message names the input and never repeats
    the value, which may be a secret.
    """

    category = ErrorCategory.INVALID
    system = System.LOCAL

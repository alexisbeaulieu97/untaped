"""The values plugin's one error: a value or a declaration that doesn't fit."""

from __future__ import annotations

from untaped.sdk import ErrorCategory, System, UntapedError, experimental


@experimental
class InputError(UntapedError):
    """An input value that doesn't fit its declaration, or a name nothing declares.

    Invalid local input (exit 1): the values come from flags, files and
    answers on this machine. The message names the input and never repeats
    the value, which may be a secret.
    """

    category = ErrorCategory.INVALID
    system = System.LOCAL

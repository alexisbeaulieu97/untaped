"""What a command's fields can be: positionals and marks, core's value types, core-filled blocks.

A command is a pydantic model (:class:`untaped.sdk.plugin.Command`); this
module is the vocabulary its fields use beyond plain Python types:

- ``Positional[T]`` makes an argument; ``AllowHyphen`` and ``Short("-x")``
  shape one; ``Column``, ``Key``, ``Following`` and ``Written`` mark a
  record's or a file option's fields for core.
- ``File[M]``, ``ExistingFile``, ``TextOrStdin``, ``PathOrStdin``,
  ``KeyValue`` and ``KeyJson`` are value types core parses.
- ``Output[R]``, ``Stdin[R]``, ``Lines``, ``Confirm``, ``DryRun``, ``Refresh``,
  ``Parallel`` and ``Limit`` are fields core fills: each adds its fixed flags,
  configured by the field's default value.
- ``key(Model).field`` is a typed reference to one settings field.

The declaration walk (``untaped.runtime.composition``) reads all of it; a
command never parses a token itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel

from untaped.sdk._block import POSITIONAL, Mark

# --- positionals and marks ------------------------------------------------------

if TYPE_CHECKING:
    type Positional[T, *Marks] = T
    """An argument rather than an option: ``names: Positional[list[str]]``."""
else:

    class Positional:
        """An argument rather than an option: ``names: Positional[list[str]]``.

        Arguments come in declaration order, a bundle's flattened in place.
        Further marks follow the type: ``Positional[str, AllowHyphen]``.
        """

        def __class_getitem__(cls, item: object) -> object:
            items = item if isinstance(item, tuple) else (item,)
            return Annotated[(items[0], POSITIONAL, *items[1:])]


AllowHyphen = Mark("AllowHyphen")
"""An argument that may start with ``-`` (``HEAD~1``, ``-1``): ``Positional[str, AllowHyphen]``."""

Column = Mark("Column")
"""A record field a list's table shows by default: ``name: Annotated[str, Column]``.

None marked: every field. A single record's table shows every field either way.
An outcome's ``action`` and a check's ``status`` are marked by their base; a
subclass redeclaring one keeps the mark only by marking it again.
"""

Key = Mark("Key")
"""The record field a bare identifier stands for, on stdin and in state rows."""

Following = Mark("Following")
"""A list-of-models field a table prints as a second table after the first."""

Bytes = Mark("Bytes")
"""An ``int`` count of bytes a table shows in words (``1.5 KiB``): ``Annotated[int, Bytes]``.

Only the table changes: json, yaml, raw and pipe keep the number.
"""

Age = Mark("Age")
"""A ``datetime`` a table shows as how long ago it was (``3 h ago``), by the run's clock.

Only the table changes: json, yaml, raw and pipe keep the timestamp.
"""

Duration = Mark("Duration")
"""An ``int`` or ``float`` of seconds a table shows as a run time (``1m42s``).

Only the table changes: json, yaml, raw and pipe keep the number.
"""

ObjectName = Mark("ObjectName")
"""A ``str`` git object name a table shortens to 10 characters when it is a full 40-hex one.

Only the table changes: json, yaml, raw and pipe keep the whole name.
"""


class Status:
    """The roles a table draws a field's words in: ``Status(success=("cloned",))``.

    The roles are the theme's (``success``, ``warning``, ``error``), so a user
    theme recolours them; a value no role names stays plain. A subclass that
    redeclares the field adds to its base's roles, the nearest class winning for
    a value. Only the table changes: json, yaml, raw and pipe keep the word.
    """

    __slots__ = ("roles",)

    def __init__(
        self,
        *,
        success: Iterable[object] = (),
        warning: Iterable[object] = (),
        error: Iterable[object] = (),
    ) -> None:
        given = (("success", success), ("warning", warning), ("error", error))
        for role, values in given:
            if isinstance(values, str):
                raise TypeError(f"Status({role}=...) takes a tuple of words, as ({values!r},)")
        self.roles: dict[str, str] = {
            _word(value): role for role, values in given for value in values
        }

    def __repr__(self) -> str:
        return f"Status({self.roles!r})"


def _word(value: object) -> str:
    """A status value as the word a row holds: a ``str``, or a member of an enum of words.

    Anything else (an enum of numbers) is refused: its cell holds no word to draw.
    """
    word = _plain(value)
    if not isinstance(word, str):
        raise TypeError(f"Status takes words (str values), not {value!r}")
    return word


def _plain(value: object) -> object:
    """``value``, or an enum member's value: what a row holds."""
    return value.value if isinstance(value, Enum) else value


class Roled(str):
    """A table cell's text and the theme role its field's ``Status`` gave its word."""

    __slots__ = ("role",)
    role: str

    def __new__(cls, text: str, role: str) -> Roled:
        cell = super().__new__(cls, text)
        cell.role = role
        return cell


Spread = Mark("Spread")
"""A ``dict[str, scalar]`` field a table shows as one column per key, named by the key.

``--columns KEY`` picks one in a table; where the field is a default column its
keys are. The command names its keys as columns once it is built (a model
validator calling ``self.output.columns.add(...)``, or ``.offer(...)`` for a key
hidden by default), so ``--columns`` is checked before ``run``; a named key a
row lacks is an empty cell, and a key it did not name shows by default but is
not a column ``--columns`` picks. Only the table changes: json, yaml, raw and
pipe keep the mapping.
"""

Written = Mark("Written")
"""A ``File[M]`` option the command writes rather than reads: the model's "Starter" line."""


class Document:
    """A value naming a document of ``M`` that the plugin finds and reads itself.

    ``recipe: Annotated[Positional[str], Document(RecipeFile)]`` takes a
    library name or a path, as the plugin resolves them; core checks nothing
    and reads nothing (a path read as it is given is ``File[M]``'s), it only
    learns the model, so ``M`` is one of the command's inputs, documented as a
    ``File[M]`` of it is.
    """

    __slots__ = ("model",)

    def __init__(self, model: type[BaseModel]) -> None:
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TypeError(f"Document takes a pydantic model class, not {model!r}")
        self.model = model

    def __repr__(self) -> str:
        return f"Document({self.model.__name__})"


class Short:
    """A one-letter flag for an option: ``repo: Annotated[str, Short("-r")]``.

    The letters core's own blocks use (``-f``, ``-c``, ``-y``, ``-j``, ``-h``)
    are a composition error.
    """

    __slots__ = ("flag",)

    def __init__(self, flag: str) -> None:
        if len(flag) != 2 or flag[0] != "-" or not flag[1].isalpha():
            raise ValueError(f"Short takes one letter after a dash, as Short('-r'), not {flag!r}")
        self.flag = flag

    def __repr__(self) -> str:
        return f"Short({self.flag!r})"


# --- value types --------------------------------------------------------------


# --- the fields core fills ---------------------------------------------------------

"""The two bases core's blocks share, so the declaration walk recognises them by type.

A :class:`Block` is something a plugin class holds as an attribute (a cache, a
stub skill, a doctor check): the attribute names it, and the plugin class
owns it. A :class:`FieldBlock` is a core-filled field type of a command
(``Output``, ``Stdin``, ``Confirm``...): core fills it and gives it its flags.
Both live here, in the root, so a group's blocks subclass them without the
root importing the group (layout-12.0.md decision 2).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, get_origin

from pydantic_core import PydanticOmit, core_schema

if TYPE_CHECKING:
    from types import FrameType

    from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue


class UnboundBlockError(LookupError):
    """A block was asked for its name before a plugin class held it."""


class Binding(NamedTuple):
    """Where a block is held: the plugin class and the attribute naming it."""

    owner: type
    attribute: str


def _creating_module() -> str:
    """The module that created the block: the first frame outside ``untaped.sdk``."""
    frame: FrameType | None = sys._getframe(2)
    while frame is not None:
        name = str(frame.f_globals.get("__name__", ""))
        if name != "untaped.sdk" and not name.startswith("untaped.sdk."):
            return name
        frame = frame.f_back
    return "__main__"  # pragma: no cover - a block created inside the SDK alone


class Block:
    """Base of every block a plugin class holds; its attribute is its name.

    A block records the module that created it. Assigning it in a plugin
    class's body binds it to that class and attribute (``__set_name__``), so
    a module constant (``MOTD = Cache(...)``) learns its name when the plugin
    class holding it (``motd = MOTD``) is created. A block held by a second
    attribute or plugin keeps every binding; composition refuses all but one.
    Blocks compare and hash by identity, never by their names, so binding
    changes nothing a dict already holds.
    """

    #: The kind of block in the declaration's words (``cache``, ``stub-skill``).
    block_kind: ClassVar[str] = "block"
    #: How the attribute becomes the block's name: as written (a setting key, a
    #: folder) or kebab-cased (an id: ``repo_store`` is ``repo-store``).
    name_style: ClassVar[Literal["attribute", "kebab"]] = "attribute"

    def __init__(self) -> None:
        self._module = _creating_module()
        self._bindings: list[Binding] = []

    def __set_name__(self, owner: type, name: str) -> None:
        from untaped.sdk.plugin import Plugin  # noqa: PLC0415 - plugin imports this module

        if isinstance(owner, type) and issubclass(owner, Plugin):
            self._bindings.append(Binding(owner, name))

    @property
    def module(self) -> str:
        """The module that created the block."""
        return self._module

    @property
    def bindings(self) -> tuple[Binding, ...]:
        """Every plugin class attribute holding the block, in the order they were created."""
        return tuple(self._bindings)

    @property
    def binding(self) -> Binding:
        """The one plugin class attribute holding the block.

        Raises :class:`UnboundBlockError` naming the block and its module when
        no plugin class holds it yet.
        """
        if not self._bindings:
            raise UnboundBlockError(
                f"{type(self).__name__} created in {self._module} is held by no plugin "
                "class yet: assign it to an attribute of the plugin class that owns it"
            )
        return self._bindings[0]


class FieldOption(NamedTuple):
    """One flag a core-filled field adds to a command line."""

    flag: str
    token: str
    help: str
    short: str | None = None
    negative: str | None = None


class FieldBlock:
    """Base of the field types core fills (``Output``, ``Stdin``, ``Confirm``, ...).

    A command requests one by declaring a field of its type; the field's
    default instance configures it (``refresh: Refresh = Refresh(MOTD)``), and
    a field without one gets the type's plain instance, so ``Say(names=[...])``
    builds in a test. Its flags are fixed whatever the field is called, and
    ``model_json_schema()`` leaves it out: a payload never carries core's part.
    """

    #: The kind of field in the declaration's words (``output``, ``stdin``).
    field_kind: ClassVar[str] = "field"
    #: The help panel core lists the field's flags in.
    panel: ClassVar[str] = "Options"
    #: The flags the field adds, before any per-instance ones.
    options: ClassVar[tuple[FieldOption, ...]] = ()

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.is_instance_schema(get_origin(source) or source)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        raise PydanticOmit


class Mark:
    """A mark a field carries in ``Annotated`` metadata (``Column``, ``Key``, ...)."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


#: The mark ``Positional[...]`` puts on a field: an argument, not an option.
POSITIONAL = Mark("Positional")
#: The mark ``TextOrStdin`` and ``PathOrStdin`` carry: ``-`` reads stdin.
OR_STDIN = Mark("OrStdin")

"""Composition's checks of a :class:`~untaped.sdk.network.server_api.ServerApi` declaration.

Each names what is wrong in the declaration's own terms (its port, its
keys, an adapter by class), so a plugin is refused before any command
runs: an unsubscripted block, keys outside the plugin's settings or of the
wrong type, adapters without a docstring or recorded versions, a recorded
version its adapter doesn't serve or two adapters serve, a version type
whose ``parse`` doesn't read back its ``str``, and a deprecated adapter
without its removal or with a replacement that is none of its adapters.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from untaped.sdk._keys import SettingKey
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.marks import Deprecated
from untaped.sdk.network.credentials import CredentialKey
from untaped.sdk.network.server_api import ApiAdapter, ServerVersion

if TYPE_CHECKING:
    from untaped.sdk.network.server_api import ServerApi


def problems(
    block: ServerApi[Any, Any, Any], arguments: tuple[Any, Any, Any] | None, owned: object
) -> tuple[str, ...]:
    """Why ``block``'s declaration can't work; ``owned`` is its plugin's settings model."""
    if arguments is None:
        return (
            "a ServerApi names its port, settings and version types: declare it "
            "subscripted, as in ServerApi[JiraApi, JiraSettings, JiraVersion](...)",
        )
    port, settings, version = arguments
    found = list(_type_problems(port, settings, version))
    found += _key_problems(block, settings, version, owned)
    if isinstance(version, type) and issubclass(version, ServerVersion) and _hashable(version):
        found += _adapter_problems(block.adapters, version)
    return tuple(found)


def _is_protocol(value: object) -> bool:
    return isinstance(value, type) and bool(getattr(value, "_is_protocol", False))


def _type_problems(port: object, settings: object, version: object) -> Iterator[str]:
    """What is wrong with the types the block was subscripted with."""
    if not _is_protocol(port):
        yield f"its port {port!r} is not a Protocol: commands reach it only through its members"
    if not (isinstance(settings, type) and issubclass(settings, BaseModel)):
        yield f"its settings type {settings!r} is not a pydantic model"
    if not (isinstance(version, type) and issubclass(version, ServerVersion)):
        yield f"its version type {version!r} does not subclass ServerVersion"
        return
    if version.__str__ is ServerVersion.__str__ or "parse" not in _defined(version):
        yield f"its version type {version.__name__} must define both __str__() and parse()"
    if not _hashable(version):
        yield (
            f"its version type {version.__name__} is not hashable: declare it "
            "@dataclass(frozen=True)"
        )


def _hashable(cls: type) -> bool:
    return getattr(cls, "__hash__", None) is not None


def _defined(cls: type) -> set[str]:
    """The names ``cls`` or a class between it and :class:`ServerVersion` defines."""
    names: set[str] = set()
    for each in cls.__mro__:
        if each is ServerVersion:
            break
        names.update(vars(each))
    return names


def _key_problems(
    block: ServerApi[Any, Any, Any], settings: object, version: object, owned: object
) -> Iterator[str]:
    """What is wrong with ``server=``, ``credential=`` and ``override=``."""
    for name, setting in (("server", block.server), ("override", block.override)):
        model = SettingKey.model_of(setting)
        if not (isinstance(owned, type) and issubclass(owned, model)):
            yield (
                f"its {name}= is {setting!r}, not a setting of the plugin's own settings "
                f"model ({getattr(owned, '__qualname__', 'none declared')})"
            )
        elif settings is not model and not (
            isinstance(settings, type) and issubclass(model, settings)
        ):
            yield f"its {name}= is {setting!r}, not a setting of {settings!r}"
    held = unwrap_optional(block.server._annotation())
    if not (isinstance(held, type) and issubclass(held, CredentialKey)):
        yield f"its server= {block.server!r} does not hold a CredentialKey (a URL and its TLS)"
    elif block.credential.key is not held:
        yield (
            f"its credential= is for {block.credential.key.__name__}, while server= "
            f"{block.server!r} holds a {held.__name__}"
        )
    overridden = block.override._annotation()
    if unwrap_optional(overridden) is not version or overridden is version:
        name = getattr(version, "__name__", repr(version))
        yield f"its override= {block.override!r} is not a field typed {name} | None"


def _adapter_problems(adapters: Sequence[Any], version: type[ServerVersion]) -> Iterator[str]:
    """What is wrong with the adapters: none, unrecorded, overlapping, unknown replacements."""
    if not adapters:
        yield "it has no adapters: name at least one ApiAdapter subclass"
        return
    checked: list[type] = []
    for adapter in adapters:
        name = getattr(adapter, "__qualname__", repr(adapter))
        if not (isinstance(adapter, type) and issubclass(adapter, ApiAdapter)):
            yield f"its adapter {name} does not subclass ApiAdapter"
            continue
        if not inspect.cleandoc(adapter.__dict__.get("__doc__") or ""):
            yield f"its adapter {name} has no docstring: its first line names the servers it serves"
        if not callable(getattr(adapter, "serves", None)):
            yield f"its adapter {name} has no serves(version) classmethod"
            continue
        recorded = getattr(adapter, "recorded", ())
        if not isinstance(recorded, tuple) or not recorded:
            yield f"its adapter {name} records no version: name the versions its tests ran against"
            continue
        for each in recorded:
            yield from _recorded_problems(adapter, name, each, version)
        yield from _mark_problems(adapter, name, adapters)
        checked.append(adapter)
    yield from _overlaps(checked, version)


def _recorded_problems(
    adapter: type, name: str, each: object, version: type[ServerVersion]
) -> Iterator[str]:
    """What is wrong with one version ``adapter`` records."""
    if not isinstance(each, version):
        yield f"its adapter {name} records {each!r}, not a {version.__name__}"
        return
    try:
        again = version.parse(str(each))
    except ValueError as exc:
        yield f"{version.__name__}.parse() can't read {str(each)!r}, which str() wrote: {exc}"
        return
    if again != each:
        yield f"{version.__name__}.parse(str(v)) != v for {each!r}: it read back {again!r}"
    if not _serves(adapter, each):
        yield f"its adapter {name} does not serve {str(each)!r}, which it records"


def _overlaps(adapters: Sequence[type], version: type[ServerVersion]) -> Iterator[str]:
    """Each recorded version two adapters serve: the pick would depend on their order."""
    recorded = [
        each
        for adapter in adapters
        for each in getattr(adapter, "recorded", ())
        if isinstance(each, version)
    ]
    for each in dict.fromkeys(recorded):
        serving = [adapter.__qualname__ for adapter in adapters if _serves(adapter, each)]
        if len(serving) > 1:
            both = " and ".join(serving)
            yield f"its adapters {both} both serve {str(each)!r}, a recorded version"


def _serves(adapter: type, version: object) -> bool:
    serves = cast("Callable[[object], bool]", adapter.serves)  # type: ignore[attr-defined]
    return bool(serves(version))


def _mark_problems(adapter: type, name: str, adapters: Sequence[Any]) -> Iterator[str]:
    mark = adapter.__dict__.get("stability")
    if not isinstance(mark, Deprecated):
        return
    if mark.removed_in is None:
        yield (
            f"its adapter {name} has a deprecated() mark without removed_in: "
            "name the major removing it"
        )
    if mark.replacement is not None and mark.replacement not in adapters:
        yield f"its adapter {name}'s replacement {mark.replacement!r} is none of its adapters"

"""One file format for the data untaped keeps for itself: a stamp, then the data.

Every such file names what wrote it under the key ``untaped``: the format's
number (``file``) and the untaped version (``by``), and, for data that can
go stale, when it was last refreshed. Two layouts share the stamp:

- the *object* layout, ``{"untaped": {...}, <the model's fields>}``: one JSON
  object whose other keys are a model's fields, at the top level, where a
  reader from before the stamp (11.0) finds them. :func:`read_data_file`,
  :func:`write_data_file` and :func:`copy_data_file` read and write it, for
  a file whose place its owner decides (a repo store, a backup folder, a
  project's ``.untaped/``).
- the *line* layout, core's caches: line 1 ``{"untaped": {...}}`` with a key's
  freshness, line 2 the value, any JSON. Freshness is read without parsing
  the value, and a failed refresh rewrites line 1 and copies line 2's bytes.

Every file is the owner's only (``0600``). A file this release can't read
is never an absent one: a read answers a :data:`DataRead`.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import typing
from collections.abc import Collection, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ValidationError

from untaped.sdk.diagnostic import format_utc, parse_utc
from untaped.sdk.errors import ConfigError
from untaped.sdk.marks import experimental
from untaped.sdk.storage._fs import atomic_write, locked_write, write_temp_name

#: The key every data file keeps its stamp under.
STAMP: Final = "untaped"
#: This format's number: a file with a higher one is a newer untaped's.
FILE: Final = 1
#: Owner-only: what untaped keeps for itself is nobody else's to read.
MODE: Final = 0o600


@experimental
@dataclass(frozen=True)
class Found[M]:
    """The file holds ``value``; ``refreshed_at`` is when it was refreshed, if it says.

    ``by`` is the untaped version its stamp names: ``None`` for a file with
    no stamp (an earlier release's) or a stamp that names none.
    """

    value: M
    refreshed_at: datetime | None = None
    by: str | None = None


@experimental
@dataclass(frozen=True)
class Absent:
    """No file at the path."""


@experimental
@dataclass(frozen=True)
class Unreadable:
    """A file is there, but it is not this model's data file; ``reason`` says why."""

    reason: str


@experimental
@dataclass(frozen=True)
class OtherVersion:
    """A newer untaped wrote the file in a format this release can't read (``file``, ``by``)."""

    file: int
    by: str | None


type DataRead[M] = Found[M] | Absent | Unreadable | OtherVersion


def written_by() -> str:
    """This run's untaped version, as a stamp's ``by`` says it."""
    return _version()


@functools.cache
def _version() -> str:
    try:
        return version("untaped")
    except PackageNotFoundError:
        return "unknown"


def check_model(model: object) -> type[BaseModel]:
    """``model`` once it is a pydantic model with no field or alias named ``untaped``.

    That key is the stamp's: a field under it could never be read back.
    """
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ConfigError(f"a data file holds a pydantic model, not {model!r}")
    _check_fields(model)
    return model


@functools.cache
def _check_fields(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        names = {name, field.alias, field.serialization_alias}
        if isinstance(field.validation_alias, str):
            names.add(field.validation_alias)
        if STAMP in names:
            raise ConfigError(
                f"{model.__name__}.{name}: a data file keeps its stamp under {STAMP!r}, "
                "so no field of its model may be named that"
            )


# --------------------------------------------------------------------------- #
# The object layout
# --------------------------------------------------------------------------- #


@experimental
def read_data_file[M: BaseModel](
    path: Path,
    model: type[M],
    *,
    unstamped: Literal["refuse", "accept"] = "refuse",
    legacy_stamps: Collection[str] = (),
) -> DataRead[M]:
    """What ``path`` holds, read as ``model``: found, absent, unreadable or a newer untaped's.

    ``unstamped="accept"`` reads a file with no stamp in the model's shape:
    one an earlier untaped wrote before the stamp existed. ``"refuse"`` fits
    only files no earlier untaped wrote. A plain string under ``untaped`` is
    data, never a stamp, unless ``legacy_stamps`` names it: the stamps an
    earlier format of this one file wrote, read as that file in the model's
    shape. The model's own rules decide unknown fields (pydantic ignores
    them unless the model forbids them).
    """
    check_model(model)
    try:
        text = path.read_bytes()
    except FileNotFoundError, NotADirectoryError:
        return Absent()
    except OSError as exc:
        return Unreadable(f"cannot be read: {exc.strerror or exc}")
    try:
        data = json.loads(text)
    except ValueError:
        return Unreadable("is not valid JSON")
    if not isinstance(data, dict):
        return Unreadable("is not a JSON object")
    stamp = data.get(STAMP)
    refreshed_at = by = None
    if isinstance(stamp, dict):
        checked = check_stamp(stamp)
        if not isinstance(checked, dict):
            return checked
        refreshed_at = parse_utc(checked.get("refreshed_at"))
        by = checked["by"] if isinstance(checked.get("by"), str) else None
        fields = {key: value for key, value in data.items() if key != STAMP}
    elif isinstance(stamp, str) and stamp in legacy_stamps:
        fields = {key: value for key, value in data.items() if key != STAMP}
    elif unstamped == "accept":
        fields = data
    else:
        return Unreadable(f"has no {STAMP!r} stamp")
    try:
        value = model.model_validate(fields)
    except ValidationError as exc:
        return Unreadable(f"does not hold a {model.__name__}: {first_error(exc)}")
    return Found(value, refreshed_at, by)


@experimental
def write_data_file(
    path: Path,
    value: BaseModel,
    *,
    refreshed_at: datetime | None = None,
    exclusive: bool = False,
) -> bool:
    """Write ``value`` to ``path`` with this release's stamp, owner-only; whether it wrote.

    The model's fields sit at the top level beside the stamp, dumped as JSON
    by alias. ``refreshed_at`` goes in the stamp, for data that can go stale.
    The write is atomic. ``exclusive=True`` creates the file whole and only
    if it does not exist: ``False`` when it does, and a racing reader never
    sees it empty. Creates missing parent folders; raises :class:`OSError`.
    """
    check_model(type(value))
    text = _object_text(value.model_dump(mode="json", by_alias=True), refreshed_at)
    if exclusive:
        return _create(path, text)
    atomic_write(path, text, mode=MODE)
    return True


@experimental
def copy_data_file(source: Path, target: Path) -> None:
    """Copy ``source``'s data to ``target``, stamped by this release, with its freshness cleared.

    For a file whose place another owner decides: the copy holds the same
    data, never claims to have been refreshed, and is written atomically,
    owner-only. Raises :class:`OSError` when ``source`` can't be read or
    ``target`` written, and :class:`ValueError` when ``source`` is not a JSON
    object or a format this release can't read (a newer untaped's).
    """
    data = json.loads(source.read_bytes())
    if not isinstance(data, dict):
        raise ValueError(f"{source} is not a JSON object")
    stamp = data.get(STAMP)
    if isinstance(stamp, dict):
        checked = check_stamp(stamp)
        if isinstance(checked, OtherVersion):
            raise ValueError(f"{source} was written by a newer untaped ({checked.by})")
        if isinstance(checked, Unreadable):
            raise ValueError(f"{source} {checked.reason}")
    kept = {"file": FILE, "by": written_by()}
    copied = {STAMP: kept, **{key: value for key, value in data.items() if key != STAMP}}
    atomic_write(target, json.dumps(copied, ensure_ascii=False) + "\n", mode=MODE)


def _create(path: Path, text: str) -> bool:
    """Create ``path`` holding ``text``, whole and only if absent; whether this call created it.

    The text goes to a temp file first, linked into place, so a racing
    reader never sees an empty file; where links aren't supported an
    exclusive create stands in.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = write_temp_name(path)
    try:
        _write_new(tmp, text)  # a failure here leaves no file at path
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        except OSError:  # links aren't supported here
            return _create_exclusive(path, text)
        return True
    finally:
        with suppress(OSError):
            tmp.unlink()


def _create_exclusive(path: Path, text: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, MODE)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(OSError):
            path.unlink()
        raise
    return True


def _write_new(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, MODE)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


#: How long a write waits for another process writing the same data file.
_WRITE_WAIT = 10.0


@experimental
class DataFile[M: BaseModel]:
    """One small stamped file of ``M`` wherever its owner decides: ``DataFile[Mark].at(path)``.

    For a file another owner places (a repo store's private file, a marker
    beside a skill, a project's file), named per call. It is the object
    layout: the stamp under ``untaped``, the model's fields beside it.
    :meth:`read` answers what is there; :meth:`write` writes under the file's
    lock and keeps the keys the model doesn't know (a newer untaped's), and
    never overwrites a newer untaped's file.
    """

    __slots__ = ("model", "path")

    model: type[M]
    path: Path

    def __init__(self, path: Path, model: type[M]) -> None:
        self.model = check_model(model)  # type: ignore[assignment]
        self.path = Path(path)

    @classmethod
    def __class_getitem__(cls, params: object) -> object:
        # ``DataFile[Mark]`` keeps its model for ``.at(path)``, which a plain
        # alias hands to the class without its arguments.
        alias = super().__class_getitem__(params)  # type: ignore[misc]
        return _FileAlias(alias.__origin__, alias.__args__)

    @classmethod
    def at(cls, path: Path, *, model: type[M] | None = None) -> DataFile[M]:
        """The data file at ``path``; ``DataFile[Mark].at(path)`` names its model."""
        if model is None:
            raise ConfigError("name the data file's model, as in DataFile[Mark].at(path)")
        return cls(path, model)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DataFile):
            return NotImplemented
        return (self.model, self.path) == (other.model, other.path)

    def __hash__(self) -> int:
        return hash((self.model, self.path))

    def __repr__(self) -> str:
        return f"DataFile[{self.model.__name__}].at({str(self.path)!r})"

    def read(self) -> DataRead[M]:
        """What the file holds: found, absent, unreadable, or a newer untaped's."""
        return read_data_file(self.path, self.model)

    def write(
        self, value: M, *, refreshed_at: datetime | None = None, exclusive: bool = False
    ) -> bool:
        """Write ``value``, stamped by this release, owner-only; whether it wrote.

        Keys the file holds that the model doesn't know are kept, and a field
        whose value is unchanged keeps its stored spelling. ``refreshed_at``
        goes in the stamp, for data that can go stale. ``exclusive=True``
        writes only when no file is there (``False`` when one is). A newer
        untaped's file, or a write that fails, is a :class:`ConfigError`.
        """
        if not isinstance(value, self.model):
            raise ConfigError(
                f"{self!r} keeps a {self.model.__name__}, not a {type(value).__name__}"
            )
        busy = f"{self.path} is busy (another untaped process)"
        with locked_write(self.path, timeout=_WRITE_WAIT, busy=busy):
            before = self.read()
            if isinstance(before, OtherVersion):
                raise ConfigError(
                    f"{self.path} was written by a newer untaped ({before.by or 'unknown'}), "
                    "whose format this release cannot write",
                    hint="upgrade untaped",
                    details={"path": str(self.path)},
                )
            if exclusive and not isinstance(before, Absent):
                return False
            body = value.model_dump(mode="json", by_alias=True)
            if isinstance(before, Found):
                body = self._kept(before.value, body)
            try:
                atomic_write(self.path, _object_text(body, refreshed_at), mode=MODE)
            except OSError as exc:
                raise ConfigError(
                    f"could not write {self.path}: {exc.strerror or exc}; nothing changed",
                    details={"path": str(self.path)},
                ) from exc
        return True

    def copy_to(self, path: Path) -> DataFile[M]:
        """Copy this file's data to ``path``, stamped by this release, its freshness cleared.

        Raises :class:`OSError` or :class:`ValueError` as :func:`copy_data_file` does.
        """
        copy_data_file(self.path, Path(path))
        return DataFile(Path(path), self.model)

    def _kept(self, current: M, body: dict[str, Any]) -> dict[str, Any]:
        """``body`` laid over the stored fields, so keys this release doesn't know survive."""
        from untaped.sdk.storage._write_back import write_back  # noqa: PLC0415 - writes only

        try:
            stored = json.loads(self.path.read_bytes())
        except OSError, ValueError:
            return body
        if not isinstance(stored, dict):
            return body
        stored.pop(STAMP, None)
        known = current.model_dump(mode="json", by_alias=True)
        return write_back(self.model, stored, known, body)


class _FileAlias(typing._GenericAlias, _root=True):  # type: ignore[call-arg,name-defined,misc]
    """``DataFile[Mark]``: the alias that hands its model to ``.at(path)`` too."""

    def at(self, path: Path) -> DataFile[Any]:
        return DataFile(path, self.__args__[0])


def _object_text(body: Mapping[str, Any], refreshed_at: datetime | None) -> str:
    """The object layout of ``body``, stamped by this release (and ``refreshed_at``)."""
    stamp: dict[str, Any] = {"file": FILE, "by": written_by()}
    if refreshed_at is not None:
        stamp["refreshed_at"] = format_utc(refreshed_at)
    return json.dumps({STAMP: stamp, **body}, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- #
# The line layout (core's caches)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Lines:
    """A line-layout file: its stamp (line 1) and line 2's bytes (``None``: no line 2).

    ``holds`` says whether line 2 has a value, also when it was left unread.
    """

    stamp: Mapping[str, Any]
    value: bytes | None
    holds: bool


def read_lines(path: Path, *, value: bool = True) -> Lines | Absent | Unreadable | OtherVersion:
    """``path`` in the line layout; the value's bytes are left unparsed.

    ``value=False`` reads line 1 only (a status read of a large value):
    ``holds`` still says whether a value follows, ``value`` is ``None``.
    """
    try:
        with path.open("rb") as file:
            if value:
                text = file.read()
            else:
                text = file.readline()
                text += file.read(2)  # enough to tell a value from a bare newline
    except FileNotFoundError, NotADirectoryError:
        return Absent()
    except OSError as exc:
        return Unreadable(f"cannot be read: {exc.strerror or exc}")
    head, newline, rest = text.partition(b"\n")
    try:
        first = json.loads(head)
    except ValueError:
        return Unreadable("its first line is not valid JSON")
    stamp = first.get(STAMP) if isinstance(first, dict) else None
    if not isinstance(stamp, dict):
        return Unreadable(f"its first line has no {STAMP!r} stamp")
    checked = check_stamp(stamp)
    if not isinstance(checked, dict):
        return checked
    rest = rest.rstrip(b"\n") if newline else b""
    return Lines(stamp=checked, value=(rest or None) if value else None, holds=bool(rest))


def write_lines(path: Path, stamp: Mapping[str, Any], value: bytes | None) -> None:
    """Write ``stamp`` (with this release's ``file`` and ``by``) then ``value``, atomically."""
    line = json.dumps(
        {STAMP: {"file": FILE, "by": written_by(), **stamp}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    content = line + b"\n" if value is None else line + b"\n" + value + b"\n"
    atomic_write(path, content, mode=MODE)


# --------------------------------------------------------------------------- #
# The stamp and the type hash
# --------------------------------------------------------------------------- #


def check_stamp(stamp: Mapping[str, Any]) -> dict[str, Any] | Unreadable | OtherVersion:
    """``stamp`` when this release reads its ``file`` number; else why not."""
    number = stamp.get("file")
    by = stamp.get("by")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return Unreadable(f"its {STAMP!r} stamp names no format")
    if number > FILE:
        return OtherVersion(file=number, by=by if isinstance(by, str) else None)
    return dict(stamp)


#: Schema keywords that describe a type to a human: never part of its shape.
_DESCRIPTIVE = frozenset({"title", "description", "examples"})
#: Keywords whose value maps names to schemas: the names are data, not keywords.
_NAMED = frozenset({"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"})
#: Keywords whose value is data, kept as it is.
_LITERAL = frozenset({"default", "const", "enum"})


def type_hash(schema: Mapping[str, Any]) -> str:
    """The hash of a JSON schema's shape: titles, descriptions and examples left out, sorted.

    A docstring, a description or the order of fields never changes it; a
    field, a type or a nested model's name (its ``$defs`` entry) does.
    """
    shape = json.dumps(_shape(schema), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(shape.encode()).hexdigest()[:16]


def _shape(node: Any) -> Any:
    if isinstance(node, list):
        return [_shape(item) for item in node]
    if not isinstance(node, dict):
        return node
    shaped: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DESCRIPTIVE:
            continue
        if key in _LITERAL:
            shaped[key] = value
        elif key in _NAMED and isinstance(value, dict):
            shaped[key] = {name: _shape(each) for name, each in value.items()}
        elif key == "required" and isinstance(value, list):
            shaped[key] = sorted(value, key=str)
        else:
            shaped[key] = _shape(value)
    return shaped


def first_error(exc: ValidationError) -> str:
    """The first problem pydantic found, as ``where: what``."""
    error = exc.errors(include_url=False)[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else str(error["msg"])


__all__ = [
    "Absent",
    "DataRead",
    "Found",
    "OtherVersion",
    "Unreadable",
    "copy_data_file",
    "read_data_file",
    "write_data_file",
]

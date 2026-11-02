"""Reading ``config.yml`` and ``state.yml``: the YAML, its root and its format stamp.

A non-empty ``config.yml`` must carry ``format_version`` equal to
:data:`FORMAT_VERSION`: an older stamp (or none: 11.x's format 1) is
refused, and so is a newer one, which an older untaped would misread.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from untaped.runtime.profile_resolver import DEFAULT_PROFILE
from untaped.sdk.errors import ConfigError

STATE_FILE_NAME = "state.yml"


#: On-disk format of ``config.yml``: format 2 holds the base at the top level
#: and named profiles under ``profiles:``. A non-empty file must carry
#: ``format_version: 2``; an unstamped one is 11.x's format 1, refused. Bump
#: only in a major release, when an older reader ignoring a new core key would
#: change behaviour (see CONTRIBUTING.md). A renamed key (``renamed_keys``)
#: does not bump it: a bump would make an older release refuse the whole file,
#: and ``untaped config migrate`` renames keys only when the user runs it.
FORMAT_VERSION = 2


def load_config_yaml(yaml_file: Path) -> dict[str, Any]:
    """Parse ``config.yml`` into a dict (``{}`` when absent or empty).

    Unreadable files, YAML syntax errors, a non-mapping document root, a
    file that is not format 2 (:func:`check_config_format`) and an 11.x
    ``state.yml`` beside it all raise :class:`ConfigError` naming the path.
    """
    _check_no_shared_state(yaml_file.parent)
    raw = _read_yaml(yaml_file)
    check_config_format(raw, yaml_file)
    return raw


def _check_no_shared_state(folder: Path) -> None:
    """Refuse a folder holding 11.x's shared ``state.yml`` beside the config read from it.

    12.0 keeps each plugin's state in its own ``plugins/<name>/state.yml``
    and never reads the shared file, so it has no upgrade path either.
    """
    shared = folder / STATE_FILE_NAME
    if shared.exists():
        raise OlderFormatError(
            f"{shared} was written by untaped 11.x; this release keeps each plugin's state "
            "under plugins/ and has no upgrade path",
            hint="delete it, or keep untaped 11.1 for this home",
        )


def load_state_yaml(yaml_file: Path) -> dict[str, Any]:
    """Parse a plugin's ``state.yml`` into a dict (``{}`` when absent or empty).

    Raises as :func:`load_config_yaml` does for an unreadable file, a YAML
    syntax error or a non-mapping root; its stamp is the state rows' to check.
    """
    return _read_yaml(yaml_file)


def _read_yaml(yaml_file: Path) -> dict[str, Any]:
    if not yaml_file.exists():
        return {}
    if not yaml_file.is_file():
        raise ConfigError(f"could not read {yaml_file}: not a file")
    try:
        with yaml_file.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {yaml_file}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"could not read {yaml_file}: not UTF-8 text ({exc.reason})") from exc
    except OSError as exc:
        raise ConfigError(f"could not read {yaml_file}: {exc.strerror or exc}") from exc
    return _checked_root(raw, yaml_file)


def check_config_text(text: str, path: Path) -> None:
    """Raise :class:`ConfigError` when ``text`` is not a readable config document.

    Applies the same root and format checks as :func:`load_config_yaml`
    to text already read from ``path``.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    check_config_format(_checked_root(raw, path), path)


def _checked_root(raw: Any, path: Path) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"invalid config in {path}: the document root must be a mapping, "
            f"got {type(raw).__name__}"
        )
    return raw


class FormatVersionError(ConfigError):
    """A config or state file this release must not read or write."""


class NewerFormatError(FormatVersionError):
    """A file stamped with a ``format_version`` newer than this release reads."""

    def __init__(self, message: str, *, version: int, path: Path) -> None:
        super().__init__(message)
        self.version = version
        self.path = path


class OlderFormatError(FormatVersionError):
    """A ``config.yml`` (format 1) or shared ``state.yml`` untaped 11.x wrote: no upgrade path."""


def check_config_format(raw: dict[str, Any], path: Path) -> None:
    """Refuse a ``config.yml`` this release must not read: not format 2, or holding 11.x keys.

    11.x's keys are ``active:`` and ``profiles.default``. An empty file is a
    fresh home. Raises :class:`FormatVersionError` (exit 4), so nothing is
    written beside a file this release must not read.
    """
    if not raw:
        return
    stamp = raw.get("format_version")
    if "format_version" not in raw or (type(stamp) is int and stamp == 1):
        raise OlderFormatError(
            f"{path} was written by untaped 11.x (format 1); this release reads format "
            f"{FORMAT_VERSION} and has no upgrade path",
            hint=(
                f"delete it and run `untaped setup`, or keep untaped 11.1 for this home; "
                f"a file written for 12.0 starts with `format_version: {FORMAT_VERSION}`"
            ),
        )
    _check_stamp(raw, path, newest=FORMAT_VERSION)
    if "active" in raw:
        raise FormatVersionError(
            f"`active:` in {path} is not read: a run picks its profiles with "
            "--profile or UNTAPED_PROFILE, else reads the base alone",
            hint="delete the `active:` line",
        )
    profiles = raw.get("profiles")
    if isinstance(profiles, dict) and DEFAULT_PROFILE in profiles:
        raise FormatVersionError(
            f"profiles.default in {path} is not read: the base is config.yml's top level",
            hint="move its sections to the top of the file",
        )


def _check_stamp(raw: dict[str, Any], path: Path, *, newest: int) -> None:
    if "format_version" not in raw:
        return
    value = raw["format_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FormatVersionError(  # invalid stamps get the same write-path protection
            f"invalid format_version in {path}: expected a positive integer, got {value!r}"
        )
    if value > newest:
        raise NewerFormatError(
            f"{path} was written by a newer untaped (format {value}; "
            f"this release reads format {newest}); upgrade untaped",
            version=value,
            path=path,
        )

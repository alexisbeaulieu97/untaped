"""Doctor's row for the current project's ``.untaped/config.yml``.

It fails for what the file may not set or sets wrong, blaming the file only
for its own values (never the home's or a variable's), and warns when the
project's pin admits only an untaped without projects, which ignores it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from untaped.core.doctor_row import FAIL, PASS, WARN, doctor_row
from untaped.core.settings import NAME, CoreSettings
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.settings import (
    active_settings_layout,
    check_settings_field,
    profile_section_models,
)
from untaped.sdk.errors import ConfigError, first_validation_error
from untaped.sdk.words import shown_path

if TYPE_CHECKING:
    from untaped.runtime.folder_layer import FolderLayer


def project_config_row() -> dict[str, object] | None:
    """The current project's ``.untaped/config.yml``: read, or why not; ``None`` outside one."""
    from untaped.core.docs import older_pin_note  # noqa: PLC0415 - read only here
    from untaped.runtime.folder_layer import folder_layer  # noqa: PLC0415 - read only here

    title = "load project config file"
    try:
        layer = folder_layer()
        if layer is not None:
            _check_project_values(layer)
    except ConfigError as exc:
        detail = str(exc) if exc.hint is None else f"{exc}; {exc.hint}"
        return doctor_row("config", NAME, FAIL, title, f"{detail} (the rows below leave it out)")
    if layer is None:
        return None
    note = older_pin_note(Path.cwd())
    if note is not None:
        return doctor_row("config", NAME, WARN, title, note)
    return doctor_row("config", NAME, PASS, title, str(layer.file))


def _check_project_values(layer: FolderLayer) -> None:
    """Refuse (:class:`ConfigError`) what the project's file may not set or sets wrong.

    The file's values are checked twice: alone, over the defaults (so a bad
    value in the home or a variable can't hide one of the file's), and as
    a run inside the project reads them (the base, the project's picks, its
    values, variables), blamed on the file only when that check passes
    without its values: a bad value in the home is the home's rows' to report.
    """
    layout = active_settings_layout()
    alone = layout.project_effective({}, replace(layer, picks=()))  # refuses what it may not set
    models = profile_section_models()
    checked = [
        (section, part)
        for section in sorted(set(layer.sections) & set(models))
        for part in ((None,) if section != NAME else tuple(CoreSettings.model_fields))
    ]
    for section, part in checked:
        node = alone.get(section)
        if part is not None:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                continue
            node = {part: node}
        try:
            models[section].model_validate(node or {})
        except ValidationError as exc:
            if any(_sets(node, error["loc"], error["type"]) for error in exc.errors()):
                where = section if part is None else f"{section}.{part}"
                raise ConfigError(
                    f"invalid config section {where!r} in {shown_path(layer.file)}: "
                    f"{first_validation_error(exc)}"
                ) from exc
    try:
        raw = read_config_dict()
    except ConfigError, OSError:
        return  # the home's own row reports it
    with_file = layout.project_effective(raw, layer)
    without = layout.project_effective(raw, replace(layer, sections={}))
    for section, part in checked:
        why = _section_error(with_file, section, models[section], part)
        if why is not None and _section_error(without, section, models[section], part) is None:
            raise ConfigError(why)


def _sets(node: object, loc: tuple[int | str, ...], kind: str = "value_error") -> bool:
    """Whether the error of ``kind`` at ``loc`` is the file's own: ``node`` sets a value there.

    The file's when it holds a single value there, or any value of the wrong
    type (``kind`` ``*_type``). A missing field, or a check of a whole model
    or list (which may rest on values the home supplies), is left to the
    check with the home.
    """
    for step in loc:
        held = (isinstance(node, dict) and step in node) or (
            isinstance(node, list) and isinstance(step, int) and step < len(node)
        )
        if not held:
            return False
        node = node[step]  # type: ignore[index]
    return kind.endswith("_type") or not isinstance(node, (dict, list))


def _section_error(
    effective: Mapping[str, Any], section: str, model: type[BaseModel], part: str | None
) -> str | None:
    """Why ``section`` (or one ``part`` of core's) of ``effective`` fails a run's check."""
    node = effective.get(section)
    if part is not None and isinstance(node, dict):
        node = None if node.get(part) is None else {part: node[part]}
    try:
        check_settings_field(section, node, model=model, part=part)
    except ConfigError as exc:
        return str(exc)
    return None

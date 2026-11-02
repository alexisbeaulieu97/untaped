"""Settings from the environment: ``UNTAPED_<SECTION>__<KEY>`` variables.

A variable names a key of the active settings by its section and path;
an old key's variable (``renamed_keys``) still sets the new key, and a
retired one is named so doctor can say what replaced it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic_settings.sources import EnvSettingsSource

from untaped.runtime.section_models import CREDENTIAL_NAMES, model_sections
from untaped.sdk.health.deprecated_keys import (
    key_mappings,
    notify_use,
    rename_keys,
)


def env_var_name(path: Iterable[str]) -> str:
    """The ``UNTAPED_*`` variable that sets the setting at ``path`` (``("github", "token")``).

    The one place an environment name is built. A hyphen becomes an
    underscore, as in the plugin's import package (``acme-tools`` reads
    ``UNTAPED_ACME_TOOLS__KEY``): plugin names hold no ``_``, so the
    spelling stays unambiguous, and ``__`` stays the nesting delimiter.
    """
    return "UNTAPED_" + "__".join(part.replace("-", "_") for part in path).upper()


def env_name_of(section: str, key: str) -> str:
    """The ``UNTAPED_*`` variable that sets ``key`` of ``section``."""
    return env_var_name([section, *key.split(".")])


def env_is_set(name: str) -> bool:
    return any(key.upper() == name for key in os.environ)


def env_sets_key(section: str, key: str) -> bool:
    """Whether the environment sets top-level field ``key`` of ``section``, blank or not.

    Its ``UNTAPED_<SECTION>__<KEY>`` variable, or the key inside an
    ``UNTAPED_<SECTION>`` JSON object; a blob that isn't a JSON object
    counts, since it cannot be told apart. Names match case-insensitively,
    as the settings source reads them. ``key`` is the current name; an old
    name from ``renamed_keys`` is not checked.
    """
    if env_is_set(env_name_of(section, key)):
        return True
    blob_name = env_var_name([section])
    blob = next((value for name, value in os.environ.items() if name.upper() == blob_name), None)
    if blob is None:
        return False
    try:
        data = json.loads(blob)
    except ValueError:
        return True
    return not isinstance(data, dict) or key in data


def env_var_setting(
    path: tuple[str, ...], *, environ: Mapping[str, str] | None = None
) -> str | None:
    """The set ``UNTAPED_*`` variable that supplies the leaf at ``path``, if any.

    The deepest one wins: the leaf's own variable, else an ancestor's JSON
    object that holds it; an ancestor whose value (or a level inside it)
    isn't a JSON object counts, since it cannot be told apart. Old spellings
    are not checked; ``env_var_supplying`` finds a field's. ``environ``
    defaults to this process's variables.
    """
    variables = os.environ if environ is None else environ
    for depth in range(len(path), 0, -1):
        name = env_var_name(path[:depth])
        value = next((text for key, text in variables.items() if key.upper() == name), None)
        if value is None:
            continue
        if depth == len(path):
            return name
        try:
            data: Any = json.loads(value)
        except ValueError:
            return name
        for part in path[depth:]:
            if not isinstance(data, dict):
                return name
            if part not in data:
                break
            data = data[part]
        else:
            return name
    return None


@dataclass(frozen=True)
class RetiredVariable:
    """A set ``UNTAPED_*`` variable naming a key that is no longer read."""

    name: str
    new: str | None = None
    """The current key's variable, for a retired key."""
    note: str | None = None
    """The :class:`~untaped.sdk.health.deprecated_keys.Retired` note, for a deleted key."""


def retired_env_vars(sections: Mapping[str, type[BaseModel]]) -> list[RetiredVariable]:
    """Each set variable spelling a retired or deleted key of ``sections``.

    Neither is read any more, so without this a variable left in a shell
    profile or CI is ignored with no signal.
    """
    found: list[RetiredVariable] = []
    for section, model in sections.items():
        mappings = key_mappings(model)
        for old in sorted(mappings.retired):
            name = env_name_of(section, old)
            if env_is_set(name):
                found.append(
                    RetiredVariable(name, new=env_name_of(section, mappings.migratable[old]))
                )
        for old, deleted in sorted(mappings.deleted.items()):
            name = env_name_of(section, old)
            if env_is_set(name):
                found.append(RetiredVariable(name, note=deleted.reason()))
    return found


def _env_spelling(section: str, key: str) -> str:
    """How the environment spells ``key``: its variable when set, else a key in the JSON blob."""
    name = env_name_of(section, key)
    return name if env_is_set(name) else f"{key} in {env_var_name([section])}"


class RenamingEnvSource(EnvSettingsSource):
    """The ``UNTAPED_*`` environment source, with old key names renamed.

    pydantic-settings builds each section's dict from its ``UNTAPED_<SECTION>__*``
    variables (and an ``UNTAPED_<SECTION>`` JSON blob) without checking the
    inner names, so old names arrive here and are renamed like YAML keys.
    """

    def _load_env_vars(self) -> Mapping[str, str | None]:
        """The environment, with a hyphenated section's variables under its field name.

        pydantic-settings looks a section up by its field name, so
        ``UNTAPED_ACME_TOOLS__KEY`` (see :func:`env_var_name`) is read as
        ``UNTAPED_ACME-TOOLS__KEY``.
        """
        env_vars = dict(super()._load_env_vars())
        for section in self.settings_cls.model_fields:
            if "-" not in section:
                continue
            spelled = env_var_name([section])
            field = f"{self.env_prefix}{section}"
            if not self.case_sensitive:
                spelled, field = spelled.lower(), field.lower()
            moved = [key for key in env_vars if key.partition("__")[0] == spelled]
            for key in moved:
                env_vars[field + key[len(spelled) :]] = env_vars.pop(key)
        return env_vars

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        for section, model in model_sections(self.settings_cls).items():
            value = data.get(section)
            if not isinstance(value, dict) or not key_mappings(model):
                continue
            data[section], uses = rename_keys(model, value)
            overrides = CREDENTIAL_NAMES.get(model, frozenset())
            for use in uses:
                if use.kind == "deprecated" and use.old in overrides:
                    continue  # UNTAPED_<SECTION>__<NAME> is a credential's override, not deprecated
                # The new key follows the old one's form: a variable or a blob key.
                as_variable = env_is_set(env_name_of(section, use.old))
                notify_use(
                    use,
                    section=section,
                    old=_env_spelling(section, use.old),
                    new=env_name_of(section, use.new) if as_variable else use.new,
                    kept=None if use.kept is None else _env_spelling(section, use.kept),
                )
        return data

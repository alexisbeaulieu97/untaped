"""Settings-file adapter wiring schema introspection + YAML I/O + env detection together.

Scope (profile) awareness goes through the settings layout: every write —
including ``http``/``ui``, which are ordinary per-profile settings now — lands
in ``profiles.<target>``.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import SecretStr, TypeAdapter, ValidationError

from untaped.config_file import (
    mutate_config,
    read_config_dict,
    set_at_path,
    unset_at_path,
)
from untaped.config_schema import FieldDescriptor, find_descriptor, walk_settings
from untaped.errors import ConfigError, first_validation_error
from untaped.settings import (
    Settings,
    active_settings_layout,
    get_profile_settings_model,
    load_settings_section,
    validate_settings_section,
)


class SettingsFileRepository:
    """Single concrete adapter for everything ``config`` needs.

    Reads and writes validate only the section a key belongs to, so one
    invalid value elsewhere in the file never blocks inspecting or repairing
    the rest of the config through the CLI.
    """

    def __init__(self, settings_cls: type[Settings] | None = None) -> None:
        self._settings_cls = settings_cls
        self._descriptors: list[FieldDescriptor] | None = None
        self._sections: dict[str, Any] = {}

    def descriptors(self) -> list[FieldDescriptor]:
        if self._descriptors is None:
            self._descriptors = walk_settings(self._profile_model())
        return self._descriptors

    def descriptor(self, key: str) -> FieldDescriptor:
        descriptors = self.descriptors()
        descriptor = find_descriptor(descriptors, key)
        if descriptor is None:
            valid = ", ".join(d.key for d in descriptors)
            raise ConfigError(f"unknown setting: {key!r}. Valid keys: {valid}")
        return descriptor

    def setting_value(self, descriptor: FieldDescriptor) -> Any:
        """Effective (env ⤥ profile ⤥ default) value of one leaf.

        Validates only the leaf's top-level section; raises ``ConfigError``
        when that section is invalid.
        """
        section = descriptor.path[0]
        if section not in self._sections:
            self._sections[section] = load_settings_section(section, self._settings_cls)
        cursor: Any = self._sections[section]
        for segment in descriptor.path[1:]:
            cursor = getattr(cursor, segment, None)
            if cursor is None:
                return None
        return cursor

    def raw_setting_value(self, descriptor: FieldDescriptor) -> Any:
        """Unvalidated effective value of one leaf (env string wins over YAML)."""
        env_value = self.env_value_for(descriptor)
        if env_value is not None:
            return env_value
        cursor: Any = active_settings_layout().effective(self.yaml_dict())
        for segment in descriptor.path:
            if not isinstance(cursor, dict) or segment not in cursor:
                return None
            cursor = cursor[segment]
        return cursor

    def yaml_dict(self) -> dict[str, Any]:
        """The raw, unmerged YAML dict (top-level)."""
        return read_config_dict()

    def provenance(self) -> dict[tuple[str, ...], str]:
        """Map every leaf path that came from YAML to its supplying scope."""
        try:
            return active_settings_layout().provenance(self.yaml_dict())
        except ConfigError:
            return {}

    def profile_names(self) -> list[str]:
        return active_settings_layout().profile_names(self.yaml_dict())

    def profile_data(self, name: str) -> dict[str, Any] | None:
        return active_settings_layout().profile_data(self.yaml_dict(), name)

    def profile_value_for(self, descriptor: FieldDescriptor, profile: str) -> Any:
        """Raw value at ``descriptor.path`` in ``profile``'s effective view.

        Resolves through the layout's layering (e.g. ``profiles.default``
        beneath ``profiles.<profile>``); returns ``None`` when the profile's
        view doesn't set the leaf.
        """
        effective = active_settings_layout().effective(self.yaml_dict(), profile=profile)
        cursor: Any = effective
        for segment in descriptor.path:
            if not isinstance(cursor, dict) or segment not in cursor:
                return None
            cursor = cursor[segment]
        return cursor

    def env_var_for(self, descriptor: FieldDescriptor) -> str:
        return "UNTAPED_" + "__".join(descriptor.path).upper()

    def env_value_for(self, descriptor: FieldDescriptor) -> str | None:
        return os.environ.get(self.env_var_for(descriptor))

    def set_value(self, key: str, raw_value: str, *, profile: str | None = None) -> str:
        """Validate ``raw_value`` against the key's type, then persist.

        The raw string is validated against the leaf's annotation (lax mode:
        ``true``/``no`` for bools, numbers, literals, paths); string and
        secret fields keep the exact input. Only the key's own section is
        re-validated, so an unrelated invalid section never blocks a repair.
        Returns the resolved target profile name so callers can report where
        the write landed.
        """
        descriptor = self.descriptor(key)
        value = _coerce_value(key, descriptor, raw_value)
        resolved: str | None = None

        def _apply(data: dict[str, Any]) -> None:
            nonlocal resolved
            target_data, resolved = active_settings_layout().write_profile(data, profile)
            set_at_path(target_data, descriptor.path, value)
            try:
                self._validate_section(data, descriptor, profile=resolved)
            except ValidationError as exc:
                raise ConfigError(
                    f"invalid value for {key!r}: {first_validation_error(exc)}"
                ) from exc

        mutate_config(_apply)
        # ``mutate_config`` always runs ``_apply``, which sets ``resolved`` from
        # ``write_profile`` (always a profile name).
        assert resolved is not None
        return resolved

    def unset_value(self, key: str, *, profile: str | None = None) -> tuple[bool, str]:
        """Remove ``key`` from the resolved write scope.

        Returns ``(removed, target)``. An explicit ``--target-profile`` the
        layout cannot satisfy raises ``ConfigError``. Removing a key that
        simply isn't set in the resolved scope is a no-op
        (``removed=False``).
        """
        descriptor = self.descriptor(key)
        removed = False
        resolved: str | None = None

        def _apply(data: dict[str, Any]) -> None:
            nonlocal removed, resolved
            target_data, resolved = active_settings_layout().write_profile(data, profile)
            if not unset_at_path(target_data, descriptor.path):
                return
            removed = True
            # Symmetric with ``set_value``: re-validate the key's section so a
            # removal that would leave the scope in a state pydantic would
            # reject surfaces here (with the offending key in the message),
            # not at next-load with an opaque traceback.
            try:
                self._validate_section(data, descriptor, profile=resolved)
            except ValidationError as exc:
                raise ConfigError(
                    f"unsetting {key!r} would leave profile {resolved!r} invalid: "
                    f"{first_validation_error(exc)}"
                ) from exc

        mutate_config(_apply)
        # ``write_profile`` (run inside ``_apply``, before the early return) always
        # sets ``resolved`` to a profile name.
        assert resolved is not None
        return removed, resolved

    def _profile_model(self) -> type[Settings]:
        return self._settings_cls or get_profile_settings_model()

    def _validate_section(
        self, data: dict[str, Any], descriptor: FieldDescriptor, *, profile: str
    ) -> None:
        """Validate the written key's section as ``profile`` would resolve it.

        Merges from the *target* profile's perspective — otherwise an invalid
        value silently lands in a non-active profile and only fails when that
        profile is later activated. Env overrides are deliberately ignored:
        the file must be valid on its own.
        """
        effective = active_settings_layout().effective(data, profile=profile)
        validate_settings_section(effective, descriptor.path[0], self._profile_model())


def _coerce_value(key: str, descriptor: FieldDescriptor, raw_value: str) -> Any:
    """Validate a CLI-supplied string against the leaf type; return its YAML form.

    ``str``/``SecretStr`` fields keep the input verbatim (no YAML parsing, so
    ``p4ss #word`` or ``0123`` survive intact). Other types validate the raw
    string in pydantic's lax mode and store the JSON-mode dump (e.g. a
    ``Path`` as a string). For those non-string types the literal ``null``
    stores ``None``; the section validation in ``set_value`` rejects it when
    the field is not optional. (``config unset`` removes a key instead.)
    """
    if descriptor.annotation in (str, SecretStr):
        return raw_value
    if raw_value == "null":
        return None
    adapter: TypeAdapter[Any] = TypeAdapter(descriptor.annotation)
    try:
        value = adapter.validate_strings(raw_value)
    except ValidationError as exc:
        raise ConfigError(f"invalid value for {key!r}: {first_validation_error(exc)}") from exc
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return adapter.dump_python(value, mode="json")

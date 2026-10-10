"""Settings-file adapter wiring schema introspection + YAML I/O + env detection together.

Scope (profile) awareness goes through the settings layout: every write —
including ``http``/``ui``, which are ordinary per-profile settings now — lands
in ``profiles.<target>``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import yaml
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from untaped.config_file import (
    mutate_config,
    read_config_dict,
    set_at_path,
    unset_at_path,
)
from untaped.config_schema import FieldDescriptor, find_descriptor, walk_settings
from untaped.deprecated_keys import (
    apply_move,
    key_mappings,
    migration_moves,
    old_spellings,
    profile_sections,
)
from untaped.errors import ConfigError, first_validation_error
from untaped.settings import (
    Settings,
    active_settings_layout,
    env_var_name,
    get_profile_settings_model,
    load_settings_section,
    model_sections,
    section_stabilities,
    validate_settings_section,
)
from untaped.settings_layout import ResolvedConfig
from untaped.stability import Stability, setting_mark
from untaped.yaml_roundtrip import KeyRename

SpellingRemoved = Callable[[str], None]
"""Called with each old spelling ``config set``/``unset`` removes (``section.key``)."""


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
        self._resolved: ResolvedConfig | None = None

    def descriptors(self) -> list[FieldDescriptor]:
        if self._descriptors is None:
            self._descriptors = walk_settings(self._profile_model(), include_collections=True)
        return self._descriptors

    def descriptor(self, key: str) -> FieldDescriptor:
        descriptors = self.descriptors()
        descriptor = find_descriptor(descriptors, key)
        if descriptor is None:
            valid = ", ".join(d.key for d in descriptors)
            raise ConfigError(f"unknown setting: {key!r}. Valid keys: {valid}", category="invalid")
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

    def env_var_for(self, descriptor: FieldDescriptor) -> str:
        return env_var_name(descriptor.path)

    def env_value_for(self, descriptor: FieldDescriptor) -> str | None:
        name = self.env_var_supplying(descriptor)
        return None if name is None else os.environ.get(name)

    def env_var_supplying(self, descriptor: FieldDescriptor) -> str | None:
        """The set ``UNTAPED_*`` variable for ``descriptor``: its own, else an old spelling's."""
        names = [self.env_var_for(descriptor)]
        model = self.section_model(descriptor.path[0])
        if model is not None:
            readable = key_mappings(model).readable
            names += [
                env_var_name((descriptor.path[0], *old.split(".")))
                for old in old_spellings(model, _relative(descriptor))
                if old in readable
            ]
        return next((name for name in names if name in os.environ), None)

    def _resolve(self) -> ResolvedConfig:
        """The layered profiles with the old keys they used (read once per repository)."""
        if self._resolved is None:
            try:
                self._resolved = active_settings_layout().resolve(
                    self.yaml_dict(), sections=self._section_models()
                )
            except ConfigError:
                self._resolved = ResolvedConfig({}, {}, {})
        return self._resolved

    def section_model(self, section: str) -> type[BaseModel] | None:
        """The settings model of ``section``, if it is one."""
        return self._section_models().get(section)

    def mark(self, descriptor: FieldDescriptor) -> Stability | None:
        """The effective stability mark of ``descriptor``: its own, else its plugin's."""
        return setting_mark(
            descriptor.key,
            sections=self._section_models(),
            section_stability=section_stabilities(),
        )

    def deprecated_source(self, descriptor: FieldDescriptor) -> str | None:
        """The old key or variable that supplied ``descriptor``'s value, if any.

        The environment wins, as it does for the value; otherwise only the
        profile whose layer won counts.
        """
        name = self.env_var_supplying(descriptor)
        if name is not None:
            return None if name == self.env_var_for(descriptor) else name
        section, key = descriptor.path[0], _relative(descriptor)
        model = self.section_model(section)
        if model is None or not key_mappings(model):
            return None
        resolved = self._resolve()
        profile = resolved.provenance.get(descriptor.path)
        for use in resolved.uses.get(profile or "", {}).get(section, ()):
            if use.kind == "renamed" and use.new == key:
                return f"{section}.{use.old}"
        return None

    def set_value(
        self,
        key: str,
        raw_value: str,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: SpellingRemoved | None = None,
    ) -> str:
        """Validate ``raw_value`` against the key's type, then persist.

        The raw string is validated against the leaf's annotation (lax mode:
        ``true``/``no`` for bools, numbers, literals, paths); string and
        secret fields keep the exact input. Only the key's own section is
        re-validated, so an unrelated invalid section never blocks a repair.
        Returns the resolved target profile name so callers can report where
        the write landed. ``dry_run`` validates the same way but writes nothing.
        """
        return self.update_value(
            key,
            lambda _target, _current: raw_value,
            profile=profile,
            dry_run=dry_run,
            on_spelling_removed=on_spelling_removed,
        )

    def update_value(
        self,
        key: str,
        update: Callable[[str, Any], str | None],
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: SpellingRemoved | None = None,
    ) -> str:
        """Read-modify-write ``key`` in the target profile under one config lock.

        ``update(target, current)`` receives the target profile's name and
        the key's raw value in that profile's own data (``None`` when unset),
        and returns the raw string to store (validated as in
        :meth:`set_value`) or ``None`` to remove the key. It runs inside the
        locked mutation, so a concurrent writer's change is never replaced
        from a stale read; raise from it to abort without writing. Returns the
        target profile name.

        A value stored under an old spelling of the key counts as the key's
        own; writing or removing the key removes every old spelling in the
        target profile, each reported to ``on_spelling_removed``.
        """
        descriptor = self.descriptor(key)
        resolved: str | None = None

        def _apply(data: dict[str, Any]) -> None:
            nonlocal resolved
            target_data, resolved = active_settings_layout().write_profile(data, profile)
            olds = self._old_paths(descriptor)
            current = next(
                (
                    value
                    for value in (_at_path(target_data, path) for path in [descriptor.path, *olds])
                    if value is not None
                ),
                None,
            )
            raw_value = update(resolved, current)
            if raw_value is None:
                unset_at_path(target_data, descriptor.path)
            else:
                set_at_path(target_data, descriptor.path, _coerce_value(key, descriptor, raw_value))
            _remove_spellings(target_data, olds, on_spelling_removed)
            try:
                self._validate_section(data, descriptor, profile=resolved)
            except ValidationError as exc:
                raise ConfigError(
                    f"invalid value for {key!r}: {first_validation_error(exc)}",
                    category="invalid",
                ) from exc

        _run(_apply, dry_run=dry_run)
        # ``_run`` always runs ``_apply``, which sets ``resolved`` from
        # ``write_profile`` (always a profile name).
        assert resolved is not None
        return resolved

    def unset_value(
        self,
        key: str,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        on_spelling_removed: SpellingRemoved | None = None,
    ) -> tuple[bool, str]:
        """Remove ``key``, and every old spelling of it, from the resolved write scope.

        Returns ``(removed, target)``; under ``dry_run`` ``removed`` says
        whether it would be removed and nothing is written. An explicit
        ``profile`` the layout cannot satisfy raises ``ConfigError``. Removing a key that
        simply isn't set in the resolved scope is a no-op
        (``removed=False``). Each old spelling removed is reported to
        ``on_spelling_removed``.
        """
        descriptor = self.descriptor(key)
        removed = False
        resolved: str | None = None

        def _apply(data: dict[str, Any]) -> None:
            nonlocal removed, resolved
            target_data, resolved = active_settings_layout().write_profile(data, profile)
            own = unset_at_path(target_data, descriptor.path)
            olds = _remove_spellings(target_data, self._old_paths(descriptor), on_spelling_removed)
            if not (own or olds):
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
                    f"{first_validation_error(exc)}",
                    category="invalid",
                ) from exc

        _run(_apply, dry_run=dry_run)
        # ``write_profile`` (run inside ``_apply``, before the early return) always
        # sets ``resolved`` to a profile name.
        assert resolved is not None
        return removed, resolved

    def migrate_keys(self, *, dry_run: bool = False) -> list[dict[str, str]]:
        """Rename every renamed or retired key in every profile, under one lock.

        Returns one row per change (``profile``, ``from``, ``to``, ``action``:
        ``renamed``, ``dropped`` or ``deleted``, and ``detail``: for a delete,
        the value the key held and why it went); ``dry_run`` writes nothing.
        Deprecated settings and ``state.yml`` are left alone.
        """
        rows: list[dict[str, str]] = []
        renames: list[KeyRename] = []

        def _apply(data: dict[str, Any]) -> None:
            for name, section, model, section_data in profile_sections(
                data, self._section_models()
            ):
                for move in migration_moves(model, section_data):
                    if not apply_move(section_data, move):
                        continue
                    deleted = move.action == "deleted"
                    rows.append(
                        {
                            "profile": name,
                            "from": f"{section}.{move.old}",
                            "to": "" if deleted else f"{section}.{move.to}",
                            "action": move.action,
                            "detail": f"was {_shown(move.value)}; {move.note}" if deleted else "",
                        }
                    )
                    if move.action == "renamed":
                        base = ("profiles", name, section)
                        renames.append(
                            ((*base, *move.old.split(".")), (*base, *move.to.split(".")))
                        )

        if dry_run:
            _apply(read_config_dict())
        else:
            mutate_config(_apply, renames=renames)
        return rows

    def _section_models(self) -> dict[str, type[BaseModel]]:
        return model_sections(self._profile_model())

    def _old_paths(self, descriptor: FieldDescriptor) -> list[tuple[str, ...]]:
        """Absolute paths of every old spelling of ``descriptor``, closest first."""
        section = descriptor.path[0]
        model = self.section_model(section)
        if model is None:
            return []
        return [(section, *old.split(".")) for old in old_spellings(model, _relative(descriptor))]

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
        validate_settings_section(
            effective,
            descriptor.path[0],
            self._profile_model(),
            written_key=".".join(descriptor.path),
        )


def _relative(descriptor: FieldDescriptor) -> str:
    """``descriptor``'s key within its section (``sweep.parallel``)."""
    return ".".join(descriptor.path[1:])


def _at_path(data: Any, path: tuple[str, ...]) -> Any:
    for segment in path:
        data = data.get(segment) if isinstance(data, dict) else None
    return data


def _remove_spellings(
    data: dict[str, Any], paths: list[tuple[str, ...]], report: SpellingRemoved | None
) -> bool:
    """Remove every path in ``paths`` present in ``data``; ``True`` when any was."""
    removed = False
    for path in paths:
        if unset_at_path(data, path):
            removed = True
            if report is not None:
                report(".".join(path))
    return removed


def _run(apply: Callable[[dict[str, Any]], None], *, dry_run: bool) -> None:
    """Apply a config mutation, or run it on an in-memory copy for a dry run."""
    if dry_run:
        apply(read_config_dict())
    else:
        mutate_config(apply)


def _coerce_value(key: str, descriptor: FieldDescriptor, raw_value: str) -> Any:
    """Validate a CLI-supplied string against the leaf type; return its YAML form.

    ``str``/``SecretStr`` fields keep the input verbatim (no YAML parsing, so
    ``p4ss #word`` or ``0123`` survive intact). Mapping and list fields parse
    the input as JSON or YAML and replace the whole value. Other types
    validate the raw string in pydantic's lax mode and store the JSON-mode
    dump (e.g. a ``Path`` as a string). For those non-string types the
    literal ``null`` stores ``None``; the section validation in ``set_value``
    rejects it when the field is not optional. (``config unset`` removes a
    key instead.)
    """
    if descriptor.annotation in (str, SecretStr):
        return raw_value
    if raw_value == "null":
        return None
    adapter: TypeAdapter[Any] = TypeAdapter(descriptor.annotation)
    try:
        if descriptor.is_collection:
            value = adapter.validate_python(_parse_structured(key, raw_value))
        else:
            value = adapter.validate_strings(raw_value)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid value for {key!r}: {first_validation_error(exc)}", category="invalid"
        ) from exc
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return adapter.dump_python(value, mode="json")


def _parse_structured(key: str, raw_value: str) -> Any:
    """Parse a mapping/list value given as JSON or YAML (JSON is valid YAML)."""
    try:
        return yaml.safe_load(raw_value)
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or "not valid JSON or YAML"
        raise ConfigError(f"invalid value for {key!r}: {problem}", category="invalid") from exc


def _shown(value: Any) -> str:
    """A deleted key's value as ``config migrate`` prints it: the YAML scalar, or its shape."""
    if isinstance(value, dict):
        return "<mapping>"
    if isinstance(value, list):
        return "<list>"
    return yaml.safe_dump(value, default_flow_style=True).removesuffix("\n...\n").strip()

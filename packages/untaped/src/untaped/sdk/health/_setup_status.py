"""What a plugin still needs set up, read from its setting marks.

One reading of "set" for every front (``setup``, ``setup plan``, doctor's
``setup`` row, the online checks): the value core's settings resolution
returns for this run is not ``None`` and, for a string, not blank (``[]``
counts as set). A declared :class:`~untaped.sdk.network.credentials.Credential` is set
when :meth:`~untaped.sdk.network.credentials.Credential.answer` names a source for the
run's key; nothing is read or run to find out. Its conventional variables
(``env=``) are ambient, not tied to a profile: they count once every other
marked field is set, so at once for a plugin whose only mark is the secret.

:func:`setup_result` is core's doctor ``setup`` row for a plugin that marks
its fields or declares a credential.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, SecretStr

from untaped.sdk.health.marks import MarkedField, SetupMarks, is_set, setup_marks
from untaped.sdk.network.credentials import (
    CredentialKey,
    credential_key,
    describe_key,
    override_variable,
)

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin
    from untaped.sdk.health.doctor import DoctorResult

#: The id and title of core's doctor row for a plugin that marks what it needs.
SETUP_CHECK = "setup"


def plugin_marks(plugin: ComposedPlugin) -> SetupMarks | None:
    """What ``plugin`` needs set up (its marks and credentials); else ``None``."""
    return setup_marks(plugin.settings, plugin.credentials)


def field_value(settings: BaseModel, field: MarkedField) -> object:
    """The resolved value of ``field`` in ``settings`` (``None`` where a level is missing)."""
    value: object = settings
    for part in field.path:
        value = getattr(value, part, None)
    return value


def unset_fields(settings: BaseModel, marks: SetupMarks) -> tuple[MarkedField, ...]:
    """The fields ``marks`` requires that ``settings`` (validated, composed) leaves unset."""
    required = marks.required
    own = {field: _is_set(settings, field, ambient=False) for field in required}
    missing: list[MarkedField] = []
    for field in required:
        others_set = all(own[other] for other in required if other is not field)
        ambient = field.is_secret and others_set
        if not own[field] and not (ambient and _is_set(settings, field, ambient=True)):
            missing.append(field)
    return tuple(missing)


def _is_set(settings: BaseModel, field: MarkedField, *, ambient: bool) -> bool:
    if not field.is_secret:
        return is_set(field_value(settings, field))
    return secret_source(settings, field, ambient=ambient) is not None


def secret_source(settings: BaseModel, field: MarkedField, *, ambient: bool = True) -> str | None:
    """Where secret ``field`` comes from (``stored 9b07e4``, ``GH_TOKEN``), if anywhere.

    ``ambient=False`` leaves out its conventional variables. A credential
    whose key is unset has none. Nothing is read or run.
    """
    credential = field.credential
    assert credential is not None, f"{field.key} is not a secret"
    key = credential_key(credential, settings)
    if key is None:
        # No server yet, so no stored login; a variable still counts.
        return override_variable(credential.plugin, credential.name)
    answer = credential.answer(key)
    if answer is None or (answer.step == "env" and not ambient):
        return None
    return answer.source


def set_command(field: MarkedField, marks: SetupMarks, *, section: str) -> list[str]:
    """The command (after ``untaped``) that sets ``field``; a ``<NAME>`` is the user's value.

    ``auth login <section>`` for a credential (``auth login <section>
    <name>`` for one not named ``token``), ``config set`` for any other field.
    """
    del marks
    credential = field.credential
    if credential is not None:
        tail = [] if credential.name == "token" else [credential.name]
        return ["auth", "login", section, *tail]
    if field.key_model is not None:
        leaf = next(
            (name for name, each in field.key_model.model_fields.items() if each.is_required()),
            None,
        )
        if leaf is not None:
            return ["config", "set", f"{section}.{field.key}.{leaf}", f"<{leaf.upper()}>"]
    return ["config", "set", f"{section}.{field.key}", field.value_name]


def setup_result(settings: BaseModel | None, marks: SetupMarks, *, section: str) -> DoctorResult:
    """Core's doctor ``setup`` row for a plugin with marks.

    Nothing set passes as "not set up" (an unused plugin never fails doctor);
    some set warns, naming what is missing, with that field's command or
    ``setup plan --only <section>`` as the fix; all set passes.
    """
    from untaped.sdk.health.doctor import DoctorResult  # noqa: PLC0415 - doctor imports this

    if settings is None:
        return DoctorResult(id=SETUP_CHECK, ok=True, detail="skipped: settings are invalid")
    required = marks.required
    missing = unset_fields(settings, marks)
    if required and len(missing) == len(required):
        return DoctorResult(id=SETUP_CHECK, ok=True, detail="not set up")
    if missing:
        names = ", ".join(f"{section}.{field.key}" for field in missing)
        detail = f"{len(required) - len(missing)} of {len(required)} set; missing {names}"
        fix = (
            set_command(missing[0], marks, section=section)
            if len(missing) == 1
            else ["setup", "plan", "--only", section]
        )
        return DoctorResult(id=SETUP_CHECK, ok=True, warn=True, detail=detail, fix=fix)
    detail = summary(settings, marks)
    return DoctorResult(id=SETUP_CHECK, ok=True, detail=detail or "set up")


def summary(settings: BaseModel, marks: SetupMarks) -> str:
    """The marked fields as a pass row shows them: each value, then where each secret comes from.

    ``https://aap.example.com; token from stored 9b07e4``. A secret
    setting without a ``Secret`` mark shows only that it is set.
    """
    parts: list[str] = []
    for field in marks.required:
        if field.is_secret:
            source = secret_source(settings, field)
            parts.append(f"{field.key} from {source}")
            continue
        parts.append(shown_value(settings, field))
    return "; ".join(parts)


def shown_value(settings: BaseModel, field: MarkedField) -> str:
    """A set marked field as a row shows it: a key described, a secret setting only as set."""
    value = field_value(settings, field)
    if isinstance(value, SecretStr) or field.masked:
        return f"{field.key} set"
    if isinstance(value, CredentialKey):
        return describe_key(value)
    return str(value)


__all__ = [
    "SETUP_CHECK",
    "field_value",
    "plugin_marks",
    "secret_source",
    "set_command",
    "setup_result",
    "summary",
    "unset_fields",
]

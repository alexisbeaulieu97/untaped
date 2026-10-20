"""What a plugin still needs set up, read from its setting marks.

One reading of "set" for every front (``setup``, ``setup plan``, doctor's
``setup`` row, the online checks): the value core's settings resolution
returns for this run is not ``None`` and, for a string, not blank (``[]``
counts as set). A :class:`~untaped.setting_marks.Secret` is set when
:func:`~untaped.auth.describe_token_source` names a source; its command is
never run to find out. Its conventional variables (``Secret(env=...)``) are
ambient, not tied to a profile: they count once every other marked field is
set, so at once for a plugin whose only mark is the secret.

:func:`setup_result` is core's doctor ``setup`` row for a plugin that marks
its fields. A plugin read through the legacy fallback keeps its own
``connection_check`` row instead, and :func:`notify_unmarked` warns, once
per plugin per run, wherever that fallback is read.
"""

from __future__ import annotations

from pydantic import BaseModel, SecretStr

from untaped.auth import PLAINTEXT_TOKEN, describe_token_source, plaintext_subject
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError
from untaped.plugins.registry import DoctorResult, PluginSpec
from untaped.setting_marks import (
    UNMARKED_SERVICE,
    MarkedField,
    SetupMarks,
    is_set,
    setup_marks,
    unmarked_subject,
)
from untaped.settings import active_settings_layout, resolve_config_path

#: The id and title of core's doctor row for a plugin that marks what it needs.
SETUP_CHECK = "setup"


def plugin_marks(spec: PluginSpec) -> SetupMarks | None:
    """What ``spec``'s settings need set up (marks or the legacy fallback); ``None`` for none."""
    return None if spec.settings is None else setup_marks(spec.settings)


def notify_unmarked(plugin: str, marks: SetupMarks | None) -> None:
    """Warn, once per plugin per run, that ``plugin`` is set up through the legacy fallback.

    A no-op for ``marks`` of a plugin that marks its fields, or ``None``.
    """
    if marks is not None and marks.legacy:
        UNMARKED_SERVICE.notify(unmarked_subject(plugin), key=plugin)


def field_value(settings: BaseModel, field: MarkedField) -> object:
    """The resolved value of ``field`` in ``settings`` (``None`` where a level is missing)."""
    value: object = settings
    for part in field.path:
        value = getattr(value, part, None)
    return value


def unset_fields(
    settings: BaseModel, marks: SetupMarks, *, section: str
) -> tuple[MarkedField, ...]:
    """The fields ``marks`` requires that ``settings`` (validated, composed) leaves unset."""
    required = marks.required
    own = {field: _is_set(settings, field, section=section, ambient=False) for field in required}
    missing: list[MarkedField] = []
    for field in required:
        others_set = all(own[other] for other in required if other is not field)
        ambient = field.secret is not None and others_set
        if not own[field] and not (
            ambient and _is_set(settings, field, section=section, ambient=True)
        ):
            missing.append(field)
    return tuple(missing)


def _is_set(settings: BaseModel, field: MarkedField, *, section: str, ambient: bool) -> bool:
    if field.secret is None:
        return is_set(field_value(settings, field))
    source = describe_token_source(settings, section=section, ambient=ambient, field=field.key)
    return source is not None


def secret_source(settings: BaseModel, field: MarkedField, *, section: str) -> str | None:
    """Where secret ``field`` comes from (``awx.token_command``, ``$GH_TOKEN``), if anywhere."""
    return describe_token_source(settings, section=section, field=field.key)


def set_command(field: MarkedField, *, section: str) -> list[str]:
    """The command (after ``untaped``) that sets ``field``; a ``<NAME>`` is the user's value.

    ``auth set`` for the secret named ``token``; another secret's command
    setting; ``config set`` for any other field.
    """
    if field.secret is not None:
        if field.path == ("token",):
            return ["auth", "set", section]
        return ["config", "set", f"{section}.{field.command}", "<COMMAND>"]
    return ["config", "set", f"{section}.{field.key}", field.value_name]


def setup_result(settings: BaseModel | None, marks: SetupMarks, *, section: str) -> DoctorResult:
    """Core's doctor ``setup`` row for a plugin with marks.

    Nothing set passes as "not set up" (an unused plugin never fails doctor);
    some set warns, naming what is missing, with that field's command or
    ``setup plan --only <section>`` as the fix; all set passes, or warns
    with the automatic ``auth migrate`` when the ``token`` is plain text in
    the config file.
    """
    if settings is None:
        return DoctorResult(id=SETUP_CHECK, ok=True, detail="skipped: settings are invalid")
    required = marks.required
    missing = unset_fields(settings, marks, section=section)
    if required and len(missing) == len(required):
        return DoctorResult(id=SETUP_CHECK, ok=True, detail="not set up")
    if missing:
        names = ", ".join(f"{section}.{field.key}" for field in missing)
        detail = f"{len(required) - len(missing)} of {len(required)} set; missing {names}"
        fix = (
            set_command(missing[0], section=section)
            if len(missing) == 1
            else ["setup", "plan", "--only", section]
        )
        return DoctorResult(id=SETUP_CHECK, ok=True, warn=True, detail=detail, fix=fix)
    detail = summary(settings, marks, section=section)
    if marks.token is not None and stored_plaintext(section):
        subject = plaintext_subject(section, resolve_config_path().name)
        # The row's fix line names `auth migrate`, so the sentence names no replacement.
        sentence = PLAINTEXT_TOKEN.sentence(subject, replacement=None)
        return DoctorResult(
            id=SETUP_CHECK,
            ok=True,
            warn=True,
            detail=f"{detail}; {sentence}" if detail else sentence,
            fix="auth migrate",
            automatic=True,
        )
    return DoctorResult(id=SETUP_CHECK, ok=True, detail=detail or "set up")


def summary(settings: BaseModel, marks: SetupMarks, *, section: str) -> str:
    """The marked fields as a pass row shows them: each value, then where each secret comes from.

    ``https://aap.example.com; token from awx.token_command``. A secret
    setting without a ``Secret`` mark shows only that it is set.
    """
    parts: list[str] = []
    for field in marks.required:
        if field.secret is not None:
            source = secret_source(settings, field, section=section)
            parts.append(f"{field.key} from {source}")
            continue
        value = field_value(settings, field)
        parts.append(f"{field.key} set" if isinstance(value, SecretStr) else str(value))
    return "; ".join(parts)


def stored_plaintext(section: str, *, field: str = "token") -> bool:
    """Whether the config file itself sets ``<section>.<field>`` for the resolved profile.

    Read from the file, not the validated settings: an ``UNTAPED_*`` override
    neither hides a token stored in the file nor counts as one.
    """
    try:
        node = active_settings_layout().effective(read_config_dict()).get(section)
    except ConfigError:
        return False
    token = node.get(field) if isinstance(node, dict) else None
    return isinstance(token, str) and bool(token.strip())


__all__ = [
    "SETUP_CHECK",
    "field_value",
    "notify_unmarked",
    "plugin_marks",
    "secret_source",
    "set_command",
    "setup_result",
    "stored_plaintext",
    "summary",
    "unset_fields",
]

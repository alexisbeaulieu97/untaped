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
its fields. A plugin read through the legacy fallback keeps its own
``connection_check`` row instead, and :func:`notify_unmarked` warns, once
per plugin per run, wherever that fallback is read.
"""

from __future__ import annotations

from pydantic import BaseModel, SecretStr

from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import PluginSpec
from untaped.runtime.settings import active_settings_layout, resolve_config_path
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.doctor import DoctorResult
from untaped.sdk.health.marks import (
    UNMARKED_SERVICE,
    MarkedField,
    SetupMarks,
    is_set,
    section_marks,
    setup_marks,
    unmarked_subject,
)
from untaped.sdk.network.auth import PLAINTEXT_TOKEN, describe_token_source, plaintext_subject
from untaped.sdk.network.credentials import (
    CredentialKey,
    credential_key,
    describe_key,
    legacy_setting,
    override_variable,
)

#: The id and title of core's doctor row for a plugin that marks what it needs.
SETUP_CHECK = "setup"


def plugin_marks(spec: PluginSpec) -> SetupMarks | None:
    """What ``spec`` needs set up (marks, credentials, or the legacy fallback); else ``None``."""
    if spec.settings is None:
        return section_marks(spec.name, None) if spec.credentials else None
    return setup_marks(spec.settings, spec.credentials)


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
        ambient = field.is_secret and others_set
        if not own[field] and not (
            ambient and _is_set(settings, field, section=section, ambient=True)
        ):
            missing.append(field)
    return tuple(missing)


def _is_set(settings: BaseModel, field: MarkedField, *, section: str, ambient: bool) -> bool:
    if not field.is_secret:
        return is_set(field_value(settings, field))
    return secret_source(settings, field, section=section, ambient=ambient) is not None


def secret_source(
    settings: BaseModel, field: MarkedField, *, section: str, ambient: bool = True
) -> str | None:
    """Where secret ``field`` comes from (``stored 9b07e4``, ``GH_TOKEN``), if anywhere.

    ``ambient=False`` leaves out its conventional variables. A credential
    whose key is unset has none. Nothing is read or run.
    """
    credential = field.credential
    if credential is None:
        return describe_token_source(settings, section=section, ambient=ambient)
    key = credential_key(credential, settings)
    if key is None:
        # No server yet, so no stored login; a variable or an old setting still counts.
        variable = override_variable(credential.plugin, credential.name)
        legacy = None if variable is not None else legacy_setting(credential)
        return variable or (None if legacy is None else legacy[0])
    answer = credential.answer(key)
    if answer is None or (answer.step == "env" and not ambient):
        return None
    return answer.source


def set_command(field: MarkedField, marks: SetupMarks, *, section: str) -> list[str]:
    """The command (after ``untaped``) that sets ``field``; a ``<NAME>`` is the user's value.

    ``auth login <section>`` for a credential (``auth login <section>
    <name>`` for one not named ``token``), ``auth set <section>`` for the
    legacy fallback's ``token``, ``config set`` for any other field.
    """
    del marks
    credential = field.credential
    if credential is not None:
        tail = [] if credential.name == "token" else [credential.name]
        return ["auth", "login", section, *tail]
    if field.is_secret:
        return ["auth", "set", section]
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
    ``setup plan --only <section>`` as the fix; all set passes, or warns
    with the automatic ``auth migrate`` when a legacy token is plain text in
    the config file, or with ``config migrate`` when a credential still
    comes from its deprecated setting.
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
            set_command(missing[0], marks, section=section)
            if len(missing) == 1
            else ["setup", "plan", "--only", section]
        )
        return DoctorResult(id=SETUP_CHECK, ok=True, warn=True, detail=detail, fix=fix)
    detail = summary(settings, marks, section=section)
    plain = next(
        (
            field
            for field in marks.secrets
            if field.credential is None and stored_plaintext(section, field=field.key)
        ),
        None,
    )
    if plain is not None:
        subject = plaintext_subject(section, resolve_config_path().name, field=plain.key)
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
    old = next(
        (
            source
            for field in marks.secrets
            if field.credential is not None
            and (source := secret_source(settings, field, section=section)) is not None
            and source.startswith(f"{section}.")
        ),
        None,
    )
    if old is not None:
        return DoctorResult(
            id=SETUP_CHECK,
            ok=True,
            warn=True,
            detail=f"{detail}; `config migrate` moves {old} to a stored login",
            fix="config migrate",
        )
    return DoctorResult(id=SETUP_CHECK, ok=True, detail=detail or "set up")


def summary(settings: BaseModel, marks: SetupMarks, *, section: str) -> str:
    """The marked fields as a pass row shows them: each value, then where each secret comes from.

    ``https://aap.example.com; token from awx.token_command``. A secret
    setting without a ``Secret`` mark shows only that it is set.
    """
    parts: list[str] = []
    for field in marks.required:
        if field.is_secret:
            source = secret_source(settings, field, section=section)
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

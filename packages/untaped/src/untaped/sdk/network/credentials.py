"""Credentials: secrets a plugin declares, which core keeps outside configuration.

A plugin declares what a secret is for as a :class:`CredentialKey` (for awx:
the server's URL and how its certificate is checked) and the secret itself as
a :class:`Credential` listed in ``PluginSpec(credentials=)``. Core files each
credential under the key it is for and answers :meth:`Credential.get` from,
in order:

1. ``UNTAPED_<PLUGIN>__<NAME>`` (or ``<name>`` in an ``UNTAPED_<PLUGIN>`` JSON
   object), today's override spelling;
2. the credential stored with ``untaped auth login`` for exactly this key;
3. in 11.x only, the old ``<plugin>.<name>`` or ``<plugin>.<name>_command``
   setting (:func:`untaped.runtime.settings.legacy_credential_fields` marks them);
4. the plugin's own variables (``env=``), in order.

A key a folder file chose, by any of its fields, gets nothing from steps 1, 3
and 4: a repository can't point a token from your shell or an old config line
at a server of its choosing. A stored credential answers only its exact key,
so a changed URL or TLS setting misses it.

The secret comes back as a :class:`BoundSecret`, bound to the origin the key
names (its one URL field, the one typed :data:`TokenUrl`, or an ``origin``
class variable): ``connected_client`` sends it nowhere else.

No field name reaches core: a key is read by its fields' types.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import typing
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, cast

from pydantic import (
    AnyUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    HttpUrl,
    SecretStr,
    ValidationError,
)
from pydantic.fields import FieldInfo

from untaped.sdk._block import OwnedBlock
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.errors import ConfigError

if TYPE_CHECKING:
    from untaped.sdk.health.marks import Suggest
    from untaped.sdk.network._credential_store import CredentialRow


# --------------------------------------------------------------------------- #
# Field types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _TlsVerifyMark:
    """The :data:`TlsVerify` type's marker, found by type, never by name."""


@dataclass(frozen=True)
class _TokenUrlMark:
    """The :data:`TokenUrl` type's marker: this URL field is the token's origin."""


_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


def _tls_value(value: object) -> object:
    """``true``/``false`` (as written in YAML or a variable) or a CA file path, ``~`` expanded."""
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _TRUE:
            return True
        if text.lower() in _FALSE:
            return False
        if not text:
            raise ValueError("verify_tls takes true, false, or the path of a CA file")
        return Path(text).expanduser()
    if isinstance(value, Path):
        return value.expanduser()
    return value


TlsVerify = Annotated[bool | Path, _TlsVerifyMark(), BeforeValidator(_tls_value)]
"""How a server's certificate is checked: ``true`` (untaped's trust, root
``untaped.http.ca_bundle_path`` or the OS store), ``false`` (not at all), or a CA file
that replaces it for this server. A relative path is read from the config
file's folder."""

TokenUrl = Annotated[HttpUrl, _TokenUrlMark()]
"""A key's URL field that is the token's origin, for a key with several URL fields."""


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


class CredentialKey(BaseModel):
    """What a credential is for: everything that decides who can read the request.

    A plugin subclasses it with the fields that decide where its secret may
    be sent, such as the server's ``url: HttpUrl`` and ``verify_tls:
    TlsVerify``. Core reads a key as data: it hashes it, shows it, and finds
    its URL and :data:`TlsVerify` fields by type. A key with no URL field
    names its fixed endpoint with ``origin: ClassVar[HttpUrl]``; one with
    neither is not for HTTP (a vault password), and its secret is unbound.
    Change a field's default and every stored login of that key is lost:
    the public surface records the defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: ClassVar[HttpUrl | None] = None


@dataclass(frozen=True)
class KeyShape:
    """What core reads off a key class, by type."""

    urls: tuple[str, ...]
    """The URL fields, in declaration order."""
    token_url: str | None
    """The field the token's origin comes from, when one is a URL."""
    tls: str | None
    """The :data:`TlsVerify` field, if any."""


def _is_url(annotation: object) -> bool:
    inner = unwrap_optional(annotation)
    return isinstance(inner, type) and issubclass(inner, AnyUrl)


def _has_mark(field: FieldInfo, mark: type) -> bool:
    return any(isinstance(item, mark) for item in field.metadata)


@cache
def key_shape(model: type[CredentialKey]) -> KeyShape:
    """The URL fields, origin field and TLS field of ``model``, found by type."""
    fields = model.model_fields
    urls = tuple(name for name, field in fields.items() if _is_url(field.annotation))
    marked = tuple(name for name in urls if _has_mark(fields[name], _TokenUrlMark))
    token_url = marked[0] if marked else urls[0] if len(urls) == 1 else None
    tls = next((name for name, field in fields.items() if _has_mark(field, _TlsVerifyMark)), None)
    return KeyShape(urls=urls, token_url=token_url, tls=tls)


def key_errors(model: object) -> list[str]:
    """One sentence per way ``model`` is not a usable credential key; ``[]`` when it is."""
    if not (isinstance(model, type) and issubclass(model, CredentialKey)):
        return [f"key={model!r} is not a CredentialKey subclass"]
    errors: list[str] = []
    name = model.__name__
    for field_name, field in model.model_fields.items():
        if _contains_secret(field.annotation):
            errors.append(
                f"{name}.{field_name} is a SecretStr; a key is shown and hashed, so it holds "
                "no secret"
            )
    shape = key_shape(model)
    marked = [each for each in shape.urls if _has_mark(model.model_fields[each], _TokenUrlMark)]
    if len(marked) > 1:
        errors.append(f"{name} types several fields TokenUrl ({', '.join(marked)}); keep one")
    if len(shape.urls) > 1 and not marked:
        errors.append(
            f"{name} has several URL fields ({', '.join(shape.urls)}); type the token's "
            "origin TokenUrl"
        )
    if model.origin is not None:
        if shape.urls:
            errors.append(f"{name} has URL fields and an origin; keep one")
        elif not isinstance(model.origin, HttpUrl) or model.origin.scheme != "https":
            errors.append(f"{name}.origin must be an https HttpUrl, not {model.origin!r}")
    return errors


def _contains_secret(annotation: object) -> bool:
    if annotation is SecretStr or (
        isinstance(annotation, type) and issubclass(annotation, SecretStr)
    ):
        return True
    return any(_contains_secret(arg) for arg in typing.get_args(annotation))


def _normal(value: object) -> object:
    """A key field's value as canonical JSON data: URLs and paths in one spelling."""
    if isinstance(value, AnyUrl):
        return str(value).rstrip("/")
    if isinstance(value, Path):
        return str(_absolute(value))
    if isinstance(value, BaseModel):
        return {name: _normal(each) for name, each in value}
    if isinstance(value, list | tuple):
        return [_normal(each) for each in value]
    return value


def _absolute(path: Path) -> Path:
    """``path`` with ``~`` expanded and, when relative, read from the config file's folder."""
    path = path.expanduser()
    if path.is_absolute():
        return path
    from untaped.sdk.network.auth import command_folder  # noqa: PLC0415 - auth imports settings

    return command_folder() / path


def key_data(key: CredentialKey) -> dict[str, object]:
    """``key``'s fields that differ from their default, normalised by type.

    Leaving defaults out keeps every stored login when a plugin adds a field
    with a default.
    """
    data: dict[str, object] = {}
    for name, field in type(key).model_fields.items():
        value = getattr(key, name)
        if not field.is_required() and value == field.get_default(call_default_factory=True):
            continue
        data[name] = _normal(value)
    return data


def key_id(plugin: str, name: str, key: CredentialKey) -> str:
    """The sha256 of canonical JSON of ``{plugin, name, key}``: the stored row's id."""
    text = json.dumps(
        {"plugin": plugin, "name": name, "key": key_data(key)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(text.encode()).hexdigest()


SHORT_ID = 6
"""How many hex characters of an id ``auth list`` shows and ``auth forget --id`` takes."""


def describe_key(key: CredentialKey) -> str:
    """``url=https://aap.example.com verify_tls=~/certs/ca.pem``: a key as messages show it.

    Fields at their default are left out, except a URL (github.com's default
    server is still a server); a key with none shows ``(any)``.
    """
    data = key_data(key)
    urls = key_shape(type(key)).urls
    parts = [
        f"{name}={_shown(data[name] if name in data else _normal(getattr(key, name)))}"
        for name in type(key).model_fields
        if name in data or (name in urls and getattr(key, name) is not None)
    ]
    return " ".join(parts) or "(any)"


def _shown(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        home = str(Path.home())
        return "~" + value[len(home) :] if value.startswith(home + os.sep) else value
    return json.dumps(value, sort_keys=True)


def key_origin(key: CredentialKey) -> str | None:
    """The origin a credential for ``key`` is bound to, or ``None`` for a non-HTTP key."""
    shape = key_shape(type(key))
    if shape.token_url is not None:
        url = getattr(key, shape.token_url)
        return None if url is None else str(url)
    origin = type(key).origin
    return None if origin is None else str(origin)


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #

_MASK = "**********"


class BoundSecret(SecretStr):
    """A secret bound to the origin of the key it was filed under.

    ``origin`` is ``scheme://host[:port]`` as the key gives it, or ``None``
    for a secret that is not for HTTP (``connected_client`` refuses that one
    as a bearer). ``source`` names where it came from (``stored``, a
    variable, an old setting), never the value. A secret read through a
    command runs it on first read, once per process.
    """

    def __init__(
        self,
        value: str | Callable[[], str],
        *,
        origin: str | None,
        source: str,
    ) -> None:
        super().__init__(value if isinstance(value, str) else _MASK)
        self._read = None if isinstance(value, str) else value
        self.origin = origin
        self.source = source

    def get_secret_value(self) -> str:
        """The secret; one read through a command runs it now, then keeps the result."""
        if self._read is not None:
            object.__setattr__(self, "_secret_value", self._read())
            self._read = None
        return super().get_secret_value()


class NoCredential(ConfigError):
    """No credential answers a key, or every one that would is refused (exit 4).

    Its hint is the person's step (``fix_by="user"``): ``untaped auth login``,
    or a variable they set.
    """

    fix_by = "user"


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #

_NAME = re.compile(r"^[a-z][a-z0-9]*([_-][a-z0-9]+)*$")

Step = Literal["variable", "stored", "setting", "env"]
"""Which of :meth:`Credential.get`'s steps answered."""


@dataclass(frozen=True)
class Answer:
    """Which source answers a credential for one key, named without reading it."""

    step: Step
    source: str
    """``$UNTAPED_AWX__TOKEN``, ``stored 9b07e4``, ``awx.token_command``, ``$GH_TOKEN``."""
    row: str | None = None
    """The stored row's id, for the ``stored`` step."""


_ENV_NAMES: set[str] = set()


def credential_env_names() -> frozenset[str]:
    """Every variable a credential declared so far reads (``untaped.sdk.testing`` clears them)."""
    return frozenset(_ENV_NAMES)


class Credential[K: CredentialKey](OwnedBlock):
    """A secret a plugin needs, filed under the :class:`CredentialKey` it is for.

    A plugin class holding it names it: ``token = Credential(key=AwxServer,
    env=("CONTROLLER_OAUTH_TOKEN",))`` is the plugin's credential ``token``.
    The 11.x spelling passes the plugin and name first and is listed in
    ``PluginSpec(credentials=)``: ``TOKEN = Credential("awx", "token",
    key=AwxServer, env=("CONTROLLER_OAUTH_TOKEN",))``. ``env`` names the
    variables tried last, in order; ``suggest`` is a command that prints the
    secret, which ``setup plan`` offers when its program is on ``PATH``.

    :meth:`get` returns the secret for a key or raises :class:`NoCredential`;
    :meth:`store` files one (what ``untaped auth login`` runs); :meth:`forget`
    deletes the stored one. Another plugin that needs this secret goes
    through the owning plugin's public interface (its clients), by
    convention.
    """

    block_kind = "credential"

    key: type[K]
    env: tuple[str, ...]
    suggest: Suggest | None

    def __init__(
        self,
        plugin: str | None = None,
        name: str | None = None,
        *,
        key: type[K],
        env: Sequence[str] = (),
        suggest: Suggest | None = None,
    ) -> None:
        from untaped.sdk.health.marks import Suggest  # noqa: PLC0415 - marks import settings

        if plugin is not None and (not isinstance(plugin, str) or not _NAME.match(plugin)):
            raise TypeError(f"Credential plugin must be a plugin name, not {plugin!r}")
        if name is not None and (problem := self.name_problem(name)) is not None:
            raise TypeError(problem)
        super().__init__(plugin, name)
        env = tuple(env)
        if not all(isinstance(each, str) and each.strip() for each in env):
            raise TypeError(f"Credential(env=...) takes variable names, not {env!r}")
        if suggest is not None and not isinstance(suggest, Suggest):
            raise TypeError(f"Credential(suggest=...) takes a Suggest, not {suggest!r}")
        self.key = key
        self.env = env
        self.suggest = suggest
        _ENV_NAMES.update(env)

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        if isinstance(name, str) and _NAME.match(name):
            return None
        return f"Credential name must be lower case letters, digits, '_' and '-', not {name!r}"

    @property
    def qualified(self) -> str:
        """``<plugin>.<name>``."""
        return f"{self.plugin}.{self.name}"

    @property
    def label(self) -> str:
        """How messages name it: ``awx token``."""
        return f"{self.plugin} {self.name.replace('_', ' ')}"

    def __repr__(self) -> str:
        if not self.spelled:
            return f"Credential(key={self.key.__name__})"
        return f"Credential({self.plugin!r}, {self.name!r}, key={self.key.__name__})"

    def id(self, key: K) -> str:
        """The id the stored credential for ``key`` is filed under."""
        return key_id(self.plugin, self.name, key)

    def answer(self, key: K) -> Answer | None:
        """Which step would answer :meth:`get` for ``key``, without reading anything.

        ``None`` when nothing would. A refused step (a folder chose the key)
        answers nothing.
        """
        from untaped.sdk.network._credential_store import find_row  # noqa: PLC0415

        refused = folder_chose(key)
        variable = override_variable(self.plugin, self.name)
        if variable is not None and not refused:
            return Answer("variable", f"${variable}")
        candidate = self._candidate()
        if candidate is not None:
            found = next((each for each in self.env if os.environ.get(each, "").strip()), None)
            if candidate[1] is not None:
                return Answer("setting", candidate[0])
            return None if found is None else Answer("env", f"${found}")
        row = find_row(self.id(key))
        if row is not None:
            return Answer("stored", f"stored {row.id[:SHORT_ID]}", row=row.id)
        if refused:
            return None
        legacy = legacy_setting(self)
        if legacy is not None:
            return Answer("setting", legacy[0])
        found = next((each for each in self.env if os.environ.get(each, "").strip()), None)
        return None if found is None else Answer("env", f"${found}")

    def get(self, key: K) -> BoundSecret:
        """The secret for ``key``, bound to its origin, or :class:`NoCredential` (exit 4).

        A command (stored, or an old ``<name>_command``) runs when the
        secret is first read, never here.
        """
        from untaped.sdk.network._credential_store import find_row, read_row  # noqa: PLC0415

        origin = key_origin(key)
        refused = folder_chose(key)
        variable = override_variable(self.plugin, self.name)
        if variable is not None:
            if refused:
                raise self._refusal(key, f"{variable} is set")
            return BoundSecret(
                _override_value(self.plugin, self.name), origin=origin, source=f"${variable}"
            )
        candidate = self._candidate()
        if candidate is not None and candidate[1] is not None:
            return BoundSecret(candidate[1], origin=origin, source=candidate[0])
        row = None if candidate is not None else find_row(self.id(key))
        if row is not None:
            return read_row(row, origin=origin)
        legacy = None if candidate is not None else legacy_setting(self)
        found = next((each for each in self.env if os.environ.get(each, "").strip()), None)
        if refused and (legacy is not None or found is not None):
            raise self._refusal(key, f"{legacy[0] if legacy else found} is set")
        if legacy is not None:
            setting, value = legacy
            return BoundSecret(value, origin=origin, source=setting)
        if found is not None:
            return BoundSecret(os.environ[found].strip(), origin=origin, source=f"${found}")
        raise self._missing(key)

    def _candidate(self) -> tuple[str, str | Callable[[], str] | None] | None:
        """What ``untaped setup`` is checking for this credential, before anything is stored.

        Its settings overlay (:func:`untaped.runtime.settings.settings_overlay`) names
        the candidate as the old ``<name>`` or ``<name>_command``; while it
        does, that answers instead of the stored login and the old settings
        (``(source, None)``: a variable is the candidate, so only ``env``
        answers). ``None`` outside a check.
        """
        from untaped.runtime.settings import active_overlay  # noqa: PLC0415
        from untaped.sdk.network.auth import read_command  # noqa: PLC0415

        overlay = active_overlay()
        if overlay is None or overlay.section != self.plugin:
            return None
        values = overlay.values
        command_key = f"{self.name}_command"
        if self.name not in values and command_key not in values:
            return None
        token = values.get(self.name)
        if isinstance(token, SecretStr):
            return "setup's candidate", token.get_secret_value().strip()
        argv = values.get(command_key)
        if isinstance(argv, list) and argv:
            command = [str(part) for part in argv]
            setting = f"setup's {self.label} command"
            return setting, lambda: read_command(command, setting=setting)
        return "setup's candidate", None

    def find(self, key: K | None) -> BoundSecret | None:
        """:meth:`get`, or ``None`` when nothing is set for ``key`` (or there is no key).

        For a client that also works without the secret (an unauthenticated
        ``ping``). A folder's refusal still raises :class:`NoCredential`.
        """
        if key is None or (self.answer(key) is None and not folder_chose(key)):
            return None
        return self.get(key)

    def store(
        self,
        key: K,
        *,
        command: Sequence[str] | None = None,
        token: str | None = None,
        backend: str | None = None,
    ) -> str:
        """File a credential for ``key`` and return where it went (never the secret).

        ``command`` stores an argv that prints the secret when it is needed;
        otherwise ``token`` goes to this machine's password store
        (``backend`` names one). Raises for a key a folder chose.
        """
        from untaped.sdk.network._credential_store import (  # noqa: PLC0415
            store_command,
            store_token,
        )

        refused = self.refusal(key, "filing a credential")
        if refused is not None:
            raise refused
        if command is not None:
            return store_command(self, key, command)
        if token is None:
            raise TypeError("Credential.store takes command= or token=")
        return store_token(self, key, token, backend=backend)

    def forget(self, key: K) -> CredentialRow | None:
        """Delete the stored credential for ``key``; its row, or ``None`` when none was stored."""
        from untaped.sdk.network._credential_store import forget_row  # noqa: PLC0415

        return forget_row(self.id(key))

    def refusal(self, key: K, what: str) -> NoCredential | None:
        """The error for ``what`` (``"filing a credential"``) when a folder chose ``key``."""
        return self._refusal(key, what) if folder_chose(key) else None

    def _missing(self, key: K) -> NoCredential:
        from untaped.sdk.words import command_line  # noqa: PLC0415

        login = command_line(
            f"auth login {self.plugin}" + ("" if self.name == "token" else f" {self.name}")
        )
        variable = f", or set {self.env[0]}" if self.env else ""
        return NoCredential(
            f"no {self.label} for {describe_key(key)}",
            hint=f"{login}{variable}",
            system=self.plugin,
            details={"credential": self.qualified, "key": key_data(key)},
        )

    def _refusal(self, key: K, what: str) -> NoCredential:
        sources = folder_sources(key)
        return NoCredential(
            f"{what}, but a folder's settings chose this {self.label}'s "
            f"{', '.join(sorted(sources))}; untaped never uses or files a credential for a "
            "key a folder chose",
            hint=f"run from outside that folder, or remove those settings from {_join(sources)}",
            system=self.plugin,
            details={"credential": self.qualified, "sources": dict(sorted(sources.items()))},
        )


def _join(sources: dict[str, str]) -> str:
    return " and ".join(sorted(set(sources.values())))


# --------------------------------------------------------------------------- #
# Where get looks
# --------------------------------------------------------------------------- #


def override_variable(plugin: str, name: str) -> str | None:
    """``UNTAPED_<PLUGIN>__<NAME>`` (or the ``UNTAPED_<PLUGIN>`` JSON naming it) when set.

    Matched without regard to case, as settings read variables.
    """
    from untaped.runtime.settings import env_var_name  # noqa: PLC0415
    from untaped.sdk.network.auth import token_override_name  # noqa: PLC0415

    variable = token_override_name(plugin, field=name)
    if _env(variable).strip():
        return variable
    whole = env_var_name([plugin])
    try:
        node = json.loads(_env(whole) or "null")
    except ValueError:
        return None
    value = node.get(name) if isinstance(node, dict) else None
    return whole if isinstance(value, str) and value.strip() else None


def _override_value(plugin: str, name: str) -> str:
    from untaped.runtime.settings import env_var_name  # noqa: PLC0415
    from untaped.sdk.network.auth import token_override_name  # noqa: PLC0415

    direct = _env(token_override_name(plugin, field=name)).strip()
    if direct:
        return direct
    node = json.loads(_env(env_var_name([plugin])) or "{}")
    return str(node[name]).strip()


def _env(name: str) -> str:
    """The variable ``name``'s value, whatever its case; empty when unset."""
    value = os.environ.get(name)
    if value is not None:
        return value
    return next((text for key, text in os.environ.items() if key.upper() == name), "")


def legacy_setting(credential: Credential[Any]) -> tuple[str, str | Callable[[], str]] | None:
    """The deprecated ``<plugin>.<name>`` (plain text) or ``<name>_command`` that would answer.

    ``(setting, value)``: the value is the secret, or a function running the
    command. The plain-text one warns once (the ``plaintext-token``
    behaviour). Only settings in a config file count: the override variable
    is step 1.
    """
    from untaped.sdk.network._credential_store import legacy_values  # noqa: PLC0415

    return legacy_values(credential)


# --------------------------------------------------------------------------- #
# Provenance: which keys a folder chose
# --------------------------------------------------------------------------- #

FOLDER_SOURCE_PREFIX = "project:"
"""How the layout's provenance names a value a project folder's file set."""


def is_folder_source(source: str) -> bool:
    """Whether a provenance label names a folder's file (``project:<name>``)."""
    return source.startswith(FOLDER_SOURCE_PREFIX)


def folder_leaves() -> dict[tuple[str, ...], str]:
    """Every effective leaf a folder's file set, with its source, for this run."""
    from untaped.runtime.settings import (  # noqa: PLC0415
        active_settings_layout,
        load_config_yaml,
        resolve_config_path,
    )

    try:
        raw = load_config_yaml(resolve_config_path())
        provenance = active_settings_layout().resolve(raw).provenance
    except ConfigError:
        return {}
    return {path: source for path, source in provenance.items() if is_folder_source(source)}


def key_fields(
    model: type[BaseModel], prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], type[CredentialKey]]]:
    """``(path, key class)`` of each :class:`CredentialKey`-typed field of ``model``, nested too."""
    for name, field in model.model_fields.items():
        inner = unwrap_optional(field.annotation)
        if not isinstance(inner, type) or not issubclass(inner, BaseModel):
            continue
        if issubclass(inner, CredentialKey):
            yield (*prefix, name), inner
        else:
            yield from key_fields(inner, (*prefix, name))


def _key_value_id(key: CredentialKey) -> tuple[str, str]:
    return type(key).__qualname__, json.dumps(key_data(key), sort_keys=True)


@cache
def _touched() -> tuple[frozenset[tuple[str, str]], dict[tuple[str, str], dict[str, str]]]:
    """The keys a folder chose this run, by value, and which of their leaves it set where."""
    leaves = folder_leaves()
    if not leaves:
        return frozenset(), {}
    from untaped.runtime.settings import (  # noqa: PLC0415
        load_settings_section,
        profile_section_models,
    )

    found: dict[tuple[str, str], dict[str, str]] = {}
    for section, model in profile_section_models().items():
        for path, _model in key_fields(model):
            prefix = (section, *path)
            touched = {
                ".".join(leaf): source
                for leaf, source in leaves.items()
                if leaf[: len(prefix)] == prefix
            }
            if not touched:
                continue
            try:
                value: Any = load_settings_section(section)
            except ConfigError:
                continue
            for part in path:
                value = getattr(value, part, None)
            if isinstance(value, CredentialKey):
                found.setdefault(_key_value_id(value), {}).update(touched)
    return frozenset(found), found


def folder_chose(key: CredentialKey) -> bool:
    """Whether a folder's file set any field of a key equal to ``key`` this run (by value)."""
    return _key_value_id(key) in _touched()[0]


def folder_sources(key: CredentialKey) -> dict[str, str]:
    """``setting -> folder source`` for each field of ``key`` a folder set."""
    return dict(_touched()[1].get(_key_value_id(key), {}))


def forget_provenance() -> None:
    """Drop this run's folder-chosen keys (tests, and a change of pick)."""
    _touched.cache_clear()


def legacy_section_refused(section: str) -> bool:
    """Whether a folder set any leaf of an 11.0-shaped ``section``, which is then one key.

    Its old ``token`` from a variable or a setting is refused, as for a
    declared key a folder chose.
    """
    return any(leaf[0] == section for leaf in folder_leaves())


# --------------------------------------------------------------------------- #
# What the composition declares
# --------------------------------------------------------------------------- #

_DECLARED: dict[str, tuple[Credential[Any], ...]] = {}


def bind_credentials(declared: dict[str, tuple[Credential[Any], ...]]) -> None:
    """Record each composed plugin's credentials (composition calls it on every commit)."""
    _DECLARED.clear()
    _DECLARED.update({plugin: tuple(each) for plugin, each in declared.items() if each})
    forget_provenance()


def declared_credentials(section: str | None = None) -> tuple[Credential[Any], ...]:
    """The credentials ``section``'s plugin declares; every composed plugin's without one."""
    if section is not None:
        return _DECLARED.get(section, ())
    return tuple(each for declared in _DECLARED.values() for each in declared)


def credential_key(credential: Credential[Any], settings: BaseModel | None) -> CredentialKey | None:
    """The key ``credential`` is for this run, read from its section's settings.

    The value of the section's one field typed ``credential.key`` (``None``
    while it is unset). A key no setting holds is its defaults when every
    field has one (a fixed endpoint's empty key); otherwise its fields come
    from the command line (ansible's vault id), and there is none here.
    """
    model = None if settings is None else type(settings)
    if not held_by_settings(credential, model):
        try:
            defaults: CredentialKey = credential.key()
        except ValidationError:
            return None
        return defaults
    for path, each in key_fields(cast("type[BaseModel]", model)):
        if each is not credential.key:
            continue
        value: Any = settings
        for part in path:
            value = getattr(value, part, None)
        return value if isinstance(value, credential.key) else None
    return None


def held_by_settings(credential: Credential[Any], model: type[BaseModel] | None) -> bool:
    """Whether a field of ``model`` holds ``credential``'s key (else its fields are options)."""
    return model is not None and any(each is credential.key for _path, each in key_fields(model))


def credential_list(objects: object) -> tuple[Credential[Any], ...]:
    """``objects`` as a tuple of credentials, or a :class:`TypeError` naming the stray one."""
    values = tuple(cast("Sequence[object]", objects))
    for each in values:
        if not isinstance(each, Credential):
            raise TypeError(f"credentials holds {each!r}, not a Credential")
    return cast("tuple[Credential[Any], ...]", values)


__all__ = [
    "SHORT_ID",
    "Answer",
    "BoundSecret",
    "Credential",
    "CredentialKey",
    "KeyShape",
    "NoCredential",
    "TlsVerify",
    "TokenUrl",
    "bind_credentials",
    "credential_env_names",
    "credential_key",
    "declared_credentials",
    "describe_key",
    "folder_chose",
    "folder_sources",
    "forget_provenance",
    "held_by_settings",
    "is_folder_source",
    "key_data",
    "key_errors",
    "key_fields",
    "key_id",
    "key_origin",
    "key_shape",
    "legacy_section_refused",
]

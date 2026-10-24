"""Doctor checks: the :class:`DoctorCheck` block, the row it returns, and the checks plugins reuse.

A plugin class holds a :class:`DoctorCheck`, :class:`OnlineCheck` or
:class:`ExecutableCheck`; each body gets a :class:`PluginContext` and
returns a :class:`DoctorResult`, one row of ``untaped doctor``. The 11.x
factories return a check a plugin lists in its ``SPEC.doctor_checks``
until its move: :func:`executable_check` and :func:`connection_check`
read the validated settings snapshot and the local machine only: no network
I/O and no ``token_command`` runs. :func:`online_check` is the one online
check: ``doctor --online`` runs it to contact the configured service.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from untaped.runtime.settings import plugins_root, resolve_config_path
from untaped.sdk._block import OwnedBlock, spelled_id
from untaped.sdk.errors import HttpError, HttpTransportError, UntapedError
from untaped.sdk.health._setup_status import (
    set_command,
    shown_value,
    stored_plaintext,
    unset_fields,
)
from untaped.sdk.health.marks import MarkedField, SetupMarks, section_marks
from untaped.sdk.network.auth import (
    PLAINTEXT_TOKEN,
    describe_token_source,
    find_program,
    plaintext_subject,
    takes_token_command,
    token_alternatives,
    token_instead,
)
from untaped.sdk.run import PluginHandle

if TYPE_CHECKING:
    from untaped.runtime.sink import Diagnostics


class DoctorCheck(OwnedBlock):
    """A health check contributed by the shell or a plugin.

    A plugin class holding it names it: ``store = DoctorCheck(title=..., run=...)``
    is the check ``<plugin>.store``; the 11.x spelling passes its ``id``. An
    ``online`` check contacts a remote service, so only ``untaped doctor
    --online`` runs it; every other check stays offline.
    """

    block_kind = "doctor-check"
    name_style = "kebab"

    title: str
    run: Callable[[PluginContext], DoctorResult]
    online: bool

    def __init__(
        self,
        id: str | None = None,
        *,
        title: str,
        run: Callable[[PluginContext], DoctorResult],
        online: bool = False,
    ) -> None:
        super().__init__(*spelled_id(id))
        self._id = id
        self.title = title
        self.run = run
        self.online = online

    @property
    def id(self) -> str:
        """``<plugin>.<name>``, or the id it was given."""
        return self._id if self._id is not None else f"{self.plugin}.{self.name}"

    def __repr__(self) -> str:
        return f"DoctorCheck({self.where!r}, title={self.title!r})"


@dataclass(frozen=True)
class DoctorResult:
    """Outcome of one doctor-check body.

    ``ok=False`` is a failed row (doctor exits 1). ``ok=True`` with
    ``warn=True`` is a ``warn`` row: worth attention (a deprecated setting,
    say) but not a failure, so doctor still exits 0. ``fix`` is the
    ``untaped`` command (without the program name) that repairs a failed or
    warned row, as a string (split like a shell would) or an argv list; a
    value the user supplies is a ``<NAME>`` placeholder. Doctor emits it as
    the row's ``fix`` argv and shows it under the row.

    ``automatic=True`` says the fix is safe to run unattended: it needs no
    ``<NAME>`` value, no terminal input and no confirmation, it is a
    declared write (``@writes``) that takes ``--format``, and it carries any
    ``--yes`` it needs itself. It also asks for and prints no secret, so an
    agent may run it. Doctor fails the row when an automatic fix is missing
    or has a placeholder.
    """

    id: str
    ok: bool
    detail: str
    warn: bool = False
    fix: str | list[str] | None = None
    automatic: bool = False


@dataclass(frozen=True)
class PluginContext:
    """Frozen per-invocation snapshot handed to a doctor-check or migration body.

    ``owner`` names the plugin the body runs for, which core sets; the body
    reaches its folder as ``ctx.plugin.dir``, as a command does.
    """

    settings: BaseModel | None
    owner: str | None = None

    @property
    def plugin(self) -> PluginHandle:
        """The plugin the body runs for: its folder and the files it names."""
        if self.owner is None:
            raise LookupError("this context runs for no plugin: core sets owner= when it builds it")
        return PluginHandle(name=self.owner, home=plugins_root().parent)

    @property
    def diag(self) -> Diagnostics:
        """The stderr handle, as on ``AppContext``: resolved at each call."""
        from untaped.runtime.sink import Diagnostics  # noqa: PLC0415 - the sink imports the SDK

        return Diagnostics()


def executable_check(check_id: str, program: str, *, purpose: str) -> DoctorCheck:
    """Warn when ``program`` is not on ``PATH``; ``purpose`` says what needs it."""
    return DoctorCheck(
        id=check_id,
        title=f"{program} on PATH",
        run=lambda _ctx: _on_path(check_id, program, purpose=purpose),
    )


def _on_path(check_id: str, program: str, *, purpose: str) -> DoctorResult:
    found = find_program(program)
    if found is None:
        return DoctorResult(
            id=check_id,
            ok=True,
            warn=True,
            detail=f"`{program}` not found on PATH; {purpose} will fail",
        )
    return DoctorResult(id=check_id, ok=True, detail=found)


class ExecutableCheck(DoctorCheck):
    """A program on ``PATH``: ``git = ExecutableCheck("git", purpose="cloning")``.

    Held by a plugin class, it is the check ``<plugin>.<attribute>``; a
    missing program is a warning naming ``purpose``, what fails without it.
    """

    program: str
    purpose: str

    def __init__(self, program: str, *, purpose: str) -> None:
        super().__init__(title=f"{program} on PATH", run=self._run)
        self.program = program
        self.purpose = purpose

    def _run(self, _ctx: PluginContext) -> DoctorResult:
        return _on_path(self.id, self.program, purpose=self.purpose)


def connection_check(check_id: str, *, section: str) -> DoctorCheck:
    """Check the resolved profile's ``<section>.base_url`` and token source.

    For a plugin whose settings are not marked (:mod:`untaped.sdk.health.marks`):
    a plugin that marks them gets core's ``setup`` row instead, so it lists
    no ``connection_check``.

    A section with neither is simply unused and passes; one with only half
    of the pair is a warning, and so is a token stored in plain text in the
    config file (``<section>.token``): its fix is the automatic ``auth
    migrate`` when the section takes a ``token_command``, else the
    alternatives are named.
    ``token_command`` is reported, never run.
    """

    def run(ctx: PluginContext) -> DoctorResult:
        settings = ctx.settings
        if settings is None:
            return DoctorResult(id=check_id, ok=True, detail="skipped: settings are invalid")
        base_url = str(getattr(settings, "base_url", None) or "").strip()
        source = describe_token_source(settings, section=section, ambient=bool(base_url))
        if not base_url and source is None:
            return DoctorResult(id=check_id, ok=True, detail="not configured")
        missing = [
            f"{section}.{name}"
            for name, present in (("base_url", bool(base_url)), ("token", source is not None))
            if not present
        ]
        if missing:
            return DoctorResult(
                id=check_id,
                ok=True,
                warn=True,
                detail=(
                    f"{' and '.join(missing)} not configured; "
                    f"{section} commands that call the API will fail"
                ),
            )
        if stored_plaintext(section):
            subject = plaintext_subject(section, resolve_config_path().name)
            if takes_token_command(type(settings)):
                # The row's fix line names `auth migrate`, so the sentence names no replacement.
                return DoctorResult(
                    id=check_id,
                    ok=True,
                    warn=True,
                    detail=f"{base_url}; {PLAINTEXT_TOKEN.sentence(subject, replacement=None)}",
                    fix="auth migrate",
                    automatic=True,
                )
            alternatives = token_instead(settings, section=section)
            sentence = PLAINTEXT_TOKEN.sentence(subject, replacement=alternatives)
            return DoctorResult(id=check_id, ok=True, warn=True, detail=f"{base_url}; {sentence}")
        return DoctorResult(id=check_id, ok=True, detail=f"{base_url}; token from {source}")

    return DoctorCheck(id=check_id, title=f"{section} connection settings", run=run)


def service_configured(settings: BaseModel, *, section: str) -> bool:
    """Whether ``section`` is set up: a token source, or a ``base_url`` of the user's own.

    A model's built-in default URL (GitHub's, say) alone does not count, and
    a conventional token variable counts only alongside a ``base_url``: it
    is not tied to a profile.
    """
    base_url = str(getattr(settings, "base_url", None) or "").strip()
    if describe_token_source(settings, section=section, ambient=bool(base_url)) is not None:
        return True
    field = type(settings).model_fields.get("base_url")
    return bool(base_url) and (field is None or base_url != field.default)


def online_check(
    check_id: str,
    *,
    section: str,
    probe: Callable[[], str],
    title: str | None = None,
) -> DoctorCheck:
    """Contact ``section``'s service through ``probe`` (``doctor --online`` only).

    ``probe`` is the plugin's own authenticated call (``whoami``-style),
    run against the active profile inside :func:`untaped.sdk.network.upstream.quick_probe`
    (no retries, a short timeout); it returns the pass detail and raises on
    failure. A plugin that marks what it needs
    (:mod:`untaped.sdk.health.marks`) is probed only once every marked field is
    set; an unmarked one only once it is :func:`service_configured`. A
    failure keeps one line of its message and names the command that fixes
    it: a new token for a rejected (or, unmarked, missing) one,
    ``http.ca_bundle_path`` for an untrusted certificate, otherwise the first
    marked field (``base_url`` for an unmarked one).
    """
    return DoctorCheck(
        id=check_id,
        title=title or f"{section} API reachable",
        run=lambda ctx: _probed(check_id, ctx.settings, section=section, probe=probe),
        online=True,
    )


def _probed(
    check_id: str, settings: BaseModel | None, *, section: str, probe: Callable[[], str]
) -> DoctorResult:
    """:func:`online_check`'s row: ``probe`` run against the settings, when they are set up."""
    if settings is None:
        return DoctorResult(id=check_id, ok=True, detail="skipped: settings are invalid")
    marks = section_marks(section, type(settings))
    if marks is not None and not marks.legacy:
        return _marked_probe(check_id, settings, marks, section=section, probe=probe)
    if not service_configured(settings, section=section):
        return DoctorResult(id=check_id, ok=True, detail="not configured")
    url_fix = f"config set {section}.base_url <URL>"
    if not str(getattr(settings, "base_url", None) or "").strip():
        detail = f"{section}.base_url is not set"
        return DoctorResult(id=check_id, ok=False, detail=detail, fix=url_fix)
    has_token = describe_token_source(settings, section=section) is not None
    from untaped.sdk.network.upstream import (  # noqa: PLC0415 - keep SPEC imports light
        quick_probe,
    )

    try:
        with quick_probe():
            detail = probe()
    except UntapedError as exc:
        detail = _first_line(exc) or type(exc).__name__
        token_fix = _token_fix(section, settings)
        fix = _online_fix(exc, section=section, has_token=has_token, token_fix=token_fix)
        alternatives = token_alternatives(settings, section=section)
        if fix == token_fix and alternatives:
            detail = f"{detail} (the token can also come from {alternatives})"
        return DoctorResult(id=check_id, ok=False, detail=detail, fix=fix)
    except Exception as exc:
        # Not a service error (e.g. an unexpected response shape): keep one
        # line, never the response body a validation dump would carry.
        detail = f"{type(exc).__name__}: {_first_line(exc)}".rstrip(": ")
        return DoctorResult(id=check_id, ok=False, detail=detail, fix=url_fix)
    return DoctorResult(id=check_id, ok=True, detail=detail)


class OnlineCheck(DoctorCheck):
    """The plugin's service reached through ``probe``, under ``doctor --online`` only.

    ``probe = OnlineCheck(probe=whoami)`` on a plugin class is the check
    ``<plugin>.probe`` over the plugin's own settings section, run as
    :func:`online_check` runs it; ``title`` defaults to ``<plugin> API reachable``.
    """

    probe: Callable[[], str]

    def __init__(self, *, probe: Callable[[], str], title: str | None = None) -> None:
        super().__init__(title=title or "", run=self._run, online=True)
        self.probe = probe

    @property
    def title(self) -> str:
        return self._title or f"{self.plugin} API reachable"

    @title.setter
    def title(self, value: str) -> None:
        self._title = value or None

    def __repr__(self) -> str:
        return f"OnlineCheck({self.where!r}, title={self._title!r})"

    def _run(self, ctx: PluginContext) -> DoctorResult:
        return _probed(self.id, ctx.settings, section=self.plugin, probe=self.probe)


def _marked_probe(
    check_id: str,
    settings: BaseModel,
    marks: SetupMarks,
    *,
    section: str,
    probe: Callable[[], str],
) -> DoctorResult:
    """``probe`` for a plugin that marks what it needs: only once every marked field is set.

    A rejected secret names its ``auth login``, an untrusted certificate
    ``http.ca_bundle_path``; any other failure names the marked fields' values and
    the command that sets the first of them (none when every mark is a secret).
    """
    if unset_fields(settings, marks, section=section):
        # Core's `setup` row says what is missing.
        return DoctorResult(id=check_id, ok=True, detail="skipped: not set up")
    from untaped.sdk.network.upstream import quick_probe  # noqa: PLC0415 - keep SPEC imports light

    try:
        with quick_probe():
            detail = probe()
    except Exception as exc:
        line = _first_line(exc)
        if isinstance(exc, UntapedError):
            detail = line or type(exc).__name__
        else:
            # Not a service error (e.g. an unexpected response shape): keep one
            # line, never the response body a validation dump would carry.
            detail = f"{type(exc).__name__}: {line}".rstrip(": ")
        chain = list(_causes(exc))
        secret = marks.default_secret or next(iter(marks.secrets), None)
        if secret is not None and _rejected(chain):
            alternatives = (
                " or ".join(f"${name}" for name in secret.env[:1])
                if secret.credential is not None
                else token_alternatives(settings, section=section, field=secret.key)
            )
            if alternatives:
                detail = f"{detail} (the {_noun(secret)} can also come from {alternatives})"
            return DoctorResult(
                id=check_id,
                ok=False,
                detail=detail,
                fix=set_command(secret, marks, section=section),
            )
        if _untrusted(chain):
            return DoctorResult(id=check_id, ok=False, detail=detail, fix=_CA_FIX)
        plain = [field for field in marks.required if not field.is_secret]
        if plain:
            shown = ", ".join(f"{section}.{field.key} {_shown(settings, field)}" for field in plain)
            detail = f"{detail} ({shown})"
        fix = set_command(plain[0], marks, section=section) if plain else None
        return DoctorResult(id=check_id, ok=False, detail=detail, fix=fix)
    return DoctorResult(id=check_id, ok=True, detail=detail)


def _noun(secret: MarkedField) -> str:
    return "token" if secret.key == "token" else "secret"


def _shown(settings: BaseModel, field: MarkedField) -> str:
    """``is <value>``, or ``is set`` for a masked one: a failure line never prints a secret."""
    return "is set" if field.masked else f"is {shown_value(settings, field)}"


_CA_FIX = "config set http.ca_bundle_path <PATH>"

#: OpenSSL's verify code for a certificate that does not match the host name.
_HOSTNAME_MISMATCH = 62


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else ""


def _token_fix(section: str, settings: BaseModel) -> str:
    if takes_token_command(type(settings)):
        return f"auth set {section}"
    return f"config set {section}.token --prompt"


def _rejected(chain: list[BaseException]) -> bool:
    return any(isinstance(error, HttpError) and error.status_code in (401, 403) for error in chain)


def _untrusted(chain: list[BaseException]) -> bool:
    return any(
        isinstance(error, ssl.SSLCertVerificationError)
        and getattr(error, "verify_code", None) != _HOSTNAME_MISMATCH
        for error in chain
    )


def _online_fix(exc: BaseException, *, section: str, has_token: bool, token_fix: str) -> str:
    chain = list(_causes(exc))
    if _rejected(chain):
        return token_fix
    if _untrusted(chain):
        return _CA_FIX
    # An unreachable service is a URL problem even when no token is set yet.
    if not has_token and not any(isinstance(error, HttpTransportError) for error in chain):
        return token_fix
    return f"config set {section}.base_url <URL>"


def _causes(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


__all__ = ["connection_check", "executable_check", "online_check", "service_configured"]

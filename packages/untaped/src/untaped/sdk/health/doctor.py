"""Reusable doctor-check factories for plugins.

Each factory returns a :class:`DoctorCheck` a plugin lists in its
``SPEC.doctor_checks``. :func:`executable_check` and :func:`connection_check`
read the validated settings snapshot and the local machine only: no network
I/O and no ``token_command`` runs. :func:`online_check` is the one online
check: ``doctor --online`` runs it to contact the configured service.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable, Iterator

from pydantic import BaseModel

from untaped.runtime.registry import DoctorCheck, DoctorResult, PluginContext
from untaped.runtime.settings import resolve_config_path
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
        self._title = title

    @property
    def title(self) -> str:
        return self._title or f"{self.plugin} API reachable"

    @title.setter
    def title(self, value: str) -> None:
        self._title = value or None

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

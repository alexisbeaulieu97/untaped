"""Thin wrapper around :class:`httpx.Client` with consistent error mapping.

Every HTTP failure (network, timeout, or non-2xx status) is raised as an
:class:`untaped.sdk.errors.HttpError`, so domain layers don't need to know
about httpx-specific exceptions.

TLS verification is centralised in :func:`resolve_verify`. By default we read
the OS trust store via :mod:`truststore`, which transparently picks up
corporate CAs that have been installed system-wide. Override this with
``http.ca_bundle_path`` to pin a specific PEM file, ``http.verify_hostname: false``
to keep chain verification but skip the hostname/SAN check (for a self-signed
cert that modern Python rejects on hostname), or ``http.verify_ssl: false`` to
disable verification entirely (escape hatch — leaves traffic open to MITM).
"""

from __future__ import annotations

import email.utils
import functools
import inspect
import logging
import shlex
import ssl
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, SecretStr

from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import (
    ACTIVE_PROFILE_ENV,
    classify_active_profile,
    selected_profile,
)
from untaped.runtime.redaction import redact_credentials
from untaped.runtime.settings import (
    HttpSettings,
    active_settings_layout,
    env_sets_key,
    env_var_name,
    load_settings_section,
    registered_profile_model,
)
from untaped.sdk._schema import walk_settings
from untaped.sdk.errors import (
    ConfigError,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    UntapedError,
)
from untaped.sdk.health.marks import section_marks
from untaped.sdk.marks import deprecated, deprecated_behaviour, deprecated_parameters
from untaped.sdk.network.auth import takes_token_command, token_alternatives
from untaped.sdk.network.upstream import PROBE_TIMEOUT, capped, succeeded
from untaped.sdk.words import command_line, q, run_hint

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

type AuthFn = Callable[[httpx.Request], httpx.Request]
VerifyTypes = bool | str | ssl.SSLContext
PageFetcher = Callable[[str | None], tuple[list[dict[str, Any]], str | None]]

_LOG = logging.getLogger("untaped.http")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MAX_REDIRECTS = 20


@dataclass(frozen=True)
class RetryPolicy:
    """How :class:`HttpClient` retries transient HTTP failures.

    Transport failures are retried by *phase*: a pre-send connect failure never
    reached the server and is safe to retry for any method, while a post-send
    read/write error may already have been processed and is retried only for
    ``idempotent_methods``. Retryable statuses (429/503) are likewise retried
    only for ``idempotent_methods`` — a caller whose POST is genuinely
    idempotent (e.g. a search endpoint) opts in by passing a policy whose
    ``idempotent_methods`` includes ``"POST"``. ``Retry-After`` (delta-seconds
    or HTTP-date) is honoured up to ``retry_after_max``; otherwise the delay is
    exponential backoff capped at ``backoff_max``.
    """

    max_attempts: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    retry_after_max: float = 60.0
    retry_statuses: tuple[int, ...] = (429, 503)
    honor_retry_after: bool = True
    retry_on_transport: bool = True
    idempotent_methods: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

    def allows_status_retry(self, method: str, status_code: int) -> bool:
        return status_code in self.retry_statuses and method.upper() in self.idempotent_methods

    def allows_transport_retry(self, method: str, *, presend: bool) -> bool:
        return self.retry_on_transport and (presend or method.upper() in self.idempotent_methods)

    def backoff(self, attempt: int) -> float:
        return min(self.backoff_max, self.backoff_base * 2.0 ** (attempt - 1))

    def status_delay(self, attempt: int, retry_after: str | None) -> float:
        if self.honor_retry_after and retry_after:
            parsed = _parse_retry_after(retry_after)
            if parsed is not None:
                return min(parsed, self.retry_after_max)
        return self.backoff(attempt)


class _Inherit:
    """Sentinel: a per-call ``retry`` left unset inherits the client's policy."""


_INHERIT = _Inherit()
_DEFAULT_RETRY = RetryPolicy()


def _parse_retry_after(value: str) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) to seconds.

    Returns ``None`` when unparseable so the caller falls back to computed
    backoff; a past HTTP-date clamps to ``0`` (retry immediately).
    """
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _sleep(seconds: float) -> None:  # pragma: no cover - thin wrapper, patched in tests
    time.sleep(seconds)


def resolve_verify(http: HttpSettings) -> VerifyTypes:
    """Translate :class:`HttpSettings` into a value httpx accepts for ``verify``.

    Resolution order:

    1. ``verify_ssl`` is ``False`` → ``False`` (no verification at all).
    2. ``verify_hostname`` and ``ca_bundle_path`` set → the absolute path of that
       file (httpx builds a default, hostname-checking context from it).
    3. Otherwise → an :class:`ssl.SSLContext` whose ``check_hostname`` mirrors
       ``verify_hostname``: the OS trust store (``truststore``) when no
       ``ca_bundle_path``, else a default context loaded from the bundle.

    ``verify_hostname: false`` keeps chain verification but drops the
    hostname/SAN check — the case where a trusted self-signed cert is still
    rejected by modern Python. The truststore default makes corporate CAs in
    the OS keychain "just work" without per-user configuration.

    A ``ca_bundle_path`` that does not exist raises :class:`ConfigError` naming
    the path (instead of a bare ``FileNotFoundError`` at request time).
    """
    if not http.verify_ssl:
        return False
    if http.ca_bundle_path is not None and not http.ca_bundle_path.expanduser().is_file():
        raise ConfigError(
            f"http.ca_bundle_path points to a missing file: {http.ca_bundle_path.expanduser()} "
            "(fix it with `untaped config set http.ca_bundle_path <path>` or unset it)"
        )
    context = _ssl_context(http.ca_bundle_path)
    # Hostname-checked + a pinned CA → hand httpx the path and let it build
    # the (equivalent) default, hostname-checking context itself. The bundle
    # was still loaded above so an unreadable/invalid one fails here.
    if http.verify_hostname and http.ca_bundle_path is not None:
        return str(http.ca_bundle_path.expanduser())
    context.check_hostname = http.verify_hostname
    return context


def _ssl_context(ca_bundle_path: Path | None) -> ssl.SSLContext:
    """Build an SSLContext: a bundle-loaded default context, else OS trust.

    An unreadable or unparsable ``ca_bundle_path`` raises :class:`ConfigError`.
    """
    if ca_bundle_path is not None:
        path = ca_bundle_path.expanduser()
        try:
            return ssl.create_default_context(cafile=str(path))
        except (ssl.SSLError, OSError) as exc:
            raise ConfigError(
                f"http.ca_bundle_path could not be loaded from {path}: {exc} "
                "(fix it with `untaped config set http.ca_bundle_path <path>` or unset it)"
            ) from exc
    import truststore  # noqa: PLC0415

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


_DEADLINE: ContextVar[float | None] = ContextVar("untaped_http_deadline", default=None)


@contextmanager
def request_deadline(seconds: float | None) -> Iterator[None]:
    """Bound every :class:`HttpClient` request in the block to ``seconds`` from now.

    Inside it, a request starts only while time is left, and each attempt's
    timeout is capped at the time left; the retry policy still applies within
    the budget, and a retry whose wait would end past it is not made (the
    last error stands). A request past it raises :class:`HttpTransportError`
    (``unavailable``). ``None`` leaves an enclosing deadline in effect. The
    deadline is a context variable, so threads started with a copied context
    (``bounded_map``) share it.
    """
    if seconds is None:
        yield
        return
    ends = time.monotonic() + seconds
    held = _DEADLINE.get()
    token = _DEADLINE.set(ends if held is None else min(held, ends))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def _time_left() -> float | None:
    ends = _DEADLINE.get()
    return None if ends is None else ends - time.monotonic()


def _fits(delay: float) -> bool:
    """Whether a retry after ``delay`` seconds could still start before the deadline."""
    left = _time_left()
    return left is None or delay < left


class HttpClient:
    """A minimal HTTP client suitable for talking to JSON APIs.

    ``system`` names the service its failures are attributed to (the
    :class:`HttpError` ``system``); :func:`connected_client` passes the
    settings section, and a client without one reports ``http``.
    """

    def __init__(
        self,
        base_url: str = "",
        *,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        auth: AuthFn | None = None,
        verify: VerifyTypes = True,
        proxy: str | None = None,
        retry: RetryPolicy | None = None,
        system: str | None = None,
    ) -> None:
        import httpx  # noqa: PLC0415

        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=headers or {},
            verify=verify,
            proxy=proxy,
        )
        self._auth = auth
        self._retry = retry
        self._timeout = timeout
        self.system = system or HttpError.system

    def request(
        self,
        method: str,
        path: str,
        *,
        retry: RetryPolicy | None | _Inherit = _INHERIT,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request, retrying transient failures per the active policy.

        ``retry`` overrides the client's policy for this call: a
        :class:`RetryPolicy` replaces it, ``None`` disables retries, and the
        default (``_INHERIT``) keeps the client's policy. The request is rebuilt
        each attempt so re-signing auth and re-reading the body stay correct.
        Redirects are returned, not followed, unless ``follow_redirects=True``;
        then each hop is signed again and one that leaves the request's origin
        raises :class:`HttpError` naming both, before anything is sent there.
        A final failure carries the client's ``system`` and the number of
        ``attempts`` in its ``details``. Inside :func:`request_deadline`, no
        attempt starts past the deadline and each one's timeout is capped at
        the time left. While the upstream budget is capped
        (:mod:`untaped.sdk.network.upstream`), the request is tried once with its timeout
        capped at ``PROBE_TIMEOUT``; a response lifts a ``fail_fast`` cap.
        """
        import httpx  # noqa: PLC0415

        policy = self._retry if isinstance(retry, _Inherit) else retry
        follow = bool(kwargs.pop("follow_redirects", False))
        if capped():
            policy = None
        attempt = 0
        while True:
            attempt += 1
            request = self._client.build_request(method, path, **self._bounded(kwargs))
            if self._auth is not None:
                request = self._auth(request)
            started = time.monotonic()
            sent = [request]
            try:
                response = self._send(sent, follow=follow, started=started)
            except httpx.HTTPError as exc:
                request = sent[-1]
                _log_exchange(request, f"failed: {type(exc).__name__}", started)
                # A redirect hop failing to connect still follows a request the
                # server received: retrying resends it, so it is not pre-send.
                presend = len(sent) == 1 and isinstance(
                    exc,
                    httpx.ConnectError
                    | httpx.ConnectTimeout
                    | httpx.PoolTimeout
                    | httpx.ProxyError,
                )
                # Only genuinely transient transport failures are retried; a
                # permanent HTTPError (UnsupportedProtocol, DecodingError,
                # TooManyRedirects, LocalProtocolError) raises immediately
                # rather than burning the backoff budget.
                transient = presend or isinstance(
                    exc,
                    httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError,
                )
                if (
                    policy is not None
                    and attempt < policy.max_attempts
                    and transient
                    and policy.allows_transport_retry(method, presend=presend)
                    and _fits(delay := policy.backoff(attempt))
                ):
                    _sleep_before_retry(delay, attempt, policy)
                    continue
                raise HttpTransportError(
                    str(exc),
                    url=str(request.url),
                    system=self.system,
                    details={"attempts": attempt},
                ) from exc
            request = sent[-1]
            _log_exchange(request, str(response.status_code), started)
            if response.status_code >= 400:
                if (
                    policy is not None
                    and attempt < policy.max_attempts
                    and policy.allows_status_retry(method, response.status_code)
                    and _fits(
                        delay := policy.status_delay(attempt, response.headers.get("Retry-After"))
                    )
                ):
                    _sleep_before_retry(delay, attempt, policy)
                    continue
                raise HttpStatusError(
                    f"HTTP {response.status_code} for {request.url}",
                    status_code=response.status_code,
                    url=str(request.url),
                    body=_body_snippet(response, _BODY_LIMIT),
                    system=self.system,
                    details={"attempts": attempt},
                )
            succeeded()
            return response

    def _send(self, sent: list[httpx.Request], *, follow: bool, started: float) -> httpx.Response:
        """Send ``sent[0]``; with ``follow``, its same-origin redirects too.

        Each hop sent is appended to ``sent``, so the caller knows which
        request a failure or the response belongs to. httpx builds each hop
        (``next_request``, its method and body per the status); the hop is
        signed again by the client's ``auth``, and none starts past the
        deadline.
        """
        import httpx  # noqa: PLC0415

        response = self._client.send(sent[0])
        while follow and (hop := response.next_request) is not None:
            response.close()
            request = sent[-1]
            if not same_origin(str(hop.url), str(request.url)):
                raise HttpError(
                    f"refusing to follow a redirect from {_site(str(request.url))} "
                    f"to {_site(str(hop.url))}: it leaves the origin",
                    url=str(request.url),
                    system=self.system,
                    hint=(
                        f"if the server moved to {_site(str(hop.url))} and you trust it, "
                        f"update {self.system}'s URL setting"
                    ),
                )
            if len(sent) > _MAX_REDIRECTS:
                raise HttpError(
                    f"more than {_MAX_REDIRECTS} redirects from {sent[0].url}",
                    url=str(request.url),
                    system=self.system,
                )
            left = _time_left()
            if left is not None and left <= 0:
                raise HttpTransportError(
                    "request deadline exceeded before a redirect was followed",
                    url=str(hop.url),
                    system=self.system,
                )
            if left is not None:
                # httpx copies the first request's timeout onto the hop; cap it.
                copied = hop.extensions.get("timeout", {})
                hop.extensions["timeout"] = {
                    key: left if value is None else min(value, left)
                    for key, value in httpx.Timeout(**copied).as_dict().items()
                }
            _log_exchange(request, f"{response.status_code} -> {hop.url}", started)
            sent.append(hop if self._auth is None else self._auth(hop))
            response = self._client.send(sent[-1])
        return response

    def _bounded(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """``kwargs`` with the timeout capped by the upstream budget and the deadline."""
        limit = kwargs.get("timeout", self._timeout)
        if capped():
            limit = min(limit, PROBE_TIMEOUT) if isinstance(limit, int | float) else PROBE_TIMEOUT
            kwargs = {**kwargs, "timeout": limit}
        left = _time_left()
        if left is None:
            return kwargs
        if left <= 0:
            raise HttpTransportError(
                "request deadline exceeded before the request started",
                system=self.system,
            )
        bounded = min(left, limit) if isinstance(limit, int | float) else left
        return {**kwargs, "timeout": bounded}

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Decode the JSON body, mapping parse failures to :class:`HttpError`.

        Returns ``None`` for empty bodies (e.g. 204 DELETE).
        """
        response = self.request(method, path, **kwargs)
        return _decode_json(response, system=self.system)

    def get_json(self, path: str, **kwargs: Any) -> Any:
        return self.request_json("GET", path, **kwargs)

    def get_json_dict(self, path: str, **kwargs: Any) -> dict[str, Any]:
        """GET ``path`` and assert the JSON body decodes to an object.

        Raises :class:`HttpError` (with full URL + status + body snippet,
        same shape as other ``HttpClient`` errors) when the body is
        anything other than a JSON object (array, scalar, ``null``).
        Adapter sites that promise ``-> dict[str, Any]`` call this
        instead of ``get_json`` so they don't have to suppress
        ``no-any-return`` at the seam.
        """
        response = self.request("GET", path, **kwargs)
        return _decode_json_dict(response, system=self.system)

    def get_json_list(self, path: str, **kwargs: Any) -> list[Any]:
        """GET ``path`` and assert the JSON body decodes to an array.

        Raises :class:`HttpError` (with full URL + status + body snippet,
        same shape as other ``HttpClient`` errors) when the body is
        anything other than a JSON array (object, scalar, ``null``).
        """
        response = self.request("GET", path, **kwargs)
        body = _decode_json(response, system=self.system)
        if not isinstance(body, list):
            raise HttpError(
                f"expected JSON array from {response.request.url}, got {type(body).__name__}",
                status_code=response.status_code,
                url=str(response.request.url),
                body=_body_snippet(response, _BODY_SNIPPET_LIMIT),
                system=self.system,
            )
        return body

    def post_json(self, path: str, **kwargs: Any) -> Any:
        return self.request_json("POST", path, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _log_exchange(request: httpx.Request, outcome: str, started: float) -> None:
    """Debug-log one request: method, URL (password masked), outcome, elapsed time."""
    if _LOG.isEnabledFor(logging.DEBUG):
        elapsed_ms = (time.monotonic() - started) * 1000
        url = redact_credentials(str(request.url))
        _LOG.debug("%s %s -> %s (%.0f ms)", request.method, url, outcome, elapsed_ms)


def _sleep_before_retry(delay: float, attempt: int, policy: RetryPolicy) -> None:
    _LOG.debug("retrying in %.1fs (attempt %d of %d)", delay, attempt + 1, policy.max_attempts)
    _sleep(delay)


_BODY_LIMIT = 2048
_BODY_SNIPPET_LIMIT = 256


def _body_snippet(response: httpx.Response, limit: int) -> str:
    """Decode at most ``limit`` bytes of ``response.content``.

    Used at every site that attaches a response body to ``HttpError``:
    the bytes are already buffered by httpx, but the decoded string
    survives on the exception (often through ``report_errors`` to
    stderr) long after the ``Response`` is collected — so the *string*
    needs the cap, not the buffer. ``errors="replace"`` keeps the error
    path crash-free on a truncated multi-byte sequence at the cost of
    a few ``\\ufffd`` characters; charset from the response header is
    intentionally ignored since this only ever feeds diagnostics.
    """
    return response.content[:limit].decode("utf-8", errors="replace")


def _decode_json(response: httpx.Response, *, system: str | None = None) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise HttpError(
            f"non-JSON response from {response.request.url}: {exc}",
            status_code=response.status_code,
            url=str(response.request.url),
            body=_body_snippet(response, _BODY_SNIPPET_LIMIT),
            system=system,
        ) from exc


def _decode_json_dict(response: httpx.Response, *, system: str | None = None) -> dict[str, Any]:
    body = _decode_json(response, system=system)
    if not isinstance(body, dict):
        raise HttpError(
            f"expected JSON object from {response.request.url}, got {type(body).__name__}",
            status_code=response.status_code,
            url=str(response.request.url),
            body=_body_snippet(response, _BODY_SNIPPET_LIMIT),
            system=system,
        )
    return body


# --------------------------------------------------------------------------- #
# Connection + pagination toolkit for tool API clients
# --------------------------------------------------------------------------- #


def missing_setting_error(
    section: str,
    field: str,
    *more_fields: str,
    secret: Collection[str] = (),
    token_sources: str = "",
    auth_set: bool = False,
    profile: str | None = None,
    set_in: Sequence[str] = (),
) -> ConfigError:
    """Return the standard error for missing plugin settings.

    Names every missing field; the ``hint`` names the root command (and env
    var) that sets each one. Fields listed in ``secret`` suggest ``config set … --prompt`` so the
    value never lands in shell history. ``token_sources`` (from
    :func:`untaped.sdk.network.auth.token_alternatives`) names the sources that keep a
    missing token out of the config file. ``auth_set`` (the section's model
    has ``token_command``) makes the token's command ``auth set <section>``.
    ``profile`` names the selected profile in the message. ``set_in`` names
    the other profiles that set the fields: the hint leads with how to run
    the command under one (``--profile`` or ``UNTAPED_PROFILE``), and
    ``details["profiles"]`` lists them, so a caller can recover without
    asking anyone for the value. Nothing switches profile on its own.
    """
    tail = f"; to keep the token out of the config file, use {token_sources}"
    tail = tail if token_sources else ""
    fields = (field, *more_fields)
    keys = [f"{section}.{name}" for name in fields]
    where = f" in profile {q(profile)}" if profile else ""
    lead = _other_profiles_hint(set_in, plural=len(fields) > 1)
    details = {"profiles": list(set_in)} if set_in else None
    commands = [
        f"`{command_line(f'auth set {section}')}`"
        if name == "token" and auth_set
        else f"`untaped config set {section}.{name} --prompt`"
        if name in secret
        else f"`untaped config set {section}.{name} <{name.rsplit('_', maxsplit=1)[-1]}>`"
        for name in fields
    ]
    env_vars = [env_var_name([section, name]) for name in fields]
    if len(fields) == 1:
        return ConfigError(
            f"{keys[0]} is not configured{where}",
            hint=f"{lead}set it via {commands[0]} or {env_vars[0]}{tail}",
            details=details,
        )
    return ConfigError(
        f"{', '.join(keys[:-1])} and {keys[-1]} are not configured{where}",
        hint=f"{lead}set them via {' and '.join(commands)}, or {' / '.join(env_vars)}{tail}",
        details=details,
    )


def _other_profiles_hint(profiles: Sequence[str], *, plural: bool) -> str:
    """The hint's lead naming the other profiles that set the missing fields."""
    if not profiles:
        return ""
    it = "them" if plural else "it"
    if len(profiles) == 1:
        name = shlex.quote(profiles[0])
        return (
            f"profile {q(profiles[0])} sets {it}: {run_hint(f'--profile {name} …')} "
            f"or set {ACTIVE_PROFILE_ENV}={name}; or "
        )
    names = ", ".join(q(name) for name in profiles[:-1]) + f" and {q(profiles[-1])}"
    return (
        f"profiles {names} set {it}: {run_hint('--profile NAME …')} "
        f"or set {ACTIVE_PROFILE_ENV}=NAME; or "
    )


def rejected_token_error(
    section: str,
    message: str,
    *,
    cause: BaseException | None = None,
    field: str = "token",
) -> ConfigError:
    """The standard error for a service rejecting the secret ``field`` of ``section`` (HTTP 401).

    ``auth`` in ``section`` (exit ``4``). For a declared credential the hint
    is ``auth login <section>`` (``auth login <section> <name>`` for one not
    named ``token``), the person's step. For an 11.0 section it is ``auth
    set <section>`` when its model takes ``token_command``, else ``config
    set <section>.<field> --prompt``. The ``cause``'s details (``status``,
    ``url``) are kept.
    """
    from untaped.sdk.network.credentials import declared_credentials  # noqa: PLC0415

    details = cause.details if isinstance(cause, UntapedError) else None
    declared = {each.name for each in declared_credentials(section)}
    model = registered_profile_model(section)
    if field in declared:
        command = f"auth login {section}" + ("" if field == "token" else f" {field}")
    elif field == "token" and model is not None and takes_token_command(model):
        command = f"auth set {section}"
    else:
        command = f"config set {section}.{field} --prompt"
    return ConfigError(
        message,
        category="auth",
        system=section,
        hint=f"run `{command_line(command)}`",
        details=details,
        fix_by="user" if field in declared else None,
    )


class _FromConfig:
    """``connected_client``'s ``base_url`` and ``bearer`` default: read from ``config``."""

    def __repr__(self) -> str:
        return "<from config>"


_FROM_CONFIG: Any = _FromConfig()


CONNECTED_BY_NAME = deprecated_behaviour(
    "connected-client-by-name", removed_in=12, replacement="base_url= and bearer= values"
)
"""``connected_client`` reading ``base_url`` and ``token`` off the settings by field name."""

#: ``connected_client``'s parameters for each kind of call: values, or the deprecated read by name.
_BY_VALUE = ("bearer", "verify")
_BY_NAME = ("required", "base_url_field", "bearer_token_field")


def _one_kind[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Refuse a ``connected_client`` call that mixes values with the read by name.

    Decided by the parameters the call names, so passing an old parameter's
    default still counts.
    """
    signature = inspect.signature(function)

    @functools.wraps(function)
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        passed = signature.bind_partial(*args, **kwargs).arguments
        if "base_url" in passed:
            if mixed := [f"{name}=" for name in _BY_NAME if name in passed]:
                raise TypeError(f"connected_client: pass base_url= or {_join(mixed)}, not both")
        elif mixed := [f"{name}=" for name in _BY_VALUE if name in passed]:
            raise TypeError(f"connected_client: pass base_url= with {_join(mixed)}")
        return function(*args, **kwargs)

    return call


@deprecated_parameters
@_one_kind
def connected_client(
    config: BaseModel,
    *,
    section: str,
    base_url: str | None = _FROM_CONFIG,
    bearer: SecretStr | str | None = None,
    required: Annotated[tuple[str, ...], deprecated(removed_in=12, replacement="base_url=")] = (
        "base_url",
        "token",
    ),
    headers: Mapping[str, str] | None = None,
    base_url_field: Annotated[str, deprecated(removed_in=12, replacement="base_url=")] = "base_url",
    bearer_token_field: Annotated[
        str | None, deprecated(removed_in=12, replacement="bearer=")
    ] = "token",
    http: HttpSettings | None = None,
    verify: bool | Path | None = None,
    retry: RetryPolicy | None = _DEFAULT_RETRY,
) -> HttpClient:
    """Build an :class:`HttpClient` for a service from the values it needs.

    ``base_url`` is the service's URL (its key's ``url``); a blank one
    raises "<section> is not set up", naming the marked settings still
    unset, with ``untaped setup plan --only <section>`` as the hint.
    ``bearer`` is an ``Authorization: Bearer`` header's token: a
    :class:`~untaped.sdk.network.credentials.BoundSecret` from ``Credential.get``, or
    ``None`` for a client without one (an unauthenticated ``ping``).
    ``verify`` is the key's :data:`~untaped.sdk.network.credentials.TlsVerify`: ``True``
    keeps untaped's trust (root ``http.ca_bundle_path``, else the OS store),
    ``False`` checks nothing, a CA file replaces it for this server. Left
    out, the client applies the deprecated root ``http.verify_ssl`` too.

    The credential is bound to its origin (the ``BoundSecret``'s, else
    ``base_url``'s): it is added to each request to that origin, and a
    request to any other raises :class:`HttpError` naming both, before
    anything is sent. A ``BoundSecret`` for another origin than
    ``base_url``'s, or one bound to none, is refused here. A credentialed
    client also refuses a proxy a folder's file set unless the request is
    https and verified, and any trust setting a folder's file set.
    ``headers`` are sent on every request; a caller's ``Authorization`` wins
    over the bearer and is bound the same way. ``http`` defaults to the
    run's resolved :class:`HttpSettings` (proxy, CA bundle, timeout);
    ``retry`` to a safe :class:`RetryPolicy` (transport and idempotent
    429/503 backoff), ``None`` to disable. The client's failures are
    attributed to ``section``.

    Leaving out ``base_url`` reads the settings by field name, the way
    ``required`` (default ``("base_url", "token")``), ``base_url_field``
    (``"base_url"``) and ``bearer_token_field`` (``"token"``) describe: the
    deprecated behaviour :data:`CONNECTED_BY_NAME`. Passing ``base_url``
    with any of those three, or ``bearer`` or ``verify`` without it,
    refuses.
    """
    override: VerifyTypes | None = None
    if base_url is _FROM_CONFIG:
        CONNECTED_BY_NAME.warn(
            f"calling connected_client for {section} without base_url=", stacklevel=4
        )
        url, token = _read_by_name(
            config,
            section=section,
            required=required,
            base_url_field=base_url_field,
            bearer_token_field=bearer_token_field,
        )
        origin = url
        if token and _legacy_refused(section):
            raise _legacy_refusal(section)
    else:
        url = _text(base_url)
        if not url:
            raise _not_set_up(config, section=section)
        token = _text(bearer)
        origin = _bound_origin(bearer, url, section=section)
        override = None if verify is None else _key_verify(verify)
    http_settings = http if http is not None else load_settings_section("http")
    request_headers = dict(headers or {})
    credential = _pop_authorization(request_headers) or (f"Bearer {token}" if token else None)
    base = url.rstrip("/")
    if credential is not None:
        _refuse_folder_transport(
            http_settings,
            base,
            verified=_verified(override, http_settings, keyed=verify is not None),
            section=section,
        )
    if http_settings.proxy:
        _LOG.debug("%s: via proxy %s", section, redact_credentials(http_settings.proxy))
    return HttpClient(
        base_url=base,
        headers=request_headers,
        auth=None if credential is None else _origin_bound(credential, origin, system=section),
        verify=resolve_verify(_trust(http_settings, keyed=verify is not None))
        if override is None
        else override,
        timeout=http_settings.timeout_seconds,
        proxy=http_settings.proxy,
        retry=retry,
        system=section,
    )


def _verified(override: VerifyTypes | None, http: HttpSettings, *, keyed: bool) -> bool:
    """Whether a client's TLS checks the chain and the host: its key's, else the root's."""
    if override is not None:
        return override is not False
    return (keyed or http.verify_ssl) and http.verify_hostname


def _trust(http: HttpSettings, *, keyed: bool) -> HttpSettings:
    """The root settings untaped's own trust comes from; a key's client leaves ``verify_ssl``.

    Root ``verify_ssl: false`` already reads as the key's ``verify_tls:
    false`` (the settings layout), so a key that still says ``true`` set it.
    """
    if keyed and not http.verify_ssl:
        return http.model_copy(update={"verify_ssl": True})
    return http


def _key_verify(verify: bool | Path) -> VerifyTypes | None:
    """A key's TLS as httpx takes it; ``None`` for untaped's own trust (``True``)."""
    if verify is True:
        return None
    if verify is False:
        return False
    if isinstance(verify, Path | str):
        from untaped.sdk.network.credentials import _absolute  # noqa: PLC0415

        path = _absolute(Path(verify))
        if not path.is_file():
            raise ConfigError(f"the CA file {path} does not exist")
        return str(path)
    raise TypeError(f"connected_client(verify=...) takes a key's verify_tls, not {verify!r}")


def _bound_origin(bearer: object, url: str, *, section: str) -> str:
    """The origin ``bearer`` may go to: its own (a ``BoundSecret``), checked against ``url``."""
    from untaped.sdk.network.credentials import BoundSecret  # noqa: PLC0415

    if not isinstance(bearer, BoundSecret) or not _text(bearer):
        return url
    if bearer.origin is None:
        raise ConfigError(
            f"{section}: this credential is not for HTTP, so it is never sent as a bearer",
            system=section,
        )
    if not same_origin(url, bearer.origin):
        raise ConfigError(
            f"{section}: refusing to build a client for {_site(url)} with a credential bound to "
            f"{_site(bearer.origin)}",
            system=section,
        )
    return bearer.origin


def _refuse_folder_transport(
    http: HttpSettings, base: str, *, verified: bool, section: str
) -> None:
    """Refuse to send a credential under a proxy or trust setting a folder's file chose.

    A proxy is allowed over verified https (it can't read or change the
    request); a CA bundle never is, nor TLS turned off.
    """
    from untaped.sdk.network.credentials import folder_leaves  # noqa: PLC0415

    leaves = folder_leaves()
    if not leaves:
        return
    for leaf in (("http", "ca_bundle_path"), ("http", "verify_ssl"), ("http", "verify_hostname")):
        if leaf in leaves:
            raise ConfigError(
                f"{leaves[leaf]} sets {'.'.join(leaf)}; untaped won't send a credential under a "
                "trust setting a folder chose",
                system=section,
                fix_by="user",
            )
    proxy = ("http", "proxy")
    if proxy in leaves and http.proxy and not (verified and base.startswith("https://")):
        raise ConfigError(
            f"{leaves[proxy]} sets http.proxy, and this {section} request isn't verified https; "
            "untaped won't send a credential through a folder's proxy without it",
            system=section,
            fix_by="user",
        )


def _legacy_refused(section: str) -> bool:
    from untaped.sdk.network.credentials import legacy_section_refused  # noqa: PLC0415

    return legacy_section_refused(section)


def _legacy_refusal(section: str) -> ConfigError:
    return ConfigError(
        f"a folder's settings set {section}'s settings; untaped never sends a token from a "
        "variable or a setting to a server a folder chose",
        system=section,
        fix_by="user",
    )


def _pop_authorization(headers: dict[str, str]) -> str | None:
    """Remove and return ``headers``' ``Authorization`` (any case), if it has one."""
    name = next((name for name in headers if name.lower() == "authorization"), None)
    return None if name is None else headers.pop(name)


def _origin_bound(credential: str, base: str, *, system: str) -> AuthFn:
    """An ``auth=`` hook adding ``Authorization: credential`` on ``base``'s origin only.

    A request to another origin raises :class:`HttpError` naming both sites,
    before anything is sent, so the credential never leaves the server it
    was set up for. A request's own ``Authorization`` header is kept.
    """

    def sign(request: httpx.Request) -> httpx.Request:
        if not same_origin(str(request.url), base):
            raise HttpError(
                f"refusing to send {system}'s credential to {_site(str(request.url))}: "
                f"it is bound to {_site(base)}",
                url=str(request.url),
                system=system,
            )
        request.headers.setdefault("Authorization", credential)
        return request

    return sign


def _site(url: str) -> str:
    """``scheme://host[:port]`` of an absolute ``url``: its origin, as a message names it."""
    scheme, host, port = _origin(url) or ("", "", None)
    host = f"[{host}]" if host and ":" in host else host or ""
    shown = port not in {None, -1, _DEFAULT_PORTS.get(scheme)}
    return f"{scheme}://{host}" + (f":{port}" if shown else "")


def _text(value: object) -> str:
    """A setting's value as stripped text (``""`` for none); a ``SecretStr`` is unwrapped."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return str(value).strip() if value is not None else ""


def _not_set_up(config: BaseModel, *, section: str) -> ConfigError:
    """ "<section> is not set up", naming its unset marked settings, else ``base_url=``."""
    from untaped.sdk.health._setup_status import (  # noqa: PLC0415 - imports the registry
        unset_fields,
    )

    marks = section_marks(section, type(config))
    fields = [] if marks is None else list(unset_fields(config, marks, section=section))
    fields = [field for field in fields if not field.is_secret]
    if fields:
        keys = [f"{section}.{field.key}" for field in fields]
        profile, set_in = _profiles_for(section, [(field.key,) for field in fields])
        what = f"{_join(keys)} {'is' if len(keys) == 1 else 'are'} not set"
    else:
        profile, set_in = _profiles_for(section, [])
        what = "base_url= has no value"
    where = f" in profile {q(profile)}" if profile else ""
    lead = _other_profiles_hint(set_in, plural=len(fields) > 1)
    return ConfigError(
        f"{section} is not set up{where}: {what}",
        hint=f"{lead}{run_hint(f'setup plan --only {section}')}",
        details={"profiles": list(set_in)} if set_in else None,
    )


def _join(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _read_by_name(
    config: BaseModel,
    *,
    section: str,
    required: tuple[str, ...],
    base_url_field: str,
    bearer_token_field: str | None,
) -> tuple[str, str]:
    """The old read: ``required`` fields must be non-blank; the URL and bearer by name."""
    values: dict[str, str] = {}
    missing: list[str] = []
    # ``base_url_field`` and ``bearer_token_field`` are always walked — even
    # when not listed in ``required`` — so the client can be built and the
    # bearer header set; de-dup keeps a field named twice from being read twice.
    optional = (base_url_field, bearer_token_field) if bearer_token_field else (base_url_field,)
    for field in dict.fromkeys((*required, *optional)):
        value = _text(getattr(config, field, None))
        if field in required and not value:
            missing.append(field)
        values[field] = value
    if missing:
        secret = [d.path[0] for d in walk_settings(type(config)) if d.is_secret]
        sources = token_alternatives(config, section=section) if "token" in missing else ""
        auth_set = takes_token_command(type(config))
        needs = [
            (name, "token_command") if name == "token" and auth_set else (name,) for name in missing
        ]
        profile, set_in = _profiles_for(section, needs)
        raise missing_setting_error(
            section,
            *missing,
            secret=secret,
            token_sources=sources,
            auth_set=auth_set,
            profile=profile,
            set_in=set_in,
        )
    token = values.get(bearer_token_field, "") if bearer_token_field is not None else ""
    return values[base_url_field], token


def _profiles_for(section: str, needs: Sequence[tuple[str, ...]]) -> tuple[str | None, list[str]]:
    """The selected profile and the other profiles that resolve every one of ``needs``.

    Each need is a missing field, then the settings that also supply it (a
    secret's command setting). Each profile is resolved as it would be
    selected (``default`` beneath it, old key names renamed). An
    environment override of a missing field applies to every profile, so it
    names none; an unreadable config file names only a profile chosen by
    ``--profile`` or ``UNTAPED_PROFILE``.
    """
    if any(env_sets_key(section, need[0]) for need in needs):
        return None, []
    try:
        data = read_config_dict()
    except ConfigError:
        return classify_active_profile({})[0], []
    profile = selected_profile(data)
    if not needs:
        return profile, []
    layout = active_settings_layout()
    found: list[str] = []
    for name in layout.profile_names(data):
        if name == profile:
            continue
        try:
            values = layout.effective(data, profile=name).get(section)
        except ConfigError:
            continue
        if isinstance(values, dict) and all(
            any(_is_set(values.get(field)) for field in need) for need in needs
        ):
            found.append(name)
    return profile, found


def _is_set(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def paginate_pages(
    fetch: PageFetcher,
    *,
    limit: int | None,
    max_pages: int = 100,
) -> Iterator[dict[str, Any]]:
    """Drive a cursor-style pagination loop.

    ``fetch`` maps a cursor (``None`` for the first page) to
    ``(items, next_cursor)``; iteration ends when ``next_cursor`` is ``None``,
    repeats (a server echoing the same page forever), or ``limit`` rows have
    been yielded. More than ``max_pages`` fetches raises
    :class:`UntapedError` — a paginator that never converges is a server bug
    worth surfacing, not looping on.
    """
    if limit is not None and limit <= 0:
        return
    cursor: str | None = None
    seen_cursors: set[str] = set()
    yielded = 0
    for _ in range(max_pages):
        items, next_cursor = fetch(cursor)
        for item in items:
            if limit is not None and yielded >= limit:
                return
            yield item
            yielded += 1
        if next_cursor is None or next_cursor in seen_cursors:
            return
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise UntapedError(f"pagination did not converge after {max_pages} pages")


def _parse_link_next(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for value in _split_link_header(link_header):
        url, params = _parse_link_value(value)
        rel = params.get("rel")
        if rel is not None and "next" in rel.split():
            return url
    return None


def _split_link_header(link_header: str) -> list[str]:
    values: list[str] = []
    start = 0
    in_angle = False
    for index, char in enumerate(link_header):
        if char == "<":
            in_angle = True
        elif char == ">":
            in_angle = False
        elif char == "," and not in_angle:
            values.append(link_header[start:index].strip())
            start = index + 1
    values.append(link_header[start:].strip())
    return [value for value in values if value]


def _parse_link_value(value: str) -> tuple[str, dict[str, str]]:
    if not value.startswith("<"):
        return "", {}
    end = value.find(">")
    if end < 0:
        return "", {}
    params: dict[str, str] = {}
    for raw_param in value[end + 1 :].split(";"):
        raw_param = raw_param.strip()
        if not raw_param or "=" not in raw_param:
            continue
        name, raw_value = raw_param.split("=", maxsplit=1)
        param_value = raw_value.strip()
        if len(param_value) >= 2 and param_value[0] == '"' and param_value[-1] == '"':
            param_value = param_value[1:-1]
        params[name.strip().lower()] = param_value
    return value[1:end], params


def same_origin(url: str, base: str) -> bool:
    """Whether following ``url`` from ``base`` stays on ``base``'s origin.

    The origin is the scheme, host and port, with a default port equal to an
    omitted one; a host-less relative ``url`` always stays. Refuse a
    server-supplied link that fails this before sending credentials to it.
    """
    return _origin(url) in {None, _origin(base)}


def _origin(url: str) -> tuple[str, str | None, int | None] | None:
    """``(scheme, host, port)`` of ``url``; ``None`` for a host-less relative path."""
    parts = urlsplit(url.strip())
    if not parts.scheme and not parts.netloc:
        return None
    scheme = parts.scheme.lower()
    host = _sent_host(parts.hostname)
    try:
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = -1  # an unparsable port never matches a well-formed origin
    return scheme, host, port


def _sent_host(host: str | None) -> str | None:
    """``host`` as httpx sends it: an IDN in punycode (IDNA 2008), else unchanged.

    A host httpx refuses stays as it is: it never matches a host httpx sends to.
    """
    if not host or host.isascii():
        return host
    import httpx  # noqa: PLC0415

    try:
        return httpx.URL(f"http://{host}/").raw_host.decode("ascii")
    except httpx.InvalidURL:
        return host


def _same_origin_next(response: httpx.Response, *, system: str | None = None) -> str | None:
    """Resolve the ``rel="next"`` link, refusing to leave the current origin.

    The client attaches its credentials (e.g. a bearer token) to every
    request, so following a link to another scheme/host/port would leak
    them to a server the user never configured.
    """
    link = _parse_link_next(response.headers.get("link"))
    if link is None:
        return None
    current = response.request.url
    target = current.join(link)
    if not same_origin(str(target), str(current)):
        raise HttpError(
            f"refusing to follow cross-origin pagination link {target} from {current}",
            url=str(current),
            system=system,
        )
    return str(target)


def paginate_link(
    http: HttpClient,
    path: str,
    *,
    params: Mapping[str, str] | None = None,
    page_size: int = 100,
    size_param: str = "per_page",
    limit: int | None = None,
    item_key: str | None = None,
    max_pages: int = 100,
) -> Iterator[dict[str, Any]]:
    """Walk an RFC 5988 ``Link``-header paginated endpoint (GitHub-style).

    The first request asks for ``min(page_size, limit)`` rows via
    ``size_param``; each ``rel="next"`` URL carries its own cursor
    parameters, so it is followed verbatim with no extra params. A next link
    to a different origin raises :class:`HttpError` rather than sending the
    client's credentials elsewhere; a non-JSON page raises too.
    ``item_key`` unwraps an envelope (e.g. search's ``items``); ``None``
    means the payload body is the row array. A 200 with a non-list body
    short-circuits gracefully. Loop mechanics (limit, cursor-cycle guard,
    ``max_pages``) are :func:`paginate_pages`.
    """
    first_page = min(page_size, limit) if limit is not None else page_size
    first_params = {**dict(params or {}), size_param: str(first_page)}

    def fetch(cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
        response = http.get(cursor or path, params=first_params if cursor is None else None)
        payload = _decode_json(response, system=http.system)
        items = (
            payload.get(item_key) if item_key is not None and isinstance(payload, dict) else payload
        )
        if not isinstance(items, list):
            return [], None
        return items, _same_origin_next(response, system=http.system)

    yield from paginate_pages(fetch, limit=limit, max_pages=max_pages)


def paginate_offset(
    http: HttpClient,
    method: Literal["GET", "POST"],
    path: str,
    *,
    item_key: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    page_size: int = 50,
    limit: int | None = None,
    start_param: str = "offset",
    size_param: str = "limit",
    last_flag: str | None = None,
    max_pages: int | None = 100,
    retry: RetryPolicy | None | _Inherit = _INHERIT,
) -> Iterator[dict[str, Any]]:
    """Walk offset/limit collection envelopes with neutral parameter names.

    Rows live under ``item_key``; termination honours ``total`` when the server
    provides one, or ``last_flag`` when named (a boolean is-last field such as
    Jira's ``isLast``), and otherwise stops at the first short or empty page.
    ``retry`` is forwarded per page-fetch: the default ``_INHERIT`` uses the
    client's policy, while a caller fetching an idempotent ``POST`` collection
    (e.g. a JQL search) can pass a POST-inclusive :class:`RetryPolicy` to make
    just that endpoint retry.
    """
    if limit is not None and limit <= 0:
        return
    emitted = 0
    start = 0
    pages = 0
    while True:
        request_size = page_size if limit is None else min(page_size, limit - emitted)
        if request_size <= 0:
            return
        if max_pages is not None and pages >= max_pages:
            raise UntapedError(f"pagination did not converge after {max_pages} pages")
        pages += 1
        window = {start_param: start, size_param: request_size}
        payload = _fetch_offset_page(
            http, method, path, params=params, body=body, window=window, retry=retry
        )
        rows = payload.get(item_key) if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return
        for row in rows:
            if isinstance(row, dict):
                yield row
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
        if _offset_pages_exhausted(
            payload,
            start=start,
            rows=len(rows),
            requested=request_size,
            last_flag=last_flag,
        ):
            return
        start += len(rows)


def _fetch_offset_page(
    http: HttpClient,
    method: Literal["GET", "POST"],
    path: str,
    *,
    params: dict[str, Any] | None,
    body: dict[str, Any] | None,
    window: dict[str, Any],
    retry: RetryPolicy | None | _Inherit = _INHERIT,
) -> Any:
    if method == "GET":
        return http.get_json_dict(path, params={**(params or {}), **window}, retry=retry)
    response = http.request("POST", path, json={**(body or {}), **window}, retry=retry)
    return _decode_json_dict(response, system=http.system)


def _offset_pages_exhausted(
    payload: dict[str, Any], *, start: int, rows: int, requested: int, last_flag: str | None
) -> bool:
    if last_flag is not None:
        marker = payload.get(last_flag)
        if marker is True:
            return True
        if marker is False:
            return False
    total = payload.get("total")
    if isinstance(total, int):
        return start + rows >= total
    return rows < requested

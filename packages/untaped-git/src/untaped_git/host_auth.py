"""What a git command reaching a host gets: its credential and untaped's proxy, scoped to it.

The one home for git auth: the settings a credential becomes
(:func:`host_config`), and how ``run_git`` hands settings that carry a secret
to git (:func:`auth_include`, a private file never in argv or the
environment, with :func:`drop_traces` so git's trace output can't log them).
"""

from __future__ import annotations

import base64
import os
import tempfile
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from pathlib import Path

from untaped.sdk import http_of
from untaped_git.hosts import Credential, HostAuth
from untaped_git.settings import GitSettings
from untaped_git.url import https_origin


def host_config(url: str, auth: HostAuth | None) -> tuple[dict[str, str], dict[str, str]]:
    """What a command reaching ``url`` gets: ``run_git``'s ``auth_config`` and ``config``.

    ``auth``'s credential and untaped's proxy (``untaped.http.proxy``), both
    scoped to ``url``'s https origin; an ssh URL gets neither (its
    credentials are the user's own).
    """
    origin = https_origin(url)
    if origin is None:
        return {}, {}
    credential = None if auth is None else auth.credential
    secret = {} if credential is None else _credential_config(origin, credential)
    verified = credential is None or credential.verify_tls is not False
    found = http_of(GitSettings).proxy_for(url, verified=verified)
    proxy = {f"http.{origin}/.proxy": found} if found else {}
    return secret, proxy


def _credential_config(origin: str, credential: Credential) -> dict[str, str]:
    """The command-scope settings that send ``credential`` to ``origin`` only (``run_git``'s
    ``auth_config``: a private include file, never argv or the environment).

    The CA file git checks ``origin`` against goes with it, when the
    credential names one; otherwise git verifies as it would.
    """
    header = basic_header(credential.username, credential.password)
    found = {f"http.{origin}/.extraHeader": header}
    if isinstance(credential.verify_tls, Path):
        found[f"http.{origin}/.sslCAInfo"] = str(credential.verify_tls)
    return found


def basic_header(username: str, password: object) -> str:
    """The transient ``AUTHORIZATION`` header for HTTP basic credentials."""
    secret = getattr(password, "get_secret_value", lambda: str(password))()
    token = base64.b64encode(f"{username}:{secret}".encode()).decode()
    return f"AUTHORIZATION: basic {token}"


@contextmanager
def auth_include(settings: Mapping[str, str] | None) -> Iterator[Path | None]:
    """Write ``settings`` (``section[.subsection].name`` → value) to a private include file.

    The values never appear in argv or the environment, only in this 0600
    file, which is deleted on exit. No settings: no file, and ``None``.
    """
    if not settings:
        yield None
        return
    handle, name = tempfile.mkstemp(prefix="untaped-git-auth-", suffix=".config")
    path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as config:
            config.writelines(_config_entry(key, value) for key, value in settings.items())
        yield path
    finally:
        path.unlink(missing_ok=True)


def drop_traces(env: MutableMapping[str, str]) -> None:
    """Remove the variables whose git/curl trace output could log an injected header."""
    for key in [key for key in env if is_trace_variable(key)]:
        del env[key]


def is_trace_variable(name: str) -> bool:
    """Whether ``name`` turns on git or curl trace output."""
    return name.startswith("GIT_TRACE") or name == "GIT_CURL_VERBOSE"


def _config_entry(key: str, value: str) -> str:
    """One ``key = value`` as a git config file section, value quoted."""
    section, _, rest = key.partition(".")
    subsection, _, name = rest.rpartition(".")
    if not section or not name or "\n" in key or "\n" in value:
        raise ValueError(f"not a git config key and one-line value: {key!r}")
    header = f"[{section}]" if not subsection else f'[{section} "{_quoted(subsection)}"]'
    return f'{header}\n\t{name} = "{_quoted(value)}"\n'


def _quoted(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')

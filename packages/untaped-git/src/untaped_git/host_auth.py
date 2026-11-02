"""What a git command reaching a host gets: its credential and untaped's proxy, scoped to it."""

from __future__ import annotations

import base64
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

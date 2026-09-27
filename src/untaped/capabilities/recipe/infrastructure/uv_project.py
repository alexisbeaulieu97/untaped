"""Shared helpers for uv-backed recipe, pack, and hook projects.

Every ``uv`` process run on a pack project (lock checks, locking, hook
workers) gets the allowlisted :func:`uv_environment`, since uv may execute
pack code (build backends, hooks).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

# Environment variables a uv process on a pack project inherits
# (case-insensitive): what uv and Python need, never the caller's tokens.
_UV_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LANGUAGE",
        "TZ",
        "TMPDIR",
        "TEMP",
        "TMP",
        "NETRC",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        # git+ssh dependencies: the agent socket and ssh command, not keys.
        "SSH_AUTH_SOCK",
        "GIT_SSH_COMMAND",
        # Windows process basics.
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    }
)
_UV_ENV_PREFIXES = ("LC_", "UV_", "XDG_")


def uv_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The allowlisted subset of ``environ`` (default ``os.environ``) for uv.

    Keeps paths, locale, temp dirs, ``UV_*``/``XDG_*`` configuration, TLS
    trust, proxies, and the SSH agent socket; drops everything else, such as
    ``GITHUB_TOKEN``, ``VIRTUAL_ENV``, ``PYTHONPATH`` or ``UNTAPED_*``
    credentials.
    """
    source = os.environ if environ is None else environ
    return {
        name: value
        for name, value in source.items()
        if name.upper() in _UV_ENV_NAMES or name.upper().startswith(_UV_ENV_PREFIXES)
    }


def check_lock(project_root: Path) -> None:
    """Assert ``uv.lock`` is up to date with the project (``uv lock --check``)."""
    try:
        result = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=project_root,
            env=uv_environment(),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("uv executable not found for project lock") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if "needs to be updated" in detail.lower():
            message = f"lockfile is stale — run 'uv lock' in {project_root}"
        else:
            message = f"could not verify lockfile freshness in {project_root}"
        if detail:
            message = f"{message}: {detail}"
        raise ValueError(message)


def lock_project(project_root: Path) -> None:
    """Refresh ``uv.lock`` for a uv project."""
    try:
        subprocess.run(
            ["uv", "lock"],
            cwd=project_root,
            env=uv_environment(),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("uv executable not found for project lock") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        message = "failed to create project uv.lock"
        if detail:
            message = f"{message}: {detail}"
        raise ValueError(message) from exc

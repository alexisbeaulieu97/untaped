"""Store API tokens with a tool on this machine, outside ``config.yml``.

``untaped auth set`` hands a token to one of three stores **on stdin**
(never in argv), reads it back, then writes the matching
``<section>.token_command`` so the token is read lazily like any other
command token (:mod:`untaped.auth`). Stores, in the order they are tried:

- ``security``: the macOS login keychain;
- ``secret-tool``: a Secret Service (GNOME Keyring, KWallet), detected by
  a probe lookup rather than by ``DBUS_SESSION_BUS_ADDRESS``, which is often
  set with no service behind it (WSL2, SSH);
- ``pass``: the GPG password store, which works headless with gpg-agent.

Every entry is named ``<profile>/<section>`` under the ``untaped``
service, whichever config file is in use. The tools' output is never shown:
a failure is reported by program and exit status, plus the fix for the
causes worth naming (a locked keychain, a timeout on an unseen unlock prompt).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from untaped.errors import ConfigError

StoreName = Literal["security", "secret-tool", "pass"]
"""A store ``auth set --store`` accepts."""

SERVICE = "untaped"
_PROBE_TIMEOUT_SECONDS = 5.0
_TIMEOUT_SECONDS = 60.0
_ENTRY = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_LOCKED = "User interaction is not allowed"


@dataclass(frozen=True)
class TokenStore:
    """One store: how to write, read and delete the entry ``<profile>/<section>``."""

    name: StoreName

    def read_argv(self, entry: str) -> list[str]:
        """The ``token_command`` that prints the entry's token."""
        if self.name == "security":
            return ["security", "find-generic-password", "-s", SERVICE, "-a", entry, "-w"]
        if self.name == "secret-tool":
            return ["secret-tool", "lookup", "service", SERVICE, "account", entry]
        return ["pass", "show", f"{SERVICE}/{entry}"]

    def describe(self, entry: str) -> str:
        """Where the entry lives, for messages (never the token)."""
        if self.name == "pass":
            return f"pass ({SERVICE}/{entry})"
        return f"{self.name} ({SERVICE} {entry})"

    def save(self, entry: str, token: str) -> None:
        """Write (or overwrite) the entry, then check it reads back as ``token``."""
        if self.name == "security":
            # `security -i` reads commands from stdin; the token goes as hex
            # (`-X`) so no character in it can break security's own parser.
            line = f'add-generic-password -U -s {SERVICE} -a "{entry}" -X {token.encode().hex()}\n'
            _run(["security", "-i"], stdin=line, timeout=_TIMEOUT_SECONDS)
        elif self.name == "secret-tool":
            argv = ["secret-tool", "store", f"--label={SERVICE} {entry}"]
            _run(
                [*argv, "service", SERVICE, "account", entry], stdin=token, timeout=_TIMEOUT_SECONDS
            )
        else:
            argv = ["pass", "insert", "--multiline", "--force", f"{SERVICE}/{entry}"]
            _run(argv, stdin=f"{token}\n", timeout=_TIMEOUT_SECONDS)
        # A fresh run, never the per-process token_command cache, so a failed
        # store cannot hide behind an earlier read.
        stored = _run(self.read_argv(entry), stdin=None, timeout=_TIMEOUT_SECONDS).strip()
        if stored != token:
            raise ConfigError(
                f"{self.name}: the stored token did not read back for {entry}; "
                "nothing was changed in the config"
            )

    def delete(self, entry: str) -> None:
        """Remove the entry."""
        if self.name == "security":
            argv = ["security", "delete-generic-password", "-s", SERVICE, "-a", entry]
        elif self.name == "secret-tool":
            argv = ["secret-tool", "clear", "service", SERVICE, "account", entry]
        else:
            argv = ["pass", "rm", "--force", f"{SERVICE}/{entry}"]
        _run(argv, stdin=None, timeout=_TIMEOUT_SECONDS)

    def usable(self) -> bool:
        """Whether this store works on this machine right now."""
        if shutil.which(self.name) is None:
            return False
        if self.name == "security":
            return _is_macos()
        if self.name == "pass":
            store = os.environ.get("PASSWORD_STORE_DIR") or str(Path.home() / ".password-store")
            return (Path(store) / ".gpg-id").is_file()
        # A missing item exits 1 silently; no Secret Service prints an error
        # (and may exit 1 too), so stderr decides.
        probe = ["secret-tool", "lookup", "service", f"{SERVICE}-probe", "account", "probe"]
        try:
            completed = subprocess.run(
                probe,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except OSError, subprocess.TimeoutExpired:
            return False
        return completed.returncode in (0, 1) and not completed.stderr.strip()


STORES: tuple[TokenStore, ...] = (
    TokenStore("security"),
    TokenStore("secret-tool"),
    TokenStore("pass"),
)


def entry_name(profile: str, section: str) -> str:
    """The store entry for ``section`` in ``profile``: ``<profile>/<section>``."""
    entry = f"{profile}/{section}"
    if not _ENTRY.fullmatch(entry):
        raise ConfigError(
            f"profile {profile!r} cannot name a store entry (letters, digits, '.', '_' and "
            f"'-' only); set {section}.token_command to your own command instead",
            category="invalid",
        )
    return entry


def pick_store(name: StoreName | None = None) -> TokenStore | None:
    """The named store when usable (else an error), or the first usable one."""
    if name is not None:
        store = next(store for store in STORES if store.name == name)
        if not store.usable():
            raise ConfigError(f"{name} is not usable on this machine", category="unavailable")
        return store
    return next((store for store in STORES if store.usable()), None)


def usable_stores() -> list[TokenStore]:
    """Every store that works on this machine, in the order they are tried."""
    return [store for store in STORES if store.usable()]


def preset_entry(argv: Sequence[str] | None) -> tuple[TokenStore, str] | None:
    """The store and entry when ``argv`` is a ``token_command`` untaped wrote."""
    if not argv:
        return None
    for store in STORES:
        for entry in _candidate_entries(store, argv):
            if list(argv) == store.read_argv(entry):
                return store, entry
    return None


def no_store_message(section: str, env: Sequence[str]) -> str:
    """Why nothing was stored, and the routes that remain."""
    routes = [f"set {section}.token_command to a password manager's command"]
    routes.extend(f"export ${name}" for name in env[:1])
    return (
        "no token store is usable on this machine (macOS security, secret-tool with a "
        f"Secret Service, or an initialised pass); {' or '.join(routes)}"
    )


def _candidate_entries(store: TokenStore, argv: Sequence[str]) -> list[str]:
    if store.name == "pass":
        prefix = f"{SERVICE}/"
        last = argv[-1]
        return [last.removeprefix(prefix)] if last.startswith(prefix) else []
    return [argv[i + 1] for i, part in enumerate(argv[:-1]) if part in ("-a", "account")]


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _run(argv: list[str], *, stdin: str | None, timeout: float) -> str:
    """Run a store command and return its stdout; never repeat its output on failure."""
    program = argv[0]
    try:
        completed = subprocess.run(
            argv,
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise ConfigError(f"{program!r} not found on PATH", category="unavailable") from None
    except subprocess.TimeoutExpired:
        raise ConfigError(
            f"{program!r} timed out after {timeout:g}s; it may be waiting on an unlock "
            "prompt on a screen nobody sees (over SSH, unlock the store first)",
            category="unavailable",
        ) from None
    except OSError as exc:
        raise ConfigError(f"{program!r} could not run: {exc.strerror}") from None
    if _LOCKED in completed.stderr or _LOCKED in completed.stdout:
        raise ConfigError(
            "the macOS keychain is locked; run `security unlock-keychain` and try again",
            category="unavailable",
        )
    if completed.returncode != 0:
        raise ConfigError(f"{program!r} exited with status {completed.returncode}")
    return completed.stdout


__all__ = [
    "STORES",
    "StoreName",
    "TokenStore",
    "entry_name",
    "no_store_message",
    "pick_store",
    "preset_entry",
    "usable_stores",
]

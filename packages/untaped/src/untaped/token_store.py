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
service, whichever config file is in use. Their stdout is never shown, nor
is ``security``'s stderr (its ``-i`` input carries the token): a failure is
reported by program and exit status, plus the fix for the causes worth
naming (a locked keychain, a timeout on an unseen unlock prompt). The other
tools explain their own failures on stderr, which never sees the token.
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

from untaped.auth import run_command
from untaped.errors import ConfigError

StoreName = Literal["security", "secret-tool", "pass"]
"""A store ``auth set --store`` accepts."""

SERVICE = "untaped"
_PROBE_TIMEOUT_SECONDS = 5.0
_SEGMENT = r"[A-Za-z0-9_-][A-Za-z0-9._-]*"
_ENTRY = re.compile(f"{_SEGMENT}/{_SEGMENT}")
_LOCKED = "User interaction is not allowed"
_SECURITY_NOT_FOUND = 44
_STDERR_QUOTE = 120

PASS_GPG_HINT = (
    "check that gpg works on its own (`echo test | gpg -e -r <gpg-id> | gpg -d`); usual causes: "
    "no pinentry program installed, GPG_TTY unset (`export GPG_TTY=$(tty)`), or gpg-agent not "
    "running (`gpgconf --launch gpg-agent`)"
)


class PassCommandError(ConfigError):
    """``pass`` failed (usually through gpg); it carries :data:`PASS_GPG_HINT`."""


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
            _run(["security", "-i"], stdin=line)
        elif self.name == "secret-tool":
            argv = ["secret-tool", "store", f"--label={SERVICE} {entry}"]
            _run([*argv, "service", SERVICE, "account", entry], stdin=token)
        else:
            argv = ["pass", "insert", "--multiline", "--force", f"{SERVICE}/{entry}"]
            _run(argv, stdin=f"{token}\n")
        # A fresh run, never the per-process token_command cache, so a failed
        # store cannot hide behind an earlier read.
        if _run(self.read_argv(entry)).stdout.strip() != token:
            raise ConfigError(
                f"{self.name}: the token did not read back from {entry}, which may now hold a "
                "bad value; the config was not changed, so store the token again"
            )

    def gone(self, entry: str) -> bool:
        """Whether the store itself reports the entry missing.

        Only the store's own not-found signal counts, never an exit code an
        unreachable store shares: ``security`` exits 44, ``secret-tool``
        exits 1 with nothing on stderr, and ``pass`` has no ``.gpg`` file.
        """
        if self.name == "pass":
            return not (_password_store() / SERVICE / f"{entry}.gpg").is_file()
        completed = _run(self.read_argv(entry), check=False, capture_stderr=True)
        if self.name == "security":
            return completed.returncode == _SECURITY_NOT_FOUND
        return completed.returncode == 1 and not completed.stderr.strip()

    def delete(self, entry: str) -> None:
        """Remove the entry."""
        if self.name == "security":
            argv = ["security", "delete-generic-password", "-s", SERVICE, "-a", entry]
        elif self.name == "secret-tool":
            argv = ["secret-tool", "clear", "service", SERVICE, "account", entry]
        else:
            argv = ["pass", "rm", "--force", f"{SERVICE}/{entry}"]
        _run(argv)

    def usable(self) -> bool:
        """Whether this store works on this machine right now."""
        if self.name == "pass":
            return pass_problem() is None
        if shutil.which(self.name) is None:
            return False
        if self.name == "security":
            return _is_macos()
        # A missing item exits 1 silently; no Secret Service prints an error
        # (and may exit 1 too), so stderr decides.
        probe = ["secret-tool", "lookup", "service", f"{SERVICE}-probe", "account", "probe"]
        completed = _probe(probe)
        if completed is None:
            return False
        return completed.returncode in (0, 1) and not completed.stderr.strip()


STORES: tuple[TokenStore, ...] = (
    TokenStore("security"),
    TokenStore("secret-tool"),
    TokenStore("pass"),
)


def pass_problem(*, probe_key: bool = True) -> str | None:
    """Why ``pass`` cannot store tokens here, with the fix, or ``None``.

    The store must be initialised and, with ``probe_key``, gpg must hold a
    secret key for one of its recipients. The probe lists keys without
    decrypting (never a prompt), but gpg may create ``~/.gnupg`` and start
    gpg-agent, so a check that calls this is not literally side-effect free.
    A missing pinentry or agent shows only on a real decrypt, which
    :data:`PASS_GPG_HINT` covers.
    """
    if shutil.which("pass") is None:
        return "pass is not installed"
    recipients = _gpg_recipients()
    if not recipients:
        return "the password store is not initialised (run `pass init <gpg-id>`)"
    if not probe_key:
        return None
    if shutil.which("gpg") is None:
        return "gpg is not installed"
    for recipient in recipients:
        completed = _probe(["gpg", "--batch", "--list-secret-keys", "--", recipient])
        if completed is None:
            return f"gpg did not answer within {_PROBE_TIMEOUT_SECONDS:g}s"
        if completed.returncode == 0:
            return None
    return (
        f"gpg holds no secret key for the password store's recipient ({', '.join(recipients)}); "
        "import it with `gpg --import`, or run `pass init <gpg-id>` with a key you hold"
    )


def _probe(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a short usability probe; ``None`` when it cannot run or hangs."""
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None


def _gpg_recipients() -> list[str]:
    """The key ids in the store's ``.gpg-id``, read as ``pass`` does (``#`` starts a comment)."""
    try:
        text = (_password_store() / ".gpg-id").read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return []
    return [ident for line in text.splitlines() if (ident := line.split("#", 1)[0].strip())]


def entry_name(profile: str, section: str) -> str:
    """The store entry for ``section`` in ``profile``: ``<profile>/<section>``."""
    entry = f"{profile}/{section}"
    if not _ENTRY.fullmatch(entry):
        raise ConfigError(
            f"profile {profile!r} cannot name a store entry (letters, digits, '.', '_' and "
            f"'-' only, not starting with '.'); set {section}.token_command to your own "
            "command instead",
            category="invalid",
        )
    return entry


def pick_store(name: StoreName | None = None) -> TokenStore | None:
    """The named store when usable (else an error), or the first usable one."""
    if name is not None:
        store = next(store for store in STORES if store.name == name)
        # An explicit `--store pass` skips the key probe; pass then speaks for itself.
        problem = pass_problem(probe_key=False) if name == "pass" else None
        if problem is not None or (name != "pass" and not store.usable()):
            message = f"{name}: {problem}" if problem else f"{name} is not usable on this machine"
            raise ConfigError(message, category="unavailable")
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
    skipped = pass_problem() if shutil.which("pass") else None
    note = f" ({skipped})" if skipped else ""
    return (
        "no token store is usable on this machine (macOS security, secret-tool with a "
        f"Secret Service, or pass with a gpg key){note}; {' or '.join(routes)}"
    )


def _candidate_entries(store: TokenStore, argv: Sequence[str]) -> list[str]:
    if store.name == "pass":
        prefix = f"{SERVICE}/"
        last = argv[-1]
        return [last.removeprefix(prefix)] if last.startswith(prefix) else []
    return [argv[i + 1] for i, part in enumerate(argv[:-1]) if part in ("-a", "account")]


def _password_store() -> Path:
    return Path(os.environ.get("PASSWORD_STORE_DIR") or Path.home() / ".password-store")


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _run(
    argv: list[str],
    *,
    stdin: str | None = None,
    check: bool = True,
    capture_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a store command; ``check`` raises on a non-zero exit."""
    program = argv[0]
    # `security` is read for the locked-keychain message, and `-i` input
    # (the hex token) must never be echoed back.
    secure = program == "security"
    is_pass = program == "pass"
    completed = run_command(
        argv, label=repr(program), stdin=stdin, capture_stderr=capture_stderr or secure or is_pass
    )
    if secure and (_LOCKED in completed.stderr or _LOCKED in completed.stdout):
        raise ConfigError(
            "the macOS keychain is locked; run `security unlock-keychain` and try again",
            category="unavailable",
        )
    if check and completed.returncode != 0:
        message = f"{program!r} exited with status {completed.returncode}"
        if is_pass:
            # gpg repeats one error per call; the first non-empty line says what failed.
            first = next(
                (line.strip() for line in completed.stderr.splitlines() if line.strip()), ""
            )
            if first:
                message += f": {first[:_STDERR_QUOTE]}"
            raise PassCommandError(message, hint=PASS_GPG_HINT)
        raise ConfigError(message)
    return completed


__all__ = [
    "PASS_GPG_HINT",
    "PassCommandError",
    "StoreName",
    "TokenStore",
    "entry_name",
    "no_store_message",
    "pass_problem",
    "pick_store",
    "preset_entry",
    "usable_stores",
]

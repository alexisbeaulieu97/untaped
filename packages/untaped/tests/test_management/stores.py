"""Fake ``security``, ``secret-tool`` and ``pass`` executables for token-store tests.

Each fake keeps its entries in one JSON file and logs every call's argv and
stdin, so a test can assert a token only ever travelled on stdin. ``PATH`` is
narrowed to the fakes' directory, so a developer's real keychain is never
touched. ``STUB_MODE`` injects a fault: ``fail`` (store exits 1), ``corrupt``
(store keeps a different value), ``locked`` (the macOS locked-keychain
message), ``hang`` (store sleeps past the timeout), ``hang-probe``
(``secret-tool``'s probe sleeps), ``no-service`` (``secret-tool`` reports no
Secret Service), ``no-secret-key`` (the fake ``gpg`` holds no key for the
store), ``gpg-decrypt`` (``pass`` stores, but every read fails with gpg's
repeated decryption errors on stderr).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from untaped import token_store

_STUB = r"""
import json, os, sys, time
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
mode = os.environ.get("STUB_MODE", "")
state_file = Path(os.environ["STUB_STATE"])
data = json.loads(state_file.read_text()) if state_file.exists() else {}
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps({"argv": [name, *args], "stdin": stdin}) + "\n")


def save(key, value, replace=True):
    if not replace and key in data:
        print("The specified item already exists in the keychain.", file=sys.stderr)
        sys.exit(45)
    if mode == "hang":
        time.sleep(30)
    if mode == "fail":
        sys.exit(1)
    if mode == "locked":
        print("SecKeychainItemCreateFromContent: User interaction is not allowed.", file=sys.stderr)
        sys.exit(36)
    data[key] = "garbled" if mode == "corrupt" else value
    state_file.write_text(json.dumps(data))
    if name == "pass":
        gpg_file(key).parent.mkdir(parents=True, exist_ok=True)
        gpg_file(key).write_text("encrypted")


def gpg_file(key):
    return Path(os.environ["PASSWORD_STORE_DIR"]) / f"{key}.gpg"


def show(key, missing=1):
    if key not in data:
        sys.exit(missing)
    print(data[key])


def drop(key):
    if data.pop(key, None) is None:
        sys.exit(1)
    state_file.write_text(json.dumps(data))
    if name == "pass":
        gpg_file(key).unlink()


def after(flag):
    return args[args.index(flag) + 1]


if name == "pass":
    if args[0] == "insert":
        save(args[-1], stdin.rstrip("\n"), replace="--force" in args)
    elif args[0] == "show":
        if mode == "gpg-decrypt":
            for _ in range(2):
                err = "No such file or directory"
                print("gpg: public key decryption failed: " + err, file=sys.stderr)
                print("gpg: decryption failed: " + err, file=sys.stderr)
            sys.exit(2)
        show(args[-1])
    elif args[0] == "rm":
        drop(args[-1])
elif name == "gpg":
    if mode == "no-secret-key":
        print("gpg: error reading key: No secret key", file=sys.stderr)
        sys.exit(2)
elif name == "secret-tool":
    if mode == "hang-probe" and "untaped-probe" in args:
        time.sleep(30)
    if mode == "no-service":
        print("secret-tool: Cannot autolaunch D-Bus without X11 $DISPLAY", file=sys.stderr)
        sys.exit(1)
    key = after("account")
    if args[0] == "store":
        save(key, stdin)
    elif args[0] == "lookup":
        show(key)
    elif args[0] == "clear":
        drop(key)
elif name == "security":
    if args[0] == "-i":
        for line in stdin.splitlines():
            parts = line.split()
            account = parts[parts.index("-a") + 1].strip('"')
            token = bytes.fromhex(parts[parts.index("-X") + 1]).decode()
            save(account, token, replace="-U" in parts)
    elif args[0] == "find-generic-password":
        show(after("-a"), missing=44)
    elif args[0] == "delete-generic-password":
        drop(after("-a"))
"""


@dataclass
class FakeStores:
    """Handle on the fakes: their entries and the calls made to them."""

    state: Path
    log: Path

    def entries(self) -> dict[str, str]:
        return json.loads(self.state.read_text()) if self.state.exists() else {}

    def calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


def install_fake_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *names: str,
    macos: bool = False,
) -> FakeStores:
    """Put fakes for ``names`` (and nothing else) on ``PATH``.

    ``pass`` is initialised and comes with a fake ``gpg`` holding its key.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in (*names, *(("gpg",) if "pass" in names else ())):
        script = bin_dir / name
        script.write_text(f"#!{sys.executable}\n{_STUB}", encoding="utf-8")
        script.chmod(0o755)
    password_store = tmp_path / "password-store"
    password_store.mkdir(exist_ok=True)
    (password_store / ".gpg-id").write_text("test@example.com\n", encoding="utf-8")
    stores = FakeStores(state=tmp_path / "fake-store.json", log=tmp_path / "fake-store.log")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(password_store))
    monkeypatch.setenv("STUB_STATE", str(stores.state))
    monkeypatch.setenv("STUB_LOG", str(stores.log))
    monkeypatch.setattr(token_store, "_is_macos", lambda: macos)
    return stores


__all__ = ["FakeStores", "install_fake_stores"]

"""``untaped auth login`` into this machine's password stores, and reading a store back.

Stores are fakes on a narrowed ``PATH`` (:mod:`core_test_support.commands.stores`);
no test touches a real keychain. The sentinel token must never appear in
any output, error or argv.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, HttpUrl

from core_test_support.commands.stores import FakeStores, install_fake_stores
from core_test_support.commands.support import make_spec, root_group, write_config
from untaped.sdk import Credential, CredentialKey, NeedsSetup, TlsVerify
from untaped.sdk.errors import ConfigError
from untaped.sdk.network import _token_store as token_store
from untaped.sdk.network import auth
from untaped.sdk.network._credential_store import entry_for, reset_test_store, set_test_store
from untaped.sdk.network._credential_store import rows as stored_rows
from untaped.sdk.network.auth import clear_token_cache
from untaped.sdk.testing import CliInvoker, CliResult, ScriptedPromptBackend

pytestmark = pytest.mark.usefixtures("_isolated_config", "_real_stores", "_server")

SENTINEL = "s3cr3t-'quoted' \"and\" back\\slash"


class SvcServer(CredentialKey):
    url: HttpUrl
    verify_tls: TlsVerify = True


class SvcProfile(BaseModel):
    server: Annotated[SvcServer | None, NeedsSetup] = None


TOKEN = Credential("svc", "token", key=SvcServer, env=("SVC_TOKEN",))
KEY = SvcServer(url=HttpUrl("https://svc.example.com"))


def _entry() -> str:
    """The entry ``auth login svc`` files, named for this test's config file."""
    return entry_for(TOKEN, TOKEN.id(KEY))


@pytest.fixture
def _real_stores() -> Iterator[None]:
    """The machine's stores (the fakes on ``PATH``), not the test run's memory store."""
    token = set_test_store(None)
    try:
        yield
    finally:
        reset_test_store(token)


@pytest.fixture
def _server(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "format_version: 2\nsvc: {server: {url: https://svc.example.com}}\n"
    )


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeStores:
    return install_fake_stores(tmp_path, monkeypatch, "pass")


def _login(*args: str, input: str | None = None, backend: Any = None) -> CliResult:
    app = root_group("auth", make_spec("svc", settings=SvcProfile, credentials=(TOKEN,)))
    return CliInvoker().invoke(
        app,
        ["login", "svc", *args],
        input=input,
        prompt_backend=backend,
        interactive=backend is not None,
    )


def _no_leak(result: CliResult, stores: FakeStores) -> None:
    assert SENTINEL not in result.output
    for call in stores.calls():
        assert SENTINEL not in " ".join(call["argv"])  # type: ignore[arg-type]


def _secret() -> str:
    return TOKEN.get(KEY).get_secret_value()


@pytest.mark.parametrize(
    ("store", "macos", "prefix"),
    [("pass", False, "untaped/"), ("secret-tool", False, ""), ("security", True, "")],
)
def test_login_stores_on_stdin_and_reads_back_through_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: str, macos: bool, prefix: str
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, store, macos=macos)
    result = _login(input=f"  {SENTINEL}\n")
    assert result.exit_code == 0, result.output
    assert stores.entries() == {f"{prefix}{_entry()}": SENTINEL}
    [row] = stored_rows()
    assert (row.backend, row.entry) == (store, _entry())
    _no_leak(result, stores)
    clear_token_cache()
    assert _secret() == SENTINEL


def test_security_gets_the_token_as_hex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "security", macos=True)
    assert _login(input=SENTINEL).exit_code == 0
    store_call = next(call for call in stores.calls() if call["argv"] == ["security", "-i"])
    assert SENTINEL.encode().hex() in str(store_call["stdin"])
    assert SENTINEL not in str(store_call["stdin"])


def test_login_prefers_the_first_usable_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "secret-tool", "pass")
    monkeypatch.setenv("STUB_MODE", "no-service")
    result = _login(input="tok")
    assert result.exit_code == 0, result.output
    [row] = stored_rows()
    assert row.backend == "pass", "a Secret Service probe that errors falls through to pass"


def test_login_with_a_forced_store_that_is_unusable_fails(stores: FakeStores) -> None:
    result = _login("--store", "security", input="tok")
    assert result.exit_code == 5
    assert "security is not usable on this machine" in result.stderr
    assert stored_rows() == []


def test_login_prompts_for_the_token(stores: FakeStores) -> None:
    backend = ScriptedPromptBackend(secrets=[SENTINEL])
    result = _login(backend=backend)
    assert result.exit_code == 0, result.output
    assert backend.calls == [("secret", "svc token")]
    assert stores.entries() == {f"untaped/{_entry()}": SENTINEL}
    _no_leak(result, stores)


@pytest.mark.parametrize(
    ("store", "macos"), [("pass", False), ("secret-tool", False), ("security", True)]
)
def test_login_overwrites_on_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: str, macos: bool
) -> None:
    # Each fake refuses to replace an entry unless asked to (-U, --force).
    stores = install_fake_stores(tmp_path, monkeypatch, store, macos=macos)
    assert _login(input="first").exit_code == 0
    result = _login(input="second")
    assert result.exit_code == 0, result.output
    assert list(stores.entries().values()) == ["second"]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("fail", "'pass' exited with status 1"),
        ("corrupt", "the token did not read back from "),
    ],
)
def test_a_failed_store_files_nothing(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch, mode: str, message: str
) -> None:
    monkeypatch.setenv("STUB_MODE", mode)
    result = _login(input=SENTINEL)
    assert result.exit_code != 0
    assert message in result.stderr
    assert stored_rows() == []
    _no_leak(result, stores)


def test_a_locked_keychain_names_the_unlock_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "security", macos=True)
    monkeypatch.setenv("STUB_MODE", "locked")
    result = _login(input=SENTINEL)
    assert result.exit_code == 5
    assert "run `security unlock-keychain`" in result.stderr
    assert "SecKeychainItemCreateFromContent" not in result.stderr
    assert stored_rows() == []
    _no_leak(result, stores)


def test_a_hung_store_times_out_with_the_likely_cause(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "hang")
    monkeypatch.setattr(auth, "_TIMEOUT_SECONDS", 0.5)
    result = _login(input="tok")
    assert result.exit_code == 5
    assert "timed out after 0.5s" in result.stderr
    assert "unlock prompt" in result.stderr
    assert "`gpgconf --kill gpg-agent` closes a passphrase prompt left open" in result.stderr
    assert stored_rows() == []


def test_read_back_bypasses_the_token_cache(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _login(input="first").exit_code == 0
    clear_token_cache()
    assert _secret() == "first"  # now cached for this process
    monkeypatch.setenv("STUB_MODE", "corrupt")
    result = _login(input="first")
    assert result.exit_code == 4, "a cached read must not mask the garbled store"


def test_no_usable_store_names_the_command_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch)
    result = _login(input="tok")
    assert result.exit_code == 4, result.output
    assert "no credential store answers here" in result.stderr
    assert "untaped auth login svc --token-command" in result.stderr
    assert stored_rows() == []


def test_an_uninitialised_password_store_is_not_usable(stores: FakeStores, tmp_path: Path) -> None:
    (tmp_path / "password-store" / ".gpg-id").unlink()
    result = _login(input="tok")
    assert result.exit_code == 4
    assert "the password store is not initialised" in result.stderr


def test_a_password_store_without_a_secret_key_is_not_usable_and_says_why(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "no-secret-key")
    result = _login(input="tok")
    assert result.exit_code == 4
    assert "holds no secret key" in result.stderr and "pass init" in result.stderr
    assert "pinentry" not in result.stderr
    assert stores.entries() == {}
    forced = _login("--store", "pass", input="tok")
    assert forced.exit_code == 0, "an explicit --store pass skips the key probe"


def test_an_explicit_pass_on_an_uninitialised_store_says_so(
    stores: FakeStores, tmp_path: Path
) -> None:
    (tmp_path / "password-store" / ".gpg-id").unlink()
    result = _login("--store", "pass", input="tok")
    assert result.exit_code == 5
    assert "pass: the password store is not initialised" in result.stderr


@pytest.mark.parametrize(
    "gpg_id", ["test@example.com # laptop\n", "# note\n\nother@example.com\ntest@example.com\n"]
)
def test_gpg_id_is_read_like_pass_does(stores: FakeStores, tmp_path: Path, gpg_id: str) -> None:
    (tmp_path / "password-store" / ".gpg-id").write_text(gpg_id, encoding="utf-8")
    assert token_store.pass_problem() is None


def test_pass_without_gpg_is_not_usable(stores: FakeStores, tmp_path: Path) -> None:
    (tmp_path / "fake-bin" / "gpg").unlink()
    assert token_store.pass_problem() == "gpg is not installed"


def test_a_hung_gpg_probe_is_not_usable(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "hang-gpg")
    monkeypatch.setattr(token_store, "_PROBE_TIMEOUT_SECONDS", 0.5)
    assert "did not answer" in (token_store.pass_problem() or "")
    assert _login(input="tok").exit_code == 4


def test_a_hung_secret_service_probe_is_not_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    monkeypatch.setenv("STUB_MODE", "hang-probe")
    monkeypatch.setattr(token_store, "_PROBE_TIMEOUT_SECONDS", 0.5)
    result = _login(input="tok")
    assert result.exit_code == 4
    assert "no credential store answers here" in result.stderr


def test_login_stops_before_the_prompt_when_gpg_cannot_decrypt(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "gpg-roundtrip")
    backend = ScriptedPromptBackend(secrets=[SENTINEL])
    result = _login(backend=backend)
    assert result.exit_code == 5
    assert (
        "gpg decrypt failed for the pass store: gpg: public key decryption failed" in result.stderr
    )
    assert result.stderr.count("decryption failed") == 1 and "pinentry" in result.stderr
    assert backend.calls == [], "the store is tested before the token is asked for"
    assert not [c for c in stores.calls() if c["argv"][0] == "pass"], "pass never ran"
    assert stored_rows() == []


def test_login_without_a_terminal_stops_on_a_locked_gpg_key(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The harness gives no terminal on stdin or stderr, as an agent does: gpg is told not to ask.
    monkeypatch.setenv("STUB_MODE", "locked-key")
    result = _login(input=SENTINEL)
    assert result.exit_code == 5
    assert "gpg decrypt for the pass store: the password store is locked" in result.stderr
    assert "echo test | gpg -e -r test@example.com | gpg -d > /dev/null" in result.stderr
    assert not [c for c in stores.calls() if c["argv"][0] == "pass"], "pass never ran"
    assert stored_rows() == []
    _no_leak(result, stores)


def test_reading_a_locked_pass_token_without_a_terminal_names_the_unlock_command(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _login("--store", "pass", input=SENTINEL).exit_code == 0
    clear_token_cache()
    monkeypatch.setenv("STUB_MODE", "locked-key")
    # Read outside the CLI harness: no terminal on stdin or stderr, whatever runs pytest.
    monkeypatch.setattr("sys.stdin", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    with pytest.raises(ConfigError) as caught:
        _secret()
    assert caught.value.exit_code == 5
    assert caught.value.hint is not None
    assert f"`pass show untaped/{_entry()} > /dev/null`" in caught.value.hint


def test_reading_a_pass_token_summarises_gpg_and_hints_once(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "gpg-decrypt")
    token = auth.CommandToken(["pass", "show", "untaped/svc/token/x"], setting="the svc token")
    with pytest.raises(ConfigError) as caught:
        token.get_secret_value()
    assert str(caught.value) == (
        "the svc token: 'pass' exited with status 2: "
        "gpg: public key decryption failed: No such file or directory"
    )
    assert caught.value.hint == auth.PASS_GPG_HINT


def test_a_secret_service_that_cannot_store_stops_login_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    monkeypatch.setenv("STUB_MODE", "fail")
    result = _login(input=SENTINEL)
    assert result.exit_code != 0
    assert "'secret-tool' exited with status 1" in result.stderr
    assert stored_rows() == []
    assert stores.entries() == {}


def test_an_untrusted_key_is_named_with_the_trust_fix(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "untrusted-key")
    result = _login(input=SENTINEL)
    assert result.exit_code == 5
    assert "gpg encrypt failed for the pass store: gpg: [stdin]: encryption failed" in result.stderr
    assert "gpg --edit-key" in result.stderr and "pinentry" not in result.stderr
    assert "log in with --token-command and your own command" in result.stderr


def test_the_preflight_runs_gpg_with_the_options_pass_would(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PASSWORD_STORE_GPG_OPTS", "--homedir /elsewhere")
    assert _login(input="tok").exit_code == 0
    gpg_calls = [c["argv"] for c in stores.calls() if c["argv"][0] == "gpg"]
    assert gpg_calls and all(
        "--homedir" in argv and "--no-encrypt-to" in argv for argv in gpg_calls
    )


def test_a_missing_pass_entry_gets_no_gpg_advice(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "not-in-store")
    token = auth.CommandToken(["pass", "show", "untaped/svc/token/nope"], setting="the svc token")
    with pytest.raises(ConfigError) as caught:
        token.get_secret_value()
    assert str(caught.value).endswith("Error: untaped/svc/token/nope is not in the password store.")
    assert caught.value.hint is None
    store = token_store.TokenStore("pass")
    monkeypatch.setenv("STUB_MODE", "protected")
    with pytest.raises(ConfigError, match="protected entry") as protected:
        store.delete(_entry())
    assert protected.value.hint is None


def test_gpg_that_cannot_run_is_reported_with_no_entry_left(
    stores: FakeStores, tmp_path: Path
) -> None:
    (tmp_path / "fake-bin" / "gpg").chmod(0o644)
    with pytest.raises(ConfigError, match="gpg could not run"):
        token_store.TokenStore("pass").preflight()
    assert stores.entries() == {}


def test_a_secret_service_that_garbles_the_test_value_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    monkeypatch.setenv("STUB_MODE", "corrupt")
    result = _login(input=SENTINEL)
    assert result.exit_code == 5
    assert "a test value did not read back" in result.stderr
    assert stores.entries() == {}


def test_a_hung_gpg_round_trip_times_out_before_any_token_moves(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "hang-roundtrip")
    monkeypatch.setattr(token_store, "_ROUND_TRIP_SECONDS", 0.5)
    result = _login(input=SENTINEL)
    assert result.exit_code == 5
    assert "gpg encrypt timed out" in result.stderr and "gpgconf --kill" in result.stderr
    assert stored_rows() == [] and stores.entries() == {}


def test_a_secret_tool_test_entry_that_cannot_be_removed_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    monkeypatch.setenv("STUB_MODE", "no-clear")
    result = _login(input=SENTINEL)
    assert result.exit_code == 5
    assert "could not remove its test entry" in result.stderr
    assert stored_rows() == []


def test_a_working_secret_service_leaves_no_preflight_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    assert _login(input="tok").exit_code == 0
    assert list(stores.entries().values()) == ["tok"]


def test_a_pass_that_cannot_decrypt_is_reported_once_with_the_fix(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "gpg-decrypt")
    result = _login(input=SENTINEL)
    assert result.exit_code != 0
    assert result.stderr.count("decryption failed") == 1, "gpg's repeated stderr is summarised"
    assert "pinentry" in result.stderr and "GPG_TTY" in result.stderr
    assert stored_rows() == []
    _no_leak(result, stores)

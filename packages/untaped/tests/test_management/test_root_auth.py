"""``untaped auth``: store tokens outside ``config.yml`` and move plaintext ones there.

Stores are fakes on a narrowed ``PATH`` (:mod:`test_management.stores`);
no test touches a real keychain. The sentinel token must never appear in
any output, error or argv.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from test_management.stores import FakeStores, install_fake_stores
from test_management.support import make_spec, write_config
from untaped import auth, bootstrap, token_store
from untaped.auth import clear_token_cache
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError
from untaped.sdk import TokenCommand, TokenSources
from untaped.settings import get_config_section
from untaped.testing import CliResult, ScriptedPromptBackend, invoke_cli, provider_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

SENTINEL = "s3cr3t-'quoted' \"and\" back\\slash"


class SvcProfile(BaseModel):
    """Token-bearing service double (section ``svc``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("SVC_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


class OtherProfile(BaseModel):
    """A second token-bearing service double (section ``other``)."""

    token_sources: ClassVar[TokenSources] = TokenSources()

    token: SecretStr | None = None
    token_command: TokenCommand = None


class PlainProfile(BaseModel):
    """A service without ``token_command`` (section ``plain``): not ``auth``'s."""

    token: SecretStr | None = None


def _auth(*args: str, input: str | None = None, backend: Any = None) -> CliResult:
    specs = (
        make_spec("svc", profile_model=SvcProfile),
        make_spec("other", profile_model=OtherProfile),
        make_spec("plain", profile_model=PlainProfile),
    )
    root = bootstrap.build_root_app(candidates=tuple(provider_candidate(s) for s in specs))
    return invoke_cli(
        root.meta,
        ["auth", *args],
        input=input,
        interactive=backend is not None,
        prompt_backend=backend,
    )


def _config(path: Path) -> dict[str, Any]:
    return read_config_dict(path)


def _no_leak(result: CliResult, stores: FakeStores) -> None:
    assert SENTINEL not in result.output
    for call in stores.calls():
        assert SENTINEL not in " ".join(call["argv"])  # type: ignore[arg-type]


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeStores:
    return install_fake_stores(tmp_path, monkeypatch, "pass")


# --- auth set -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("store", "macos", "command", "entry"),
    [
        ("pass", False, ["pass", "show", "untaped/default/svc"], "untaped/default/svc"),
        (
            "secret-tool",
            False,
            ["secret-tool", "lookup", "service", "untaped", "account", "default/svc"],
            "default/svc",
        ),
        (
            "security",
            True,
            ["security", "find-generic-password", "-s", "untaped", "-a", "default/svc", "-w"],
            "default/svc",
        ),
    ],
)
def test_set_stores_on_stdin_and_writes_the_preset(
    _isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: str,
    macos: bool,
    command: list[str],
    entry: str,
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, store, macos=macos)
    write_config(_isolated_config, "profiles:\n  default:\n    svc:\n      token: old\n")
    result = _auth("set", "svc", "--stdin", "--format", "json", input=f"  {SENTINEL}\n")
    assert result.exit_code == 0, result.output
    assert _config(_isolated_config)["profiles"]["default"]["svc"] == {"token_command": command}
    assert stores.entries() == {entry: SENTINEL}
    assert json.loads(result.stdout)["action"] == "stored"
    assert "svc.token_command set in profile default" in result.stderr
    _no_leak(result, stores)
    # The written command serves the token at run time.
    clear_token_cache()
    token = get_config_section("svc", SvcProfile).token
    assert token is not None
    assert token.get_secret_value() == SENTINEL


def test_security_gets_the_token_as_hex(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "security", macos=True)
    assert _auth("set", "svc", "--stdin", input=SENTINEL).exit_code == 0
    store_call = next(call for call in stores.calls() if call["argv"] == ["security", "-i"])
    assert SENTINEL.encode().hex() in str(store_call["stdin"])
    assert SENTINEL not in str(store_call["stdin"])


def test_set_prefers_the_first_usable_store(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "secret-tool", "pass")
    monkeypatch.setenv("STUB_MODE", "no-service")
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 0, result.output
    command = _config(_isolated_config)["profiles"]["default"]["svc"]["token_command"]
    assert command[0] == "pass", "a Secret Service probe that errors falls through to pass"


def test_set_with_a_forced_store_that_is_unusable_fails(
    _isolated_config: Path, stores: FakeStores
) -> None:
    result = _auth("set", "svc", "--stdin", "--store", "security", input="tok")
    assert result.exit_code == 5
    assert "security is not usable on this machine" in result.stderr
    assert not _isolated_config.exists()


def test_set_prompts_for_the_token(_isolated_config: Path, stores: FakeStores) -> None:
    backend = ScriptedPromptBackend(secrets=[SENTINEL])
    result = _auth("set", "svc", backend=backend)
    assert result.exit_code == 0, result.output
    assert backend.calls == [("secret", "svc token")]
    assert stores.entries() == {"untaped/default/svc": SENTINEL}
    _no_leak(result, stores)


def test_set_without_a_terminal_or_stdin_refuses(
    _isolated_config: Path, stores: FakeStores
) -> None:
    result = _auth("set", "svc")
    assert result.exit_code == 2
    assert "pipe it with --stdin" in result.stderr
    assert stores.entries() == {}


@pytest.mark.parametrize("payload", ["", "  \n", "one\ntwo\n"])
def test_set_rejects_empty_or_multiline_stdin(
    _isolated_config: Path, stores: FakeStores, payload: str
) -> None:
    result = _auth("set", "svc", "--stdin", input=payload)
    assert result.exit_code == 1
    assert stores.entries() == {}
    assert not _isolated_config.exists()


@pytest.mark.parametrize(
    ("store", "macos"), [("pass", False), ("secret-tool", False), ("security", True)]
)
def test_set_overwrites_on_rotation(
    _isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: str,
    macos: bool,
) -> None:
    # Each fake refuses to replace an entry unless asked to (-U, --force).
    stores = install_fake_stores(tmp_path, monkeypatch, store, macos=macos)
    assert _auth("set", "svc", "--stdin", input="first").exit_code == 0
    result = _auth("set", "svc", "--stdin", input="second")
    assert result.exit_code == 0, result.output
    assert list(stores.entries().values()) == ["second"]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("fail", "'pass' exited with status 1"),
        ("corrupt", "the token did not read back from default/svc"),
    ],
)
def test_a_failed_store_changes_nothing(
    _isolated_config: Path,
    stores: FakeStores,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    message: str,
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    svc:\n      token: old\n")
    monkeypatch.setenv("STUB_MODE", mode)
    result = _auth("set", "svc", "--stdin", input=SENTINEL)
    assert result.exit_code != 0
    assert message in result.stderr
    assert _config(_isolated_config)["profiles"]["default"]["svc"] == {"token": "old"}
    _no_leak(result, stores)


def test_a_locked_keychain_names_the_unlock_command(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "security", macos=True)
    monkeypatch.setenv("STUB_MODE", "locked")
    result = _auth("set", "svc", "--stdin", input=SENTINEL)
    assert result.exit_code == 5
    assert "run `security unlock-keychain`" in result.stderr
    assert "SecKeychainItemCreateFromContent" not in result.stderr
    assert not _isolated_config.exists()
    _no_leak(result, stores)


def test_a_hung_store_times_out_with_the_likely_cause(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "hang")
    monkeypatch.setattr(auth, "_TIMEOUT_SECONDS", 0.5)
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 5
    assert "timed out after 0.5s" in result.stderr
    assert "unlock prompt" in result.stderr
    assert not _isolated_config.exists()


def test_read_back_bypasses_the_token_cache(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _auth("set", "svc", "--stdin", input="first").exit_code == 0
    clear_token_cache()
    token = get_config_section("svc", SvcProfile).token
    assert token is not None
    assert token.get_secret_value() == "first"  # now cached for this process
    monkeypatch.setenv("STUB_MODE", "corrupt")
    result = _auth("set", "svc", "--stdin", input="first")
    assert result.exit_code == 4, "a cached read must not mask the garbled store"


def test_no_usable_store_names_the_other_routes(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch)
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 5
    assert "no token store is usable on this machine" in result.stderr
    assert "svc.token_command" in result.stderr
    assert "$SVC_TOKEN" in result.stderr
    assert not _isolated_config.exists()


def test_an_uninitialised_password_store_is_not_usable(
    _isolated_config: Path, stores: FakeStores, tmp_path: Path
) -> None:
    (tmp_path / "password-store" / ".gpg-id").unlink()
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 5
    assert "no token store is usable" in result.stderr


def test_a_password_store_without_a_secret_key_is_not_usable(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "no-secret-key")
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 5
    assert "no token store is usable" in result.stderr
    assert stores.entries() == {}


def test_a_skipped_pass_says_why(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "no-secret-key")
    result = _auth("set", "svc", "--stdin", input="tok")
    assert "holds no secret key" in result.stderr and "pass init" in result.stderr
    assert "pinentry" not in result.stderr
    forced = _auth("set", "svc", "--stdin", "--store", "pass", input="tok")
    assert forced.exit_code == 0, "an explicit --store pass skips the key probe"


def test_an_explicit_pass_on_an_uninitialised_store_says_so(
    _isolated_config: Path, stores: FakeStores, tmp_path: Path
) -> None:
    (tmp_path / "password-store" / ".gpg-id").unlink()
    result = _auth("set", "svc", "--stdin", "--store", "pass", input="tok")
    assert result.exit_code == 5
    assert "pass: the password store is not initialised" in result.stderr


@pytest.mark.parametrize(
    "gpg_id", ["test@example.com # laptop\n", "# note\n\nother@example.com\ntest@example.com\n"]
)
def test_gpg_id_is_read_like_pass_does(
    _isolated_config: Path, stores: FakeStores, tmp_path: Path, gpg_id: str
) -> None:
    (tmp_path / "password-store" / ".gpg-id").write_text(gpg_id, encoding="utf-8")
    assert token_store.pass_problem() is None


def test_pass_without_gpg_is_not_usable(
    _isolated_config: Path, stores: FakeStores, tmp_path: Path
) -> None:
    (tmp_path / "fake-bin" / "gpg").unlink()
    assert token_store.pass_problem() == "gpg is not installed"


def test_a_hung_gpg_probe_is_not_usable(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "hang-gpg")
    monkeypatch.setattr(token_store, "_PROBE_TIMEOUT_SECONDS", 0.5)
    assert "did not answer" in (token_store.pass_problem() or "")
    assert _auth("set", "svc", "--stdin", input="tok").exit_code == 5


def test_set_stops_before_the_prompt_when_gpg_cannot_decrypt(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "gpg-roundtrip")
    result = _auth("set", "svc", "--stdin", input=SENTINEL)
    assert result.exit_code == 5
    assert (
        "gpg cannot decrypt for the pass store: gpg: public key decryption failed" in result.stderr
    )
    assert result.stderr.count("decryption failed") == 1 and "pinentry" in result.stderr
    assert not [c for c in stores.calls() if c["argv"][0] == "pass"], "pass never ran"
    assert not _isolated_config.exists()
    _no_leak(result, stores)


def test_migrate_stops_before_moving_anything_when_gpg_cannot_decrypt(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _PLAINTEXT)
    monkeypatch.setenv("STUB_MODE", "gpg-roundtrip")
    before = _isolated_config.read_text()
    result = _auth("migrate")
    assert result.exit_code == 5
    assert result.stderr.count("pinentry") == 1
    assert _isolated_config.read_text() == before
    assert stores.entries() == {}
    assert _auth("migrate", "--dry-run").exit_code == 0, "a plan needs no preflight"


def test_reading_a_pass_token_summarises_gpg_and_hints_once(
    stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "gpg-decrypt")
    token = auth.CommandToken(["pass", "show", "untaped/default/svc"], section="svc")
    with pytest.raises(ConfigError) as caught:
        token.get_secret_value()
    assert str(caught.value) == (
        "svc.token_command: 'pass' exited with status 2: "
        "gpg: public key decryption failed: No such file or directory"
    )
    assert caught.value.hint == auth.PASS_GPG_HINT


def test_a_secret_service_that_cannot_store_stops_set_early(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    monkeypatch.setenv("STUB_MODE", "fail")
    result = _auth("set", "svc", "--stdin", input=SENTINEL)
    assert result.exit_code != 0
    assert "'secret-tool' exited with status 1" in result.stderr
    assert not _isolated_config.exists()
    assert stores.entries() == {}


def test_a_hung_gpg_round_trip_times_out_before_any_token_moves(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_MODE", "hang-roundtrip")
    monkeypatch.setattr(token_store, "_ROUND_TRIP_SECONDS", 0.5)
    result = _auth("set", "svc", "--stdin", input=SENTINEL)
    assert result.exit_code == 5
    assert "gpg timed out" in result.stderr and "pinentry" in result.stderr
    assert not _isolated_config.exists() and stores.entries() == {}


def test_a_secret_tool_test_entry_that_cannot_be_removed_is_reported(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    monkeypatch.setenv("STUB_MODE", "no-clear")
    result = _auth("set", "svc", "--stdin", input=SENTINEL)
    assert result.exit_code == 5
    assert "could not remove its test entry" in result.stderr
    assert not _isolated_config.exists()


def test_a_working_secret_service_leaves_no_preflight_entry(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    assert _auth("set", "svc", "--stdin", input="tok").exit_code == 0
    assert list(stores.entries().values()) == ["tok"]


def test_a_pass_that_cannot_decrypt_is_reported_once_with_the_fix(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    svc:\n      token: old\n")
    monkeypatch.setenv("STUB_MODE", "gpg-decrypt")
    result = _auth("set", "svc", "--stdin", input=SENTINEL)
    assert result.exit_code != 0
    assert result.stderr.count("decryption failed") == 1, "gpg's repeated stderr is summarised"
    assert "pinentry" in result.stderr and "GPG_TTY" in result.stderr
    assert _config(_isolated_config)["profiles"]["default"]["svc"] == {"token": "old"}
    _no_leak(result, stores)


def test_set_in_a_named_profile_names_the_entry_after_it(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n  work: {}\n")
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 0
    root = bootstrap.build_root_app(
        candidates=(provider_candidate(make_spec("svc", profile_model=SvcProfile)),)
    )
    result = invoke_cli(
        root.meta, ["--profile", "work", "auth", "set", "svc", "--stdin"], input="w"
    )
    assert result.exit_code == 0, result.output
    assert stores.entries() == {"untaped/default/svc": "tok", "untaped/work/svc": "w"}


def test_set_refuses_when_default_holds_a_plaintext_token(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    svc:\n      token: shared\n  work: {}\nactive: work\n",
    )
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 4
    assert "svc.token is set in profile default" in result.stderr
    assert "untaped auth migrate" in result.stderr
    assert stores.entries() == {}


def test_set_warns_when_the_env_override_shadows_the_store(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_SVC__TOKEN", "env")
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 0
    assert "$UNTAPED_SVC__TOKEN is set and wins over the stored token" in result.stderr


@pytest.mark.parametrize("section", ["plain", "nope"])
def test_set_rejects_a_section_without_token_command(
    _isolated_config: Path, stores: FakeStores, section: str
) -> None:
    result = _auth("set", section, "--stdin", input="tok")
    assert result.exit_code == 1
    assert f"unknown token section {section!r}; known: other, svc" in result.stderr


def test_set_rejects_a_profile_name_that_cannot_name_an_entry(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n  'my work': {}\nactive: my work\n")
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 1
    assert "profile 'my work' cannot name a store entry" in result.stderr


# --- auth unset ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("store", "macos"), [("pass", False), ("secret-tool", False), ("security", True)]
)
def test_unset_deletes_the_entry_and_the_command(
    _isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: str,
    macos: bool,
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, store, macos=macos)
    assert _auth("set", "svc", "--stdin", "--store", store, input="tok").exit_code == 0
    preview = _auth("unset", "svc", "--dry-run", "--format", "json")
    assert json.loads(preview.stdout)["action"] == "planned"
    assert len(stores.entries()) == 1
    result = _auth("unset", "svc", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "deleted"
    assert stores.entries() == {}
    assert _config(_isolated_config)["profiles"]["default"] == {}


@pytest.mark.parametrize(
    ("store", "macos", "command"),
    [
        ("pass", False, "[pass, show, untaped/default/svc]"),
        ("secret-tool", False, "[secret-tool, lookup, service, untaped, account, default/svc]"),
        ("security", True, "[security, find-generic-password, -s, untaped, -a, default/svc, -w]"),
    ],
)
def test_unset_cleans_up_when_the_entry_is_already_gone(
    _isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: str,
    macos: bool,
    command: str,
) -> None:
    install_fake_stores(tmp_path, monkeypatch, store, macos=macos)
    write_config(
        _isolated_config, f"profiles:\n  default:\n    svc:\n      token_command: {command}\n"
    )
    result = _auth("unset", "svc", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "gone"
    assert "the svc token was already gone from" in result.stderr
    assert "deleted" not in result.stderr
    assert _config(_isolated_config)["profiles"]["default"] == {}


def test_unset_keeps_everything_when_the_store_is_unreachable(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    assert _auth("set", "svc", "--stdin", input="tok").exit_code == 0
    # No Secret Service: secret-tool exits 1, as for a missing item, but says why.
    monkeypatch.setenv("STUB_MODE", "no-service")
    result = _auth("unset", "svc", "--yes")
    assert result.exit_code == 4
    assert "'secret-tool' exited with status 1" in result.stderr
    assert stores.entries() == {"default/svc": "tok"}
    assert "token_command" in _config(_isolated_config)["profiles"]["default"]["svc"]


def test_unset_when_the_store_tool_is_gone_keeps_the_command(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch)
    command = "[secret-tool, lookup, service, untaped, account, default/svc]"
    write_config(
        _isolated_config, f"profiles:\n  default:\n    svc:\n      token_command: {command}\n"
    )
    result = _auth("unset", "svc", "--yes")
    assert result.exit_code == 4
    assert "'secret-tool' not found on PATH" in result.stderr
    assert "token_command" in _config(_isolated_config)["profiles"]["default"]["svc"]


def test_a_hung_secret_service_probe_is_not_usable(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "secret-tool")
    monkeypatch.setenv("STUB_MODE", "hang-probe")
    monkeypatch.setattr(token_store, "_PROBE_TIMEOUT_SECONDS", 0.5)
    result = _auth("set", "svc", "--stdin", input="tok")
    assert result.exit_code == 5
    assert "no token store is usable" in result.stderr


@pytest.mark.parametrize("profile", ["..", ".hidden"])
def test_an_entry_segment_cannot_start_with_a_dot(profile: str) -> None:
    with pytest.raises(ConfigError, match=r"not starting with '\.'"):
        token_store.entry_name(profile, "svc")


def test_preset_entry_ignores_empty_and_foreign_commands() -> None:
    assert token_store.preset_entry(None) is None
    assert token_store.preset_entry(["pass", "show", "elsewhere/x"]) is None
    assert token_store.preset_entry(["security", "-a", "default/svc"]) is None


def test_unset_without_yes_or_terminal_refuses(_isolated_config: Path, stores: FakeStores) -> None:
    assert _auth("set", "svc", "--stdin", input="tok").exit_code == 0
    result = _auth("unset", "svc")
    assert result.exit_code == 2
    assert stores.entries() == {"untaped/default/svc": "tok"}


def test_unset_leaves_an_inherited_command_alone(
    _isolated_config: Path, stores: FakeStores
) -> None:
    assert _auth("set", "svc", "--stdin", input="tok").exit_code == 0
    config = _config(_isolated_config)
    config["profiles"]["work"] = {}
    config["active"] = "work"
    write_config(_isolated_config, json.dumps(config))
    result = _auth("unset", "svc", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "unchanged"
    assert "inherited from profile default; profile work is unchanged" in result.stderr
    assert "untaped --profile default auth unset svc" in result.stderr
    assert stores.entries() == {"untaped/default/svc": "tok"}


def test_unset_with_nothing_set_is_a_no_op(_isolated_config: Path, stores: FakeStores) -> None:
    result = _auth("unset", "svc", "--yes", "--format", "json")
    assert result.exit_code == 0
    assert "svc.token_command is not set in profile default" in result.stderr


def test_unset_leaves_a_foreign_command_alone(_isolated_config: Path, stores: FakeStores) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    svc:\n      token_command: [op, read, x]\n"
    )
    result = _auth("unset", "svc", "--yes")
    assert result.exit_code == 1
    assert "was not written by untaped" in result.stderr
    assert _config(_isolated_config)["profiles"]["default"]["svc"] == {
        "token_command": ["op", "read", "x"]
    }


# --- auth status --------------------------------------------------------------


def test_status_names_every_source_without_running_commands(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n"
        "  default:\n"
        "    svc:\n      token_command: [pass, show, untaped/default/svc]\n"
        "    other:\n      token: plain-secret\n"
        "  work:\n"
        "    svc:\n      token_command: [op, read, x]\n",
    )
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    result = _auth("status", "--format", "json")
    assert result.exit_code == 0, result.output
    assert [c["argv"][0] for c in stores.calls()] == ["gpg"], "only the usability probe runs"
    rows = {(row["profile"], row["section"]): row for row in json.loads(result.stdout)}
    assert rows["default", "svc"]["source"] == "pass (untaped/default/svc)"
    assert rows["default", "other"] == {
        "profile": "default",
        "section": "other",
        "source": "config.yml (plain text)",
        "set_in": "default",
    }
    assert rows["work", "svc"]["source"] == "token_command"
    assert rows["work", "other"]["set_in"] == "default"
    assert "token stores usable here: pass" in result.stderr
    assert "plain-secret" not in result.output


def test_status_reports_env_sources(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch)
    monkeypatch.setenv("UNTAPED_SVC__TOKEN", "x")
    monkeypatch.setenv("SVC_TOKEN", "y")
    result = _auth("status", "--format", "json")
    rows = {row["section"]: row for row in json.loads(result.stdout)}
    assert rows["svc"]["source"] == "$UNTAPED_SVC__TOKEN"
    assert rows["other"]["source"] == "none"
    assert "token stores usable here: none" in result.stderr


# --- auth migrate -------------------------------------------------------------


_PLAINTEXT = (
    "profiles:\n"
    "  default:\n"
    "    svc:\n      base_url: https://svc\n      token: d-svc\n"
    "  work:\n"
    "    svc:\n      token: w-svc\n"
    "    other:\n      token: w-other\n"
)


def test_migrate_moves_every_plaintext_token(_isolated_config: Path, stores: FakeStores) -> None:
    write_config(_isolated_config, _PLAINTEXT)
    preview = _auth("migrate", "--dry-run", "--format", "json")
    assert preview.exit_code == 0, preview.output
    assert {row["action"] for row in json.loads(preview.stdout)} == {"planned"}
    assert stores.entries() == {}
    result = _auth("migrate", "--format", "json")
    assert result.exit_code == 0, result.output
    assert [
        (row["profile"], row["section"], row["action"]) for row in json.loads(result.stdout)
    ] == [
        ("default", "svc", "moved"),
        ("work", "other", "moved"),
        ("work", "svc", "moved"),
    ]
    assert stores.entries() == {
        "untaped/default/svc": "d-svc",
        "untaped/work/svc": "w-svc",
        "untaped/work/other": "w-other",
    }
    text = _isolated_config.read_text()
    assert "d-svc" not in text and "w-svc" not in text and "w-other" not in text
    assert _config(_isolated_config)["profiles"]["default"]["svc"]["base_url"] == "https://svc"
    assert "moved 3 tokens to pass" in result.stderr
    again = _auth("migrate", "--format", "json")
    assert again.exit_code == 0
    assert json.loads(again.stdout) == []
    assert "nothing to move" in again.stderr


def test_migrate_keeps_a_token_that_fails_and_exits_non_zero(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _PLAINTEXT)
    monkeypatch.setenv("STUB_MODE", "corrupt")
    result = _auth("migrate", "--format", "json")
    assert result.exit_code == 4
    assert {row["action"] for row in json.loads(result.stdout)} == {"failed"}
    assert "3 tokens could not be moved" in result.stderr
    assert _config(_isolated_config)["profiles"]["work"]["other"] == {"token": "w-other"}


def test_migrate_with_a_pass_that_cannot_decrypt_does_not_flood(
    _isolated_config: Path, stores: FakeStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _PLAINTEXT)
    monkeypatch.setenv("STUB_MODE", "gpg-decrypt")
    result = _auth("migrate", "--format", "json")
    assert result.exit_code == 4
    rows = json.loads(result.stdout)
    quote = "'pass' exited with status 2: gpg: public key decryption failed: No such file"
    details = [row["detail"] for row in rows]
    assert details.count(quote + " or directory") == 2
    assert sum("would override the token command" in detail for detail in details) == 1
    assert "gpg:" not in result.stderr, "gpg's own stderr never reaches the terminal"
    assert result.stderr.count("pinentry") == 1, "the fix is named once, not per token"
    assert "w-other" in _isolated_config.read_text()


def test_migrate_names_no_gpg_fix_for_a_failure_that_is_not_gpg(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    svc:\n      token: d-svc\n"
        "  my work:\n    svc:\n      token: w-svc\n",
    )
    result = _auth("migrate", "--format", "json")
    assert result.exit_code == 4
    assert {row["action"] for row in json.loads(result.stdout)} == {"moved", "failed"}
    assert "pinentry" not in result.stderr and "GPG_TTY" not in result.stderr


def test_migrate_without_a_store_changes_nothing(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch)
    write_config(_isolated_config, _PLAINTEXT)
    result = _auth("migrate")
    assert result.exit_code == 5
    assert "no token store is usable" in result.stderr
    assert "w-other" in _isolated_config.read_text()


# --- deprecations ---------------------------------------------------------------


def test_loading_a_plaintext_token_warns_once(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from untaped.settings import register_profile_settings

    register_profile_settings("svc", SvcProfile)
    write_config(_isolated_config, "profiles:\n  default:\n    svc:\n      token: tok-7f3a\n")
    get_config_section("svc", SvcProfile)
    get_config_section("svc", SvcProfile)
    err = capsys.readouterr().err
    assert err.count("svc.token is stored in plain text") == 1
    assert "untaped auth migrate" in err
    assert "tok-7f3a" not in err


@pytest.mark.parametrize(
    ("name", "value", "warns"),
    [
        ("UNTAPED_SVC__TOKEN", "env", False),
        ("UNTAPED_SVC", '{"token": "env"}', False),
        # The file's token is still the one used, so it still warns.
        ("UNTAPED_SVC", '{"base_url": "https://svc"}', True),
    ],
)
def test_only_an_env_token_silences_the_warning(
    _isolated_config: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    warns: bool,
) -> None:
    from untaped.settings import register_profile_settings

    register_profile_settings("svc", SvcProfile)
    write_config(_isolated_config, "profiles:\n  default:\n    svc:\n      token: file\n")
    monkeypatch.setenv(name, value)
    get_config_section("svc", SvcProfile)
    assert ("plain text" in capsys.readouterr().err) is warns


def test_config_set_of_a_token_is_deprecated(_isolated_config: Path) -> None:
    root = bootstrap.build_root_app(
        candidates=(provider_candidate(make_spec("svc", profile_model=SvcProfile)),)
    )
    result = invoke_cli(root.meta, ["config", "set", "svc.token", "tok"])
    assert result.exit_code == 0, result.output
    assert "storing svc.token in plain text in config.yml is deprecated" in result.stderr
    assert "untaped auth set svc" in result.stderr
    quiet = invoke_cli(root.meta, ["config", "set", "svc.base_url", "https://svc"])
    assert "deprecated" not in quiet.stderr


def test_a_malformed_section_override_is_not_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_SVC", "not json")
    assert auth.token_override_env("svc") is None

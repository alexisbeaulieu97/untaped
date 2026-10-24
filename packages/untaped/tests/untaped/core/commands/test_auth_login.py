"""``untaped auth login|list|forget|status``: the credentials plugins declare."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, HttpUrl, SecretStr

from core_test_support.commands.support import make_spec, root_group, write_config
from untaped.sdk import Credential, CredentialKey, NeedsSetup, TlsVerify
from untaped.sdk.errors import ConfigError
from untaped.sdk.marks import deprecated
from untaped.sdk.network._credential_store import CredentialRow, file_reference, index
from untaped.sdk.network._credential_store import rows as stored_rows
from untaped.sdk.network._token_store import TokenStore
from untaped.sdk.network.credentials import describe_key
from untaped.sdk.testing import CliInvoker, CliResult, ScriptedPromptBackend, current_memory_store

pytestmark = pytest.mark.usefixtures("_isolated_config")


class WizServer(CredentialKey):
    url: HttpUrl
    verify_tls: TlsVerify = True


class WizProfile(BaseModel):
    server: Annotated[WizServer | None, NeedsSetup] = None


class OldWizProfile(WizProfile):
    """``wiz`` still declaring 11.0's ``token``, as github does until it moves."""

    token: Annotated[SecretStr | None, deprecated(removed_in=12)] = None


class Vault(CredentialKey):
    vault_id: str


TOKEN = Credential("wiz", "token", key=WizServer, env=("WIZ_TOKEN",))
VAULT = Credential("vlt", "password", key=Vault)
SERVER = "format_version: 2\nwiz: {server: {url: https://wiz.example.com}}\n"
KEY = WizServer(url=HttpUrl("https://wiz.example.com"))


def _auth(
    *args: str,
    input: str | None = None,
    backend: ScriptedPromptBackend | None = None,
    settings: type[BaseModel] = WizProfile,
) -> CliResult:
    app = root_group(
        "auth",
        make_spec("wiz", settings=settings, credentials=(TOKEN,)),
        make_spec("vlt", credentials=(VAULT,)),
    )
    return CliInvoker().invoke(
        app,
        list(args),
        input=input,
        prompt_backend=backend,
        interactive=backend is not None,
    )


def _secret(credential: Credential[Any], key: CredentialKey) -> str | None:
    found = credential.get(key)
    return None if found is None else found.get_secret_value()


def _json(result: CliResult) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def _server(_isolated_config: Path) -> None:
    write_config(_isolated_config, SERVER)


# --- login ---------------------------------------------------------------------


@pytest.mark.usefixtures("_server")
def test_login_reads_the_token_from_stdin_for_this_runs_server() -> None:
    row = _json(_auth("login", "wiz", "--format", "json", input="s3cret\n"))

    assert (row["plugin"], row["name"], row["action"]) == ("wiz", "token", "stored")
    assert row["key"] == "url=https://wiz.example.com"
    assert sorted(current_memory_store().entries.values()) == ["s3cret"]
    assert _secret(TOKEN, KEY) == "s3cret"


@pytest.mark.usefixtures("_server")
def test_login_prompts_on_a_terminal(_isolated_config: Path) -> None:
    backend = ScriptedPromptBackend(secrets=["typed"])

    result = _auth("login", "wiz", backend=backend)

    assert result.exit_code == 0, result.output
    assert backend.calls == [("secret", "wiz token")]
    assert "stored the wiz token for url=https://wiz.example.com" in result.stderr
    assert "typed" not in result.output


@pytest.mark.usefixtures("_server")
@pytest.mark.parametrize(
    ("payload", "message"),
    [("", "no token received on stdin"), ("a\nb\n", "stdin should hold the token on one line")],
)
def test_login_refuses_empty_or_multiline_stdin(payload: str, message: str) -> None:
    result = _auth("login", "wiz", input=payload)

    assert result.exit_code == 2, result.output
    assert message in result.stderr
    assert stored_rows() == []


@pytest.mark.usefixtures("_server")
def test_login_with_a_command_files_it_and_names_what_it_outranks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_WIZ__TOKEN", "from-env")
    monkeypatch.setenv("WIZ_TOKEN", "plugin-env")

    result = _auth("login", "wiz", "--token-command", "printf tok")

    assert result.exit_code == 0, result.output
    [row] = stored_rows()
    assert (row.backend, row.argv) == ("command", ["printf", "tok"])
    assert "$UNTAPED_WIZ__TOKEN is set and wins over this login" in result.stderr
    assert "this login now wins over $WIZ_TOKEN" in result.stderr


@pytest.mark.usefixtures("_server")
@pytest.mark.parametrize(
    ("command", "message"),
    [("", "--token-command requires an argument"), ("'open", "is not a valid command line")],
)
def test_login_refuses_a_bad_token_command(command: str, message: str) -> None:
    result = _auth("login", "wiz", "--token-command", command)

    assert result.exit_code == 2, result.output
    assert message in result.stderr


def test_login_names_the_old_setting_a_login_outranks(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "format_version: 2\nwiz: {server: {url: https://wiz.example.com}, token: old}\n",
    )

    result = _auth("login", "wiz", input="new\n", settings=OldWizProfile)

    assert result.exit_code == 0, result.output
    assert "this login now wins over wiz.token" in result.stderr
    assert "untaped config unset wiz.token" in result.stderr


def test_login_before_setup_names_the_setup_step() -> None:
    result = _auth("login", "wiz", input="tok\n")

    assert result.exit_code == 4, result.output
    assert "wiz is not set up, so there is no server to file its token for" in result.stderr
    assert "setup plan --only wiz" in result.stderr


@pytest.mark.usefixtures("_server")
def test_login_refuses_key_fields_the_settings_hold() -> None:
    result = _auth("login", "wiz", "--key", "url=https://other.example.com", input="tok\n")

    assert result.exit_code == 2, result.output
    assert "key comes from wiz's settings, not from --key" in result.stderr


def test_a_key_the_settings_do_not_hold_comes_from_options() -> None:
    row = _json(_auth("login", "vlt", "--key", "vault-id=prod", "--format", "json", input="pw\n"))

    assert row["key"] == "vault_id=prod"
    assert _secret(VAULT, Vault(vault_id="prod")) == "pw"


def test_a_missing_key_option_is_named() -> None:
    result = _auth("login", "vlt", input="pw\n")

    assert result.exit_code == 2, result.output
    assert "vlt password needs --key vault-id=" in result.stderr


def test_a_key_option_without_a_value_is_refused() -> None:
    result = _auth("login", "vlt", "--key", "vault-id", input="pw\n")

    assert result.exit_code == 2, result.output
    assert "--key takes FIELD=VALUE, not 'vault-id'" in result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("login", "nope"), "nope declares no credential; plugins that do: vlt, wiz"),
        (("login", "wiz", "other"), "wiz has no credential 'other'; it declares: token"),
        (("login",), "name a plugin when not at a terminal; choices: vlt password, wiz"),
    ],
)
def test_login_off_a_terminal_needs_a_declared_credential(
    args: tuple[str, ...], message: str
) -> None:
    result = _auth(*args, input="tok\n")

    assert result.exit_code == 2, result.output
    assert message in result.stderr


@pytest.mark.usefixtures("_server")
def test_login_without_a_plugin_offers_a_menu_on_a_terminal() -> None:
    backend = ScriptedPromptBackend(selections=[TOKEN], secrets=["picked"])

    result = _auth("login", backend=backend)

    assert result.exit_code == 0, result.output
    assert [method for method, _ in backend.calls] == ["select", "secret"]
    assert _secret(TOKEN, KEY) == "picked"


def test_no_declared_credential_is_a_usage_error() -> None:
    result = CliInvoker().invoke(root_group("auth", make_spec("bare")), ["login"], input="t\n")

    assert result.exit_code == 2, result.output
    assert "no installed plugin declares a credential" in result.stderr


# --- list ----------------------------------------------------------------------


@pytest.mark.usefixtures("_server")
def test_list_shows_each_stored_row_and_never_a_secret() -> None:
    TOKEN.store(KEY, token="s3cret")
    VAULT.store(Vault(vault_id="prod"), command=["printf", "pw"])

    result = _auth("list", "--format", "json")

    listed = _json(result)
    assert [(row["plugin"], row["backend"]) for row in listed] == [
        ("wiz", "memory"),
        ("vlt", "command"),
    ]
    assert listed[1]["where"] == "command: printf …"
    assert "s3cret" not in result.output


def test_an_empty_list_says_so() -> None:
    result = _auth("list")

    assert result.exit_code == 0, result.output
    assert "no stored credentials" in result.stderr


# --- forget --------------------------------------------------------------------


@pytest.mark.usefixtures("_server")
def test_forget_deletes_the_row_and_its_entry() -> None:
    TOKEN.store(KEY, token="s3cret")

    row = _json(_auth("forget", "wiz", "--yes", "--format", "json"))

    assert row["action"] == "deleted"
    assert stored_rows() == []
    assert current_memory_store().entries == {}


@pytest.mark.usefixtures("_server")
def test_forget_dry_run_changes_nothing() -> None:
    TOKEN.store(KEY, token="s3cret")

    row = _json(_auth("forget", "wiz", "--dry-run", "--format", "json"))

    assert row["action"] == "planned"
    assert len(stored_rows()) == 1


@pytest.mark.usefixtures("_server")
def test_forget_with_nothing_stored_is_unchanged() -> None:
    result = _auth("forget", "wiz", "--yes", "--format", "json")

    row = _json(result)
    assert row["action"] == "unchanged"
    assert "no stored wiz token for url=https://wiz.example.com" in result.stderr


def test_forget_by_id_reaches_a_row_no_profile_names() -> None:
    VAULT.store(Vault(vault_id="old"), token="pw")
    [stored] = stored_rows()

    row = _json(_auth("forget", "--id", stored.short, "--yes", "--format", "json"))

    assert (row["key"], row["action"]) == ("vault_id=old", "deleted")
    assert stored_rows() == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--id", "ffff"), "no stored credential has id 'ffff'"),
        (("wiz", "--id", "ab"), "--id names the row on its own"),
        ((), "name a plugin or --id when not at a terminal"),
    ],
)
def test_forget_refuses_what_it_cannot_name(args: tuple[str, ...], message: str) -> None:
    result = _auth("forget", *args, "--yes")

    assert result.exit_code == 2, result.output
    assert message in result.stderr


def test_an_id_prefix_matching_several_rows_is_refused() -> None:
    for each in ("a", "b", "c", "d", "e", "f", "g", "h"):
        VAULT.store(Vault(vault_id=each), token="pw")
    ids = [row.id for row in stored_rows()]
    shared = next(
        prefix
        for prefix in "0123456789abcdef"
        if sum(ident.startswith(prefix) for ident in ids) > 1
    )

    result = _auth("forget", "--id", shared, "--yes")

    assert result.exit_code == 2, result.output
    assert f"id {shared!r} matches several stored credentials" in result.stderr


@pytest.mark.usefixtures("_server")
def test_forgetting_a_migrated_row_leaves_its_old_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(TokenStore, "delete", lambda self, entry: deleted.append(entry))
    file_reference(TOKEN, KEY, TokenStore("pass"), "default/wiz")

    result = _auth("forget", "wiz", "--yes")

    assert result.exit_code == 0, result.output
    assert "which an older untaped still reads" in result.stderr
    assert "nothing was revoked on the server" in result.stderr
    assert deleted == []


# --- status --------------------------------------------------------------------


@pytest.mark.usefixtures("_server")
def test_status_names_the_answering_source_for_this_run() -> None:
    TOKEN.store(KEY, token="s3cret")

    result = _auth("status", "--format", "json")

    rows = {row["section"]: row for row in _json(result)}
    assert rows["wiz"]["key"] == "url=https://wiz.example.com"
    assert rows["wiz"]["source"].startswith("stored ")
    assert rows["wiz"]["id"]
    assert (rows["vlt"]["key"], rows["vlt"]["source"]) == ("key given on the command line", "none")
    assert "token stores usable here:" in result.stderr
    assert "s3cret" not in result.output


def test_status_before_setup_says_so() -> None:
    rows = {row["section"]: row for row in _json(_auth("status", "--format", "json"))}

    assert (rows["wiz"]["key"], rows["wiz"]["source"]) == ("not set up", "none")


@pytest.mark.usefixtures("_server")
def test_login_tests_the_store_before_asking_for_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise ConfigError("pass cannot decrypt", hint="unlock gpg")

    monkeypatch.setattr(current_memory_store(), "preflight", broken)
    backend = ScriptedPromptBackend(secrets=["never"])

    result = _auth("login", "wiz", backend=backend)

    assert result.exit_code == 4, result.output
    assert "pass cannot decrypt" in result.stderr
    assert backend.calls == []


@pytest.mark.usefixtures("_server")
def test_a_command_login_over_a_migrated_row_keeps_the_old_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(TokenStore, "delete", lambda self, entry: deleted.append(entry))
    file_reference(TOKEN, KEY, TokenStore("pass"), "default/wiz")

    result = _auth("login", "wiz", "--token-command", "printf tok")

    assert result.exit_code == 0, result.output
    assert deleted == []


@pytest.mark.usefixtures("_server")
def test_logging_in_again_into_another_entry_deletes_the_replaced_one() -> None:
    memory = current_memory_store()
    TOKEN.store(KEY, token="first")
    [row] = stored_rows()
    memory.entries["elsewhere"] = memory.entries.pop(row.entry or "")
    index().put(row.model_copy(update={"entry": "elsewhere"}))

    TOKEN.store(KEY, token="second")

    assert "elsewhere" not in memory.entries
    assert sorted(memory.entries.values()) == ["second"]


class Hub(CredentialKey):
    url: HttpUrl = HttpUrl("https://api.hub.example.com")
    verify_tls: TlsVerify = True


def test_a_defaulted_url_is_still_named() -> None:
    hub = Credential("hub", "token", key=Hub)
    hub.store(Hub(), token="t")

    assert describe_key(Hub()) == "url=https://api.hub.example.com"
    [row] = stored_rows()
    assert row.key == {}, "the id leaves defaults out, so adding one keeps every login"
    listed = _json(_auth("list", "--format", "json"))
    assert listed[0]["key"] == "url=https://api.hub.example.com"


@pytest.mark.usefixtures("_server")
def test_a_replaced_entry_that_cannot_be_deleted_only_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def locked(self: TokenStore, entry: str) -> None:
        raise ConfigError("the keychain is locked")

    monkeypatch.setattr(TokenStore, "delete", locked)
    monkeypatch.setattr(TokenStore, "gone", lambda self, entry: False)
    index().put(
        CredentialRow(id=TOKEN.id(KEY), plugin="wiz", name="token", backend="pass", entry="old")
    )

    result = _auth("login", "wiz", input="new\n")

    assert result.exit_code == 0, result.output
    assert "the replaced entry pass" in result.stderr
    assert "the keychain is locked" in result.stderr
    assert _secret(TOKEN, KEY) == "new"

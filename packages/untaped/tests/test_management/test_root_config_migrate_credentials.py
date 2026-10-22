"""``untaped config migrate`` files each profile's config token as a stored login."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import pytest
import yaml
from pydantic import BaseModel, HttpUrl

from test_management.support import compose, make_spec, write_config
from untaped import bootstrap
from untaped.credential_store import rows as stored_rows
from untaped.management import credential_migration
from untaped.management.config import build_root_config_app
from untaped.sdk import Credential, CredentialKey, NeedsSetup, TlsVerify
from untaped.testing import CliInvoker, CliResult, current_memory_store

pytestmark = pytest.mark.usefixtures("_isolated_config")


class WizServer(CredentialKey):
    url: HttpUrl
    verify_tls: TlsVerify = True


class WizProfile(BaseModel):
    server: Annotated[WizServer | None, NeedsSetup] = None


TOKEN = Credential("wiz", "token", key=WizServer, env=("WIZ_TOKEN",))
_PRESET = ["pass", "show", "untaped/default/wiz"]


def _migrate(*args: str) -> CliResult:
    result = compose(make_spec("wiz", settings=WizProfile, credentials=(TOKEN,)))
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)
    return CliInvoker().invoke(app, ["migrate", *args])


def _rows(result: CliResult) -> list[dict[str, str]]:
    assert result.exit_code == 0, result.output
    found: list[dict[str, str]] = json.loads(result.stdout)
    return [row for row in found if row["from"].startswith(("wiz.", "http."))]


def _profiles(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return data["profiles"]


def test_a_plain_text_token_is_stored_and_leaves_the_file(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {server: {url: https://wiz.example.com}, token: s3cret}\n",
    )

    result = _migrate("--yes", "--format", "json")

    [row] = _rows(result)
    assert (row["from"], row["action"]) == ("wiz.token", "stored")
    assert row["to"] == "login wiz token (url=https://wiz.example.com)"
    assert "s3cret" not in result.output
    assert _profiles(_isolated_config)["default"]["wiz"] == {
        "server": {"url": "https://wiz.example.com"}
    }
    [stored] = stored_rows()
    assert stored.backend == "memory"
    assert current_memory_store().entries[stored.entry or ""] == "s3cret"
    assert "store 1 token" not in result.stderr and "stored 1 token" in result.stderr


def test_a_preset_command_points_at_its_entry(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      server: {url: https://wiz.example.com}\n"
        f"      token_command: {json.dumps(_PRESET)}\n",
    )

    [row] = _rows(_migrate("--yes", "--format", "json"))

    assert row["detail"] == "reads pass (untaped/default/wiz), as before"
    [stored] = stored_rows()
    assert (stored.backend, stored.entry, stored.migrated) == ("pass", "default/wiz", True)
    assert "token_command" not in _profiles(_isolated_config)["default"]["wiz"]


def test_another_command_is_kept_and_a_relative_path_named(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      server: {url: https://wiz.example.com}\n"
        '      token_command: ["cat", ".wiz-token"]\n',
    )

    [row] = _rows(_migrate("--yes", "--format", "json"))

    assert row["detail"] == (
        "the command is kept; .wiz-token is relative, so it runs from the config folder"
    )
    [stored] = stored_rows()
    assert (stored.backend, stored.argv) == ("command", ["cat", ".wiz-token"])


def test_an_inherited_preset_for_another_server_is_copied(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      server: {url: https://wiz.example.com}\n"
        f"      token_command: {json.dumps(_PRESET)}\n"
        "  lab:\n    wiz: {server: {url: https://lab.example.com}}\n",
    )
    monkeypatch.setattr(credential_migration, "read_command", lambda argv, setting: "from-entry")

    rows = _rows(_migrate("--yes", "--format", "json"))

    assert [(row["profile"], row["detail"]) for row in rows] == [
        ("default", "reads pass (untaped/default/wiz), as before"),
        ("lab", "copied from pass (untaped/default/wiz)"),
    ]
    copied = next(row for row in stored_rows() if row.backend == "memory")
    assert current_memory_store().entries[copied.entry or ""] == "from-entry"
    assert "token_command" not in _profiles(_isolated_config)["default"]["wiz"]


def test_two_profiles_filing_different_tokens_for_one_server_keep_both(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n"
        "  default:\n    wiz: {server: {url: https://wiz.example.com}, token: one}\n"
        "  work:\n    wiz: {token: two}\n",
    )

    rows = _rows(_migrate("--yes", "--format", "json"))

    assert [(row["profile"], row["action"]) for row in rows] == [
        ("default", "kept"),
        ("work", "kept"),
    ]
    assert "profile work files another wiz token" in rows[0]["detail"]
    assert "profile default files another wiz token" in rows[1]["detail"]
    profiles = _profiles(_isolated_config)
    assert (profiles["default"]["wiz"]["token"], profiles["work"]["wiz"]["token"]) == ("one", "two")
    assert stored_rows() == []


def test_a_server_only_the_environment_sets_is_kept(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_WIZ__SERVER__URL", "https://wiz.example.com")
    write_config(_isolated_config, "profiles:\n  default:\n    wiz: {token: s3cret}\n")

    [row] = _rows(_migrate("--yes", "--format", "json"))

    assert row["action"] == "kept"
    assert row["detail"].startswith("profile default's file sets no wiz server")
    assert _profiles(_isolated_config)["default"]["wiz"] == {"token": "s3cret"}


def test_root_verify_ssl_false_becomes_the_keys_verify_tls(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    http: {verify_ssl: false}\n"
        "    wiz: {server: {url: https://wiz.example.com}, token: s3cret}\n",
    )

    rows = _rows(_migrate("--yes", "--format", "json"))

    assert ("http.verify_ssl", "wiz.server.verify_tls", "copied") in {
        (row["from"], row["to"], row["action"]) for row in rows
    }
    default = _profiles(_isolated_config)["default"]
    assert default["wiz"]["server"]["verify_tls"] is False
    assert default["http"] == {"verify_ssl": False}, "the root line stays until 12.0"
    [stored] = stored_rows()
    assert stored.key == {"url": "https://wiz.example.com", "verify_tls": False}


def test_root_verify_ssl_false_keeps_the_key_a_login_was_filed_for(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    http: {verify_ssl: false}\n"
        "    wiz: {server: {url: https://wiz.example.com}, token: old}\n",
    )
    compose(make_spec("wiz", settings=WizProfile, credentials=(TOKEN,)))
    server = WizServer(url=HttpUrl("https://wiz.example.com"), verify_tls=False)
    TOKEN.store(server, token="new")

    rows = _rows(_migrate("--yes", "--format", "json"))

    assert ("wiz.token", "dropped") in {(row["from"], row["action"]) for row in rows}
    found = TOKEN.get(server)
    assert found is not None and found.get_secret_value() == "new"
    assert sorted(current_memory_store().entries.values()) == ["new"]


def test_a_stored_login_outranks_the_line_which_goes(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz: {server: {url: https://wiz.example.com}, token: old}\n",
    )
    compose(make_spec("wiz", settings=WizProfile, credentials=(TOKEN,)))
    TOKEN.store(WizServer(url=HttpUrl("https://wiz.example.com")), token="new")

    [row] = _rows(_migrate("--yes", "--format", "json"))

    assert (row["action"], row["detail"]) == ("dropped", "a stored login already answers")
    assert "token" not in _profiles(_isolated_config)["default"]["wiz"]
    assert sorted(current_memory_store().entries.values()) == ["new"]


def test_dry_run_plans_and_stores_nothing(_isolated_config: Path) -> None:
    text = "profiles:\n  default:\n    wiz: {server: {url: https://wiz.example.com}, token: s}\n"
    write_config(_isolated_config, text)

    result = _migrate("--dry-run", "--format", "json")

    assert [row["action"] for row in _rows(result)] == ["planned"]
    assert "would store 1 token" in result.stderr
    assert _isolated_config.read_text() == text
    assert stored_rows() == []


def test_moving_tokens_off_a_terminal_needs_yes(_isolated_config: Path) -> None:
    text = "profiles:\n  default:\n    wiz: {server: {url: https://wiz.example.com}, token: s}\n"
    write_config(_isolated_config, text)

    result = _migrate()

    assert result.exit_code == 2, result.output
    assert "config migrate requires --yes to move tokens when not interactive" in result.stderr
    assert "an older untaped sharing this config stops reading them" in result.stderr
    assert _isolated_config.read_text() == text
    assert stored_rows() == []

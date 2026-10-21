"""``auth`` for a plugin with several secrets: which one a name means, and where each goes.

``auth set|unset <section>`` means the ``Secret`` named ``token``, else the
only one, else refuses naming each; ``<section>.<field>`` names any. A
``token`` keeps the store entry ``<profile>/<section>`` every earlier
release wrote; another secret's entry is ``<profile>/<section>.<field>``.
The records say which secret (``field``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import pytest
import yaml
from pydantic import BaseModel, SecretStr

from test_management.stores import FakeStores, install_fake_stores
from test_management.support import make_spec, write_config
from untaped import bootstrap
from untaped.auth import clear_token_cache
from untaped.sdk import NeedsSetup, Secret
from untaped.settings import get_config_section
from untaped.stability import forget_notices
from untaped.testing import CliResult, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


class DuoProfile(BaseModel):
    """A token and a webhook secret (section ``duo``)."""

    base_url: Annotated[str | None, NeedsSetup] = None
    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=("DUO_TOKEN",))] = None
    webhook_secret: Annotated[SecretStr | None, Secret()] = None


class PairProfile(BaseModel):
    """Two secrets, neither named ``token`` (section ``pair``)."""

    api_key: Annotated[SecretStr | None, NeedsSetup, Secret()] = None
    signing_key: Annotated[SecretStr | None, Secret()] = None


class KeyProfile(BaseModel):
    """One secret, not named ``token`` (section ``keyed``)."""

    api_key: Annotated[SecretStr | None, NeedsSetup, Secret(env=("KEYED_KEY",))] = None


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeStores:
    forget_notices()
    for name in ("DUO_TOKEN", "KEYED_KEY", "UNTAPED_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    return install_fake_stores(tmp_path, monkeypatch, "pass")


def _auth(*args: str, input: str | None = None) -> CliResult:
    specs = (
        make_spec("duo", settings=DuoProfile),
        make_spec("pair", settings=PairProfile),
        make_spec("keyed", settings=KeyProfile),
    )
    root = bootstrap.build_root_app(candidates=tuple(plugin_candidate(s) for s in specs))
    return invoke_cli(root.meta, ["auth", *args], input=input)


def _section(path: Path, name: str) -> dict[str, Any]:
    node: dict[str, Any] = yaml.safe_load(path.read_text())["profiles"]["default"][name]
    return node


def test_two_secrets_get_separate_entries_and_a_bare_name_means_token(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n")
    token = _auth("set", "duo", "--stdin", "--format", "json", input="tok\n")
    hook = _auth("set", "duo.webhook_secret", "--stdin", "--format", "json", input="hook\n")
    assert token.exit_code == 0, token.output
    assert hook.exit_code == 0, hook.output
    assert stores.entries() == {
        "untaped/default/duo": "tok",
        "untaped/default/duo.webhook_secret": "hook",
    }
    assert _section(_isolated_config, "duo") == {
        "token_command": ["pass", "show", "untaped/default/duo"],
        "webhook_secret_command": ["pass", "show", "untaped/default/duo.webhook_secret"],
    }
    assert json.loads(token.stdout)["field"] == "token"
    assert json.loads(hook.stdout)["field"] == "webhook_secret"
    assert "duo.webhook_secret_command set in profile default" in hook.stderr
    clear_token_cache()
    settings = get_config_section("duo", DuoProfile)
    assert settings.token is not None and settings.token.get_secret_value() == "tok"
    assert settings.webhook_secret is not None
    assert settings.webhook_secret.get_secret_value() == "hook"


def test_the_only_secret_is_what_a_bare_name_means(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n")
    result = _auth("set", "keyed", "--stdin", input="k\n")
    assert result.exit_code == 0, result.output
    assert stores.entries() == {"untaped/default/keyed.api_key": "k"}
    assert _section(_isolated_config, "keyed") == {
        "api_key_command": ["pass", "show", "untaped/default/keyed.api_key"]
    }


@pytest.mark.parametrize("verb", ["set", "unset"])
def test_several_secrets_and_no_token_refuse_a_bare_name(stores: FakeStores, verb: str) -> None:
    result = _auth(verb, "pair", "--stdin" if verb == "set" else "--yes", input="x\n")
    assert result.exit_code == 1, result.output
    assert "pair has several secrets (pair.api_key, pair.signing_key)" in result.stderr
    assert f"untaped auth {verb} pair.api_key" in result.stderr
    assert stores.entries() == {}


def test_an_entry_an_earlier_release_wrote_still_serves_the_token(
    _isolated_config: Path, stores: FakeStores
) -> None:
    """``<profile>/<section>`` stays the token's entry, so nothing written before moves."""
    write_config(_isolated_config, "profiles:\n  default: {}\n")
    assert _auth("set", "duo", "--stdin", input="old\n").exit_code == 0
    status = _auth("status", "--format", "json")
    rows = {(row["section"], row["field"]): row["source"] for row in json.loads(status.stdout)}
    assert rows[("duo", "token")] == "pass (untaped/default/duo)"
    assert rows[("duo", "webhook_secret")] == "none"
    unset = _auth("unset", "duo", "--yes", "--format", "json")
    assert unset.exit_code == 0, unset.output
    assert json.loads(unset.stdout)["action"] == "deleted"
    assert stores.entries() == {}


def test_migrate_moves_each_plaintext_secret_to_its_own_entry(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    duo: {token: t, webhook_secret: w}\n",
    )
    result = _auth("migrate", "--format", "json")
    assert result.exit_code == 0, result.output
    assert {(row["field"], row["action"]) for row in json.loads(result.stdout)} == {
        ("token", "moved"),
        ("webhook_secret", "moved"),
    }
    assert stores.entries() == {
        "untaped/default/duo": "t",
        "untaped/default/duo.webhook_secret": "w",
    }
    assert set(_section(_isolated_config, "duo")) == {"token_command", "webhook_secret_command"}


def test_an_unknown_secret_is_named_with_the_known_ones(stores: FakeStores) -> None:
    result = _auth("set", "duo.nope", "--stdin", input="x\n")
    assert result.exit_code == 1
    assert "unknown token section 'duo.nope'" in result.stderr
    assert "duo.webhook_secret" in result.stderr


def test_config_set_of_another_secret_names_its_own_auth_set(
    _isolated_config: Path, stores: FakeStores
) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n")
    specs = (make_spec("duo", settings=DuoProfile),)
    root = bootstrap.build_root_app(candidates=tuple(plugin_candidate(s) for s in specs))
    result = invoke_cli(root.meta, ["config", "set", "duo.webhook_secret", "w"])
    assert result.exit_code == 0, result.output
    stderr = " ".join(result.stderr.split())
    assert "plain-text duo.webhook_secret in config.yml is deprecated" in stderr
    assert "use untaped auth set duo.webhook_secret" in stderr.replace("`", "")

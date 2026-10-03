"""``untaped setup``: the interactive wizard that configures a profile.

It offers every composed capability whose profile model has ``base_url``
and ``token``, writes through the same validated path as ``config set``,
then runs those capabilities' checks, online ones included.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from test_management.stores import FakeStores, install_fake_stores
from test_management.support import ExtProfile, make_spec, write_config
from untaped import bootstrap
from untaped.config_file import read_config_dict
from untaped.sdk import (
    HttpStatusError,
    TokenCommand,
    TokenSources,
    online_check,
)
from untaped.testing import CliResult, ScriptedPromptBackend, invoke_cli, provider_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


class WizProfile(BaseModel):
    """Service profile double (section ``wiz``)."""

    token_sources: ClassVar[TokenSources] = TokenSources()

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


_PROBES: list[str] = []


def _probe() -> str:
    _PROBES.append("probed")
    if _FAIL:
        raise HttpStatusError("HTTP 401 from https://wiz/me", status_code=401)
    return "authenticated as alice"


_FAIL: list[bool] = []


def _setup(backend: ScriptedPromptBackend | None, *args: str) -> CliResult:
    wiz = make_spec(
        "wiz",
        profile_model=WizProfile,
        doctor_checks=(online_check("wiz.api", section="wiz", probe=_probe),),
    )
    plain = make_spec("plain", profile_model=ExtProfile)
    root = bootstrap.build_root_app(candidates=(provider_candidate(wiz), provider_candidate(plain)))
    return invoke_cli(
        root.meta,
        ["setup", "--format", "json", *args],
        interactive=backend is not None,
        prompt_backend=backend,
    )


class EnvProfile(BaseModel):
    """Service double with a conventional token variable (section ``envy``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("ENVY_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


class LegacyProfile(BaseModel):
    """Service double without ``token_command`` (section ``legacy``)."""

    base_url: str | None = None
    token: SecretStr | None = None


class ChoiceRecorder(ScriptedPromptBackend):
    """Scripted backend that also records each select's choice values."""

    offered: ClassVar[list[list[str]]] = []

    def select(
        self,
        message: str,
        choices: Any,
        *,
        default: Any | None,
        search: bool,
    ) -> Any:
        self.offered.append([choice.value for choice in choices])
        return super().select(message, choices, default=default, search=search)


@pytest.fixture(autouse=True)
def _reset_probes() -> None:
    _PROBES.clear()
    _FAIL.clear()
    ChoiceRecorder.offered.clear()


@pytest.fixture(autouse=True)
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeStores:
    """No store is usable unless a test installs one."""
    return install_fake_stores(tmp_path, monkeypatch)


def _wiz(path: Path, profile: str = "default") -> dict[str, Any]:
    section = read_config_dict(path)["profiles"][profile]["wiz"]
    assert isinstance(section, dict)
    return section


def test_setup_without_a_terminal_is_a_usage_error(_isolated_config: Path) -> None:
    result = _setup(None)
    assert result.exit_code == 2
    assert "setup requires an interactive terminal" in result.stderr
    assert "run `untaped setup plan --format json`" in result.stderr
    assert not _isolated_config.exists()


def test_setup_configures_the_service_and_checks_it(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    backend = ScriptedPromptBackend(
        texts=["default", "https://wiz"],
        multiselects=[["wiz"]],
        selections=["store"],
        secrets=[" tok "],
    )
    result = _setup(backend)
    assert result.exit_code == 0, result.output
    assert _wiz(_isolated_config) == {
        "base_url": "https://wiz",
        "token_command": ["pass", "show", "untaped/default/wiz"],
    }
    assert stores.entries() == {"untaped/default/wiz": "tok"}
    assert backend.calls == [
        ("text", "Profile to configure"),
        ("multiselect", "Capabilities to configure"),
        ("text", "wiz base URL"),
        ("select", "wiz token"),
        ("secret", "wiz token"),
    ]
    rows = {row["check"]: row for row in json.loads(result.stdout)}
    assert rows["wiz.api"] == {
        "check": "wiz.api",
        "capability": "wiz",
        "status": "pass",
        "title": "wiz API reachable",
        "detail": "authenticated as alice",
        "fix": None,
    }
    assert {row["capability"] for row in rows.values()} == {"wiz"}
    assert _PROBES == ["probed"]


def test_setup_creates_a_new_profile_with_a_token_command(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\nactive: default\n")
    backend = ScriptedPromptBackend(
        texts=["prod", "https://wiz.prod", "pass show wiz token"],
        multiselects=[["wiz"]],
        selections=["command"],
    )
    result = _setup(backend)
    assert result.exit_code == 0, result.output
    config = read_config_dict(_isolated_config)
    assert config["active"] == "default"
    assert config["profiles"]["prod"]["wiz"] == {
        "base_url": "https://wiz.prod",
        "token_command": ["pass", "show", "wiz", "token"],
    }
    assert "profile prod is ready" in result.stderr
    assert "untaped --profile prod" in result.stderr
    assert "untaped profile use prod" in result.stderr


def test_an_inherited_token_would_override_the_token_command(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    wiz:\n      token: shared\nactive: default\n"
    )
    backend = ScriptedPromptBackend(
        texts=["prod", "https://wiz.prod", "pass show wiz"],
        multiselects=[["wiz"]],
        selections=["command"],
    )
    result = _setup(backend)
    assert result.exit_code == 4  # the stored config must change
    assert "wiz.token is set in profile default" in result.stderr
    assert "untaped auth migrate" in result.stderr
    assert "wiz" not in (read_config_dict(_isolated_config)["profiles"].get("prod") or {})
    assert _PROBES == []


def test_a_malformed_token_command_writes_nothing(_isolated_config: Path) -> None:
    backend = ScriptedPromptBackend(
        texts=["default", "https://wiz", 'pass show "wiz'],
        multiselects=[["wiz"]],
        selections=["command"],
    )
    result = _setup(backend)
    assert result.exit_code == 1
    assert "invalid wiz token command" in result.stderr
    assert "Traceback" not in result.output
    assert not _isolated_config.exists()


def test_setup_can_keep_the_current_token(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      base_url: https://old\n      token: kept\n",
    )
    backend = ScriptedPromptBackend(
        texts=["default", "https://new"], multiselects=[["wiz"]], selections=["keep"]
    )
    result = _setup(backend)
    assert result.exit_code == 0, result.output
    assert _wiz(_isolated_config) == {"base_url": "https://new", "token": "kept"}


def test_a_failed_check_fails_setup_and_names_the_fix(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    _FAIL.append(True)
    backend = ScriptedPromptBackend(
        texts=["default", "https://wiz"],
        multiselects=[["wiz"]],
        selections=["store"],
        secrets=["bad"],
    )
    result = _setup(backend)
    assert result.exit_code == 1
    row = next(row for row in json.loads(result.stdout) if row["check"] == "wiz.api")
    assert row["fix"] == ["--profile", "default", "auth", "set", "wiz"]
    assert "setup: 1 of" in result.stderr
    assert stores.entries() == {"untaped/default/wiz": "bad"}


def test_selecting_nothing_changes_nothing(_isolated_config: Path) -> None:
    backend = ScriptedPromptBackend(texts=["default"], multiselects=[[]])
    result = _setup(backend)
    assert result.exit_code == 0, result.output
    assert "no capabilities selected; no changes made" in result.stderr
    assert not _isolated_config.exists()


def _setup_specs(backend: ScriptedPromptBackend, *specs: Any) -> CliResult:
    root = bootstrap.build_root_app(candidates=tuple(provider_candidate(s) for s in specs))
    return invoke_cli(
        root.meta, ["setup", "--format", "json"], interactive=True, prompt_backend=backend
    )


def test_a_plaintext_token_defaults_to_moving_it_to_the_store(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      base_url: https://wiz\n      token: old\n",
    )
    backend = ChoiceRecorder(
        texts=["default", "https://wiz"], multiselects=[["wiz"]], selections=["move"]
    )
    result = _setup(backend)
    assert result.exit_code == 0, result.output
    assert ChoiceRecorder.offered == [["move", "keep", "store", "command"]]
    assert _wiz(_isolated_config) == {
        "base_url": "https://wiz",
        "token_command": ["pass", "show", "untaped/default/wiz"],
    }
    assert stores.entries() == {"untaped/default/wiz": "old"}
    assert "old" not in _isolated_config.read_text()


def test_without_a_store_setup_never_offers_plain_text(_isolated_config: Path) -> None:
    envy = make_spec("envy", profile_model=EnvProfile)
    backend = ChoiceRecorder(
        texts=["default", "https://envy"], multiselects=[["envy"]], selections=["env"]
    )
    result = _setup_specs(backend, envy)
    assert result.exit_code == 0, result.output
    assert ChoiceRecorder.offered == [["command", "env"]]
    section = read_config_dict(_isolated_config)["profiles"]["default"]["envy"]
    assert section == {"base_url": "https://envy"}
    assert "export $ENVY_TOKEN" in result.stderr


def test_a_model_without_token_command_still_takes_a_typed_token(_isolated_config: Path) -> None:
    legacy = make_spec("legacy", profile_model=LegacyProfile)
    backend = ChoiceRecorder(
        texts=["default", "https://legacy"],
        multiselects=[["legacy"]],
        selections=["enter"],
        secrets=["tok"],
    )
    result = _setup_specs(backend, legacy)
    assert result.exit_code == 0, result.output
    assert ChoiceRecorder.offered == [["enter"]]
    section = read_config_dict(_isolated_config)["profiles"]["default"]["legacy"]
    assert section == {"base_url": "https://legacy", "token": "tok"}


def test_choices_list_the_store_first_for_a_new_service(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    envy = make_spec("envy", profile_model=EnvProfile)
    backend = ChoiceRecorder(
        texts=["default", "https://envy", "op read x"],
        multiselects=[["envy"]],
        selections=["command"],
    )
    result = _setup_specs(backend, envy)
    assert result.exit_code == 0, result.output
    assert ChoiceRecorder.offered == [["store", "command", "env"]]


def test_only_preselects_the_services_and_skips_the_multiselect(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    backend = ScriptedPromptBackend(
        texts=["default", "https://wiz"], selections=["store"], secrets=["tok"]
    )
    result = _setup(backend, "--only", "wiz")
    assert result.exit_code == 0, result.output
    assert ("multiselect", "Capabilities to configure") not in backend.calls
    assert _wiz(_isolated_config)["base_url"] == "https://wiz"


def test_only_rejects_a_name_that_is_not_a_service(_isolated_config: Path) -> None:
    result = _setup(ScriptedPromptBackend(), "--only", "plain")
    assert result.exit_code == 2
    assert "service not found: 'plain'; known: wiz" in result.stderr

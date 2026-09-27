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

from test_management.support import ExtProfile, make_spec, write_config
from untaped import bootstrap
from untaped.capability_api import (
    HttpStatusError,
    TokenCommand,
    TokenSources,
    online_check,
)
from untaped.config_file import read_config_dict
from untaped.testing import CliResult, ScriptedPromptBackend, invoke_cli

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
    root = bootstrap.build_root_app(builtins=(wiz, plain), externals=())
    return invoke_cli(
        root.meta,
        ["setup", "--format", "json", *args],
        interactive=backend is not None,
        prompt_backend=backend,
    )


@pytest.fixture(autouse=True)
def _reset_probes() -> None:
    _PROBES.clear()
    _FAIL.clear()


def _wiz(path: Path, profile: str = "default") -> dict[str, Any]:
    section = read_config_dict(path)["profiles"][profile]["wiz"]
    assert isinstance(section, dict)
    return section


def test_setup_without_a_terminal_is_a_usage_error(_isolated_config: Path) -> None:
    result = _setup(None)
    assert result.exit_code == 2
    assert "error: setup requires an interactive terminal" in result.stderr
    assert not _isolated_config.exists()


def test_setup_configures_the_service_and_checks_it(_isolated_config: Path) -> None:
    backend = ScriptedPromptBackend(
        texts=["default", "https://wiz"],
        multiselects=[["wiz"]],
        selections=["enter"],
        secrets=["tok"],
    )
    result = _setup(backend)
    assert result.exit_code == 0, result.output
    assert _wiz(_isolated_config) == {"base_url": "https://wiz", "token": "tok"}
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


def test_a_failed_check_fails_setup_and_names_the_fix(_isolated_config: Path) -> None:
    _FAIL.append(True)
    backend = ScriptedPromptBackend(
        texts=["default", "https://wiz"],
        multiselects=[["wiz"]],
        selections=["enter"],
        secrets=["bad"],
    )
    result = _setup(backend)
    assert result.exit_code == 1
    row = next(row for row in json.loads(result.stdout) if row["check"] == "wiz.api")
    assert row["detail"].endswith("run `untaped config set wiz.token --prompt`")
    assert "setup: 1 of" in result.stderr
    assert _wiz(_isolated_config)["token"] == "bad"


def test_selecting_nothing_changes_nothing(_isolated_config: Path) -> None:
    backend = ScriptedPromptBackend(texts=["default"], multiselects=[[]])
    result = _setup(backend)
    assert result.exit_code == 0, result.output
    assert "no capabilities selected; no changes made" in result.stderr
    assert not _isolated_config.exists()

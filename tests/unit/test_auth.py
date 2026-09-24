"""Tests for token resolution: explicit token > ``token_command`` > env fallbacks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr, ValidationError

from untaped.app_context import app_context
from untaped.auth import describe_token_source, resolve_token
from untaped.capability_api import TokenCommand, TokenSources, connected_client
from untaped.errors import ConfigError
from untaped.settings import (
    HttpSettings,
    get_config_section,
    get_settings,
    get_settings_model,
    register_profile_settings,
    reset_config_registry_for_tests,
)


class DemoSettings(BaseModel):
    token_sources: ClassVar[TokenSources] = TokenSources(env=("DEMO_TOKEN", "DEMO_TOKEN_2"))

    base_url: str = "https://api.example.com"
    token: SecretStr | None = None
    token_command: TokenCommand = None


class PlainSettings(BaseModel):
    token: SecretStr | None = None


def _printer(text: str, *, log: Path | None = None, code: int = 0) -> list[str]:
    """Argv for a command that prints ``text`` (and appends to ``log``)."""
    script = f"print({text!r})"
    if log is not None:
        script = f"open({str(log)!r}, 'a').write('x'); " + script
    if code:
        script += f"; raise SystemExit({code})"
    return [sys.executable, "-c", script]


def test_explicit_token_wins_over_command_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "from-env")
    settings = DemoSettings(token=SecretStr("explicit"), token_command=_printer("from-cmd"))
    resolved = resolve_token(settings, section="demo")
    assert resolved.token is not None
    assert resolved.token.get_secret_value() == "explicit"
    assert describe_token_source(settings, section="demo") == "demo.token"


def test_command_wins_over_env_and_runs_once_lazily(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "from-env")
    log = tmp_path / "runs"
    settings = DemoSettings(token_command=_printer("  from-cmd\n", log=log))
    resolved = resolve_token(settings, section="demo")
    assert describe_token_source(settings, section="demo") == "demo.token_command"
    assert not log.exists(), "the command must not run until the token is read"
    assert resolved.token is not None
    assert repr(resolved.token) == "CommandToken('**********')"
    assert not log.exists()
    assert resolved.token.get_secret_value() == "from-cmd"
    again = resolve_token(settings, section="demo")
    assert again.token is not None
    assert again.token.get_secret_value() == "from-cmd"
    assert log.read_text() == "x"


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"DEMO_TOKEN": "a", "DEMO_TOKEN_2": "b"}, "a"), ({"DEMO_TOKEN_2": "b"}, "b")],
)
def test_env_fallbacks_in_declared_order(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    resolved = resolve_token(DemoSettings(), section="demo")
    assert resolved.token is not None
    assert resolved.token.get_secret_value() == expected


def test_no_source_leaves_settings_unchanged() -> None:
    settings = DemoSettings()
    assert resolve_token(settings, section="demo") is settings
    assert describe_token_source(settings, section="demo") is None


def test_models_without_token_sources_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "from-env")
    settings = PlainSettings()
    assert resolve_token(settings, section="plain") is settings


def test_failing_command_names_program_and_status_but_not_output() -> None:
    settings = DemoSettings(token_command=_printer("s3cr3t-output", code=3))
    resolved = resolve_token(settings, section="demo")
    assert resolved.token is not None
    with pytest.raises(ConfigError) as caught:
        resolved.token.get_secret_value()
    message = str(caught.value)
    assert "demo.token_command" in message
    assert "exited with status 3" in message
    assert "s3cr3t-output" not in message
    assert "print" not in message, "arguments may carry secrets and are never shown"


def test_missing_program_and_empty_output_are_config_errors() -> None:
    missing = resolve_token(DemoSettings(token_command=["untaped-no-such-program"]), section="demo")
    assert missing.token is not None
    with pytest.raises(ConfigError, match="not found on PATH"):
        missing.token.get_secret_value()
    empty = resolve_token(DemoSettings(token_command=_printer("")), section="demo")
    assert empty.token is not None
    with pytest.raises(ConfigError, match="printed no token"):
        empty.token.get_secret_value()


def test_token_command_must_be_a_non_empty_argv_list() -> None:
    with pytest.raises(ValidationError, match="non-empty argv list"):
        DemoSettings(token_command=[])
    with pytest.raises(ValidationError):
        DemoSettings.model_validate({"token_command": "gh auth token"})


@respx.mock
def test_command_token_becomes_the_bearer_header() -> None:
    route = respx.get("https://api.example.com/user").mock(return_value=httpx.Response(200))
    settings = resolve_token(DemoSettings(token_command=_printer("cmd-token")), section="demo")
    with connected_client(settings, section="demo", http=HttpSettings()) as client:
        client.get("/user")
    assert route.calls.last.request.headers["Authorization"] == "Bearer cmd-token"


def test_section_accessors_apply_token_fallbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.yml"
    config.write_text("profiles:\n  default:\n    demo:\n      base_url: https://x\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(config))
    monkeypatch.setenv("DEMO_TOKEN_2", "env-token")
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoSettings)
    get_settings.cache_clear()
    get_settings_model.cache_clear()
    try:
        via_context = app_context().section("demo", DemoSettings)
        via_helper = get_config_section("demo", DemoSettings)
    finally:
        reset_config_registry_for_tests()
    for settings in (via_context, via_helper):
        assert settings.token is not None
        assert settings.token.get_secret_value() == "env-token"

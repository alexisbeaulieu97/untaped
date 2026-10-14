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
from untaped.auth import describe_token_source, forget_token_command, resolve_token
from untaped.errors import ConfigError
from untaped.sdk import TokenCommand, TokenSources, connected_client
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


def test_ambient_false_ignores_the_conventional_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "from-env")
    assert describe_token_source(DemoSettings(), section="demo") == "$DEMO_TOKEN"
    assert describe_token_source(DemoSettings(), section="demo", ambient=False) is None
    command = DemoSettings(token_command=["x"])
    assert describe_token_source(command, section="demo", ambient=False) == "demo.token_command"


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


def test_forgetting_a_command_runs_it_again_and_leaves_the_others_cached(tmp_path: Path) -> None:
    log, other_log = tmp_path / "runs", tmp_path / "other"
    argv, other = _printer("tok", log=log), _printer("tok", log=other_log)

    def read(command: list[str]) -> str:
        token = resolve_token(DemoSettings(token_command=command), section="demo").token
        assert token is not None
        return token.get_secret_value()

    read(argv), read(other), read(argv)
    assert (log.read_text(), other_log.read_text()) == ("x", "x")  # cached

    forget_token_command(argv)
    read(argv), read(other)

    assert (log.read_text(), other_log.read_text()) == ("xx", "x")
    forget_token_command(["never", "run"])  # an unknown command is nothing to forget


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


class _Stdin:
    def __init__(self, *, tty: bool, closed: bool = False) -> None:
        self._tty = tty
        self._closed = closed

    def isatty(self) -> bool:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        return self._tty


def _locked_pass(tmp_path: Path, *args: str) -> list[str]:
    """Argv for a ``pass show`` whose gpg key is locked: it asks for the passphrase.

    Asked with ``--pinentry-mode=error`` (in ``PASSWORD_STORE_GPG_OPTS``, as
    ``pass`` hands it to gpg), it fails as gpg does; asked otherwise, the
    prompt "is answered" and the token prints. The options it saw go to a log.
    """
    program = tmp_path / "pass"
    log = tmp_path / "gpg-opts.log"
    program.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "opts = os.environ.get('PASSWORD_STORE_GPG_OPTS', '')\n"
        f"open({str(log)!r}, 'a').write(opts + '\\n')\n"
        "if '--pinentry-mode=error' in opts.split():\n"
        "    print('gpg: public key decryption failed: No pinentry', file=sys.stderr)\n"
        "    print('gpg: decryption failed: No pinentry', file=sys.stderr)\n"
        "    raise SystemExit(2)\n"
        "print('unlocked-token')\n"
    )
    program.chmod(0o755)
    return [str(program), *(args or ("show", "untaped/default/demo"))]


def test_a_locked_pass_store_fails_fast_when_stdin_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An agent's stdin is no terminal, yet gpg-agent would draw its pinentry on
    # the GPG_TTY the agent inherited: the user's own screen, under the agent.
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=False))
    monkeypatch.setenv("PASSWORD_STORE_GPG_OPTS", "--armor")
    settings = resolve_token(DemoSettings(token_command=_locked_pass(tmp_path)), section="demo")
    assert settings.token is not None
    with pytest.raises(ConfigError) as caught:
        settings.token.get_secret_value()
    error = caught.value
    assert error.category == "unavailable"
    assert "locked" in str(error)
    assert "not a terminal" in str(error)
    assert error.hint is not None
    assert "pass show untaped/default/demo > /dev/null" in error.hint
    assert (tmp_path / "gpg-opts.log").read_text() == "--armor --pinentry-mode=error\n"


def test_a_locked_pass_store_still_prompts_from_a_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    monkeypatch.delenv("PASSWORD_STORE_GPG_OPTS", raising=False)
    settings = resolve_token(DemoSettings(token_command=_locked_pass(tmp_path)), section="demo")
    assert settings.token is not None
    assert settings.token.get_secret_value() == "unlocked-token"
    assert (tmp_path / "gpg-opts.log").read_text() == "\n"


def test_a_locked_custom_pass_command_names_no_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A closed stdin is no terminal either.
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True, closed=True))
    argv = _locked_pass(tmp_path, "show", "--clip", "work/s3cr3t-name")
    settings = resolve_token(DemoSettings(token_command=argv), section="demo")
    assert settings.token is not None
    with pytest.raises(ConfigError) as caught:
        settings.token.get_secret_value()
    assert caught.value.hint is not None
    assert "by running the same command" in caught.value.hint
    assert "s3cr3t-name" not in f"{caught.value} {caught.value.hint}"

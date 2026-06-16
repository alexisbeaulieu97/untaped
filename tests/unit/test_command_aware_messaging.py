"""SDK guidance names the active tool's command, not the retired umbrella.

A tool process registers itself via ``register_tool``; deep helpers then
render hints like ``untaped-github config set token`` instead of
``untaped config set github.token``. Absent a registered tool (a misuse
path — the SDK used without ``run_tool``) a neutral ``<tool>`` placeholder
is used, never the retired central ``untaped`` command.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.http import missing_setting_error
from untaped.settings_layout import ProfilesSettingsLayout
from untaped.tool import ToolSpec, register_tool


class _GithubSettings(BaseModel):
    token: str | None = None


def _register() -> None:
    register_tool(
        ToolSpec(command="untaped-github", section="github", profile_model=_GithubSettings)
    )


def test_missing_setting_error_names_tool_command(_isolated_config: Path) -> None:
    _register()
    msg = str(missing_setting_error("github", "token"))
    assert "untaped-github config set token" in msg
    assert "untaped config set github.token" not in msg


def test_missing_setting_error_without_tool_uses_placeholder(_isolated_config: Path) -> None:
    # No tool registered (misuse path): a neutral <tool> placeholder, never
    # the retired central `untaped` command.
    msg = str(missing_setting_error("github", "token"))
    assert "`<tool> config set github.token" in msg
    assert "untaped config set github.token" not in msg


def test_write_scope_hint_names_tool_command(_isolated_config: Path) -> None:
    _register()
    with pytest.raises(ConfigError) as exc:
        ProfilesSettingsLayout().write_scope({"profiles": {"default": {}}}, "nope")
    assert "untaped-github profile create" in str(exc.value)

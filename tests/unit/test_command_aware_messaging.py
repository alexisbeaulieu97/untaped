"""SDK guidance names the active tool's command, not the dead umbrella.

A tool process registers itself via ``register_tool``; deep helpers then
render hints like ``untaped-github config set token`` instead of
``untaped config set github.token``. Absent a registered tool (the legacy
umbrella context) the qualified, ``untaped``-prefixed form is preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.plugin_http import missing_setting_error
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


def test_missing_setting_error_defaults_to_umbrella(_isolated_config: Path) -> None:
    # No tool registered: legacy/umbrella context keeps the qualified key form.
    msg = str(missing_setting_error("github", "token"))
    assert "untaped config set github.token" in msg


def test_write_scope_hint_names_tool_command(_isolated_config: Path) -> None:
    _register()
    with pytest.raises(ConfigError) as exc:
        ProfilesSettingsLayout().write_scope({"profiles": {"default": {}}}, "nope")
    assert "untaped-github profile create" in str(exc.value)

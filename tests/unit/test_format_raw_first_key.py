"""Pin SDK ``--format raw`` first-key row contracts behaviorally."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, SecretStr

from untaped.config import build_config_app
from untaped.testing import CliInvoker
from untaped.tool import ToolSpec, register_tool


class _Settings(BaseModel):
    token: SecretStr | None = None
    base_url: str = "https://api.example.test"


SPEC = ToolSpec(
    command="untaped-demo",
    section="demo",
    profile_model=_Settings,
)


def _app():
    register_tool(SPEC)
    return build_config_app(SPEC)


def test_config_list_raw_defaults_to_key_column(_isolated_config: Path) -> None:
    result = CliInvoker().invoke(_app(), ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "demo.token" in lines
    assert "demo.base_url" in lines
    assert "https://api.example.test" not in lines


def test_config_list_all_profiles_empty_outputs_nothing(_isolated_config: Path) -> None:
    result = CliInvoker().invoke(_app(), ["list", "--all-profiles", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""

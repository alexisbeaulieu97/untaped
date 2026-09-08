"""Ongoing privacy and validation contracts for the public planning store."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
STORE = ROOT / ".untaped/orchestration"
STORE_ID = "sto_019f68b6af9e721e970126ca31dbfde1"


def load_toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_store_is_public_decision_only_and_childless() -> None:
    store = load_toml(STORE / "store.toml")
    assert store["schema"] == "untaped.orchestration.store/v1"
    assert store["id"] == STORE_ID
    assert store["name"] == "untaped"
    assert store["visibility"] == "public"
    assert store["timezone"] == "UTC"
    assert store["capabilities"] == {"active_tasks": False}
    assert load_toml(STORE / "registry.toml") == {
        "schema": "untaped.orchestration.registry/v1",
        "store_id": STORE_ID,
    }
    assert not list((STORE / "tasks").glob("*.md")) if (STORE / "tasks").exists() else True


def test_agent_rules_ignore_rules_and_workflow() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for phrase in (
        "public decision-only",
        "revision guard",
        "--force-current",
        "human-only",
        "no tasks",
        "check --local",
        "render --check",
    ):
        assert phrase in agents
    ignores = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {
        ".untaped/orchestration/**/.lock",
        ".untaped/orchestration/**/.DS_Store",
        ".untaped/orchestration/**/.*.untaped-tmp-*",
        ".untaped/orchestration/**/*~",
        ".untaped/orchestration/**/*.swp",
        ".untaped/orchestration/**/*.swo",
        ".untaped/orchestration/**/*.tmp",
        ".untaped/orchestration/**/.#*",
        ".untaped/orchestration/**/#*",
    } <= ignores
    workflow = (ROOT / ".github/workflows/orchestration.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in workflow
    assert "astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39" in workflow
    assert 'version: "0.11.26"' in workflow
    commands = re.findall(r"^\s+run: (uv .+)$", workflow, re.MULTILINE)
    assert commands == [
        "uv sync --locked",
        "uv run untaped orchestration check --local",
        "uv run untaped orchestration fmt --check --local",
        "uv run untaped orchestration render --check",
    ]
    assert all(
        path in workflow
        for path in (
            ".untaped/orchestration/**",
            ".github/workflows/orchestration.yml",
            ".gitignore",
            "AGENTS.md",
            "CLAUDE.md",
        )
    )
    assert "PYTHONPATH" not in workflow
    assert "render --check --local" not in workflow

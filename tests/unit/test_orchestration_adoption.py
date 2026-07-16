"""Repository contract for the empty public orchestration store."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / ".untaped" / "orchestration"
STORE_ID = "sto_019f6ae028d57103832444567b2cb514"
STORE_NAME = "untaped-workspace"
REVISION_RE = re.compile(r"sha256:[0-9a-f]{64}")
ORCHESTRATION_PIN = "untaped-orchestration==0.1.0"
IGNORE_RULES = {
    ".untaped/orchestration/**/.lock",
    ".untaped/orchestration/**/.DS_Store",
    ".untaped/orchestration/**/.*.untaped-tmp-*",
    ".untaped/orchestration/**/*~",
    ".untaped/orchestration/**/*.swp",
    ".untaped/orchestration/**/*.swo",
    ".untaped/orchestration/**/*.tmp",
    ".untaped/orchestration/**/.#*",
    ".untaped/orchestration/**/#*",
}


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_store_is_empty_public_decision_only_and_current() -> None:
    store = _toml(STORE / "store.toml")
    assert store["schema"] == "untaped.orchestration.store/v1"
    assert store["id"] == STORE_ID
    assert store["name"] == STORE_NAME
    assert store["visibility"] == "public"
    assert store["timezone"] == "UTC"
    assert store["capabilities"] == {"active_tasks": False}
    assert _toml(STORE / "registry.toml") == {
        "schema": "untaped.orchestration.registry/v1",
        "store_id": STORE_ID,
    }

    assert not (STORE / "tasks").exists()
    assert not (STORE / "decisions").exists()
    assert not (STORE / "archive").exists()
    assert not (STORE / "evidence").exists()
    assert not (STORE / "children").exists()

    view = (STORE / "views" / "decisions.md").read_text(encoding="utf-8")
    revision = REVISION_RE.search(view)
    assert revision is not None
    assert f"# {STORE_NAME} — Decisions" in view
    assert "_No items._" in view


def test_pointer_and_instructions_define_safe_decision_ownership() -> None:
    pointer = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "../.untaped/orchestration/views/decisions.md" in pointer
    assert "untaped-orchestration" in pointer
    assert "revision guard" in pointer
    assert "check --local" in pointer
    assert "fmt --check --local" in pointer
    assert "render --check" in pointer
    assert "check` and `render" in pointer

    guidance = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split()).lower()
    for phrase in (
        "decision-only",
        "revision guard",
        "--force-current",
        "generated",
        "never tool input",
        "check --local",
        "fmt --check --local",
        "render --check",
        "not hand edits",
    ):
        assert phrase in guidance

    store_guidance = (STORE / "AGENTS.md").read_text(encoding="utf-8")
    assert "Use `untaped-orchestration` for all canonical reads and writes" in store_guidance
    assert "Do not read generated\nviews as tool input" in store_guidance
    assert "preserve revision guards" in store_guidance
    assert (STORE / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"


def test_ignore_rules_are_exactly_the_store_scoped_runtime_rules() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    orchestration_rules = {line for line in lines if line.startswith(".untaped/orchestration/")}
    assert orchestration_rules == IGNORE_RULES


def test_workflow_is_read_only_path_filtered_and_uses_only_the_release() -> None:
    path = ROOT / ".github" / "workflows" / "orchestration.yml"
    text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"

    job = workflow["jobs"]["validate-orchestration"]
    assert job["timeout-minutes"] <= 10
    steps = job["steps"]
    checkout = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    setup_uv = next(
        step for step in steps if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    )
    assert checkout["uses"] == "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
    assert checkout["with"]["persist-credentials"] is False
    assert setup_uv["uses"] == "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e"
    assert setup_uv["with"]["version"] == "0.11.19"

    commands = [step["run"] for step in steps if "run" in step]
    prefix = f"uvx --python 3.14 --from '{ORCHESTRATION_PIN}' untaped-orchestration"
    assert commands == [
        f"{prefix} check --local",
        f"{prefix} fmt --check --local",
        f"{prefix} render --check",
    ]
    assert "uv sync" not in text
    assert "PYTHONPATH" not in text
    assert "--editable" not in text

    paths = workflow[True]["pull_request"]["paths"]
    for required in (
        ".untaped/orchestration/**",
        ".github/workflows/orchestration.yml",
        ".gitignore",
        "AGENTS.md",
        "docs/decisions.md",
        "tests/unit/test_orchestration_adoption.py",
    ):
        assert required in paths


def test_repository_contains_no_migration_or_placeholder_state() -> None:
    assert not (ROOT / "docs" / "orchestration-migration").exists()
    assert not list(ROOT.glob("**/migration*.toml"))
    assert not list(ROOT.glob("**/coverage.toml"))
    assert not list(ROOT.glob("**/import.toml"))
    tracked_store_files = {
        path.relative_to(ROOT).as_posix()
        for path in STORE.rglob("*")
        if path.is_file() and path.name != ".lock"
    }
    assert tracked_store_files == {
        ".untaped/orchestration/AGENTS.md",
        ".untaped/orchestration/CLAUDE.md",
        ".untaped/orchestration/registry.toml",
        ".untaped/orchestration/store.toml",
        ".untaped/orchestration/views/decisions.md",
    }

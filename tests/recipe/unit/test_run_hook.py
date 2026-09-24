"""Tests for the hook-run application use case.

Option validation (``--file``/content rules, missing content files) is pinned
at the CLI in ``test_cli.py``; these tests cover what the executor receives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.capabilities.recipe.application.run_hook import RunHook, TransformHookRun, select_verb
from untaped.capabilities.recipe.domain.plan import HookDebugResult, Verdict


class _DebugExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        self.calls.append(
            {
                "hook": hook,
                "content": content,
                "local_hook_project": local_hook_project,
                "target": target,
                "file": file,
                "inputs": inputs,
                "args": args,
                "capture_diagnostics": capture_diagnostics,
            }
        )
        return HookDebugResult(result=content + "!", diagnostics="diagnostic\n")

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        self.calls.append({"hook": hook, "target": target})
        return HookDebugResult(result=Verdict(status="pass"), diagnostics="")


def test_run_hook_transform_reads_target_file_and_invokes_executor(tmp_path: Path) -> None:
    executor = _DebugExecutor()
    target = tmp_path / "target"
    target.mkdir()
    (target / "config.txt").write_text("before")

    result = RunHook(executor).run(
        "sample",
        kind="transform",
        local_hook_project=None,
        target=target,
        file=Path("config.txt"),
        content=None,
        content_file=None,
        inputs={"enabled": True},
        args={"count": 3},
    )

    assert isinstance(result, TransformHookRun)
    assert result.before == "before"
    assert result.content == "before!"
    assert result.diagnostics == "diagnostic\n"
    assert executor.calls == [
        {
            "hook": "sample",
            "content": "before",
            "local_hook_project": None,
            "target": target.resolve(),
            "file": target.resolve() / "config.txt",
            "inputs": {"enabled": True},
            "args": {"count": 3},
            "capture_diagnostics": True,
        }
    ]


def test_run_hook_absolutizes_relative_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `hook run --target app-alpha` (relative) must reach the executor as an
    # absolute directory so the hook never depends on the worker's cwd.
    (tmp_path / "app-alpha").mkdir()
    monkeypatch.chdir(tmp_path)
    executor = _DebugExecutor()

    result = RunHook(executor).run(
        "sample",
        kind="validate",
        local_hook_project=None,
        target=Path("app-alpha"),
        file=None,
        content=None,
        content_file=None,
        inputs={},
        args={},
    )

    assert result.target.is_absolute()
    assert executor.calls == [{"hook": "sample", "target": (tmp_path / "app-alpha").resolve()}]


@pytest.mark.parametrize(
    ("exports", "file_given", "kind", "verb"),
    [
        ({"transform"}, False, None, "transform"),
        ({"validate"}, False, None, "validate"),
        ({"transform", "validate"}, True, None, "transform"),
        ({"transform", "validate"}, False, "validate", "validate"),
    ],
)
def test_select_verb_uses_single_export_then_kind_then_file(
    exports: set[str], file_given: bool, kind: str | None, verb: str
) -> None:
    assert select_verb(frozenset(exports), file_given=file_given, kind=kind) == verb


def test_select_verb_requires_kind_or_file_for_dual_export() -> None:
    with pytest.raises(ValueError, match="ambiguous hook verb"):
        select_verb(frozenset({"transform", "validate"}), file_given=False, kind=None)

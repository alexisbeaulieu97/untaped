"""Tests for uv-managed hook projects and worker execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from importlib.metadata import version
from io import StringIO
from pathlib import Path
from threading import Barrier, Event
from typing import Literal

import pytest
from packaging.version import Version
from pydantic import ValidationError

import untaped.capabilities.recipe.infrastructure.hook_worker_client as worker_client
from untaped.capabilities.recipe.domain.hook_project import ensure_hook_supports
from untaped.capabilities.recipe.domain.pack import PackManifest
from untaped.capabilities.recipe.domain.plan import Verdict
from untaped.capabilities.recipe.infrastructure.hook_executor import HookExecutor
from untaped.capabilities.recipe.infrastructure.hook_resolver import (
    BuiltinHookRef,
    HookResolver,
    UvHookRef,
)
from untaped.capabilities.recipe.infrastructure.hook_worker_client import (
    HookWorkerCallResult,
    HookWorkerResponse,
    UvHookWorker,
    UvHookWorkerPool,
)
from untaped.capabilities.recipe.infrastructure.pack_files import read_hook_project


def _write_hook_project(
    root: Path,
    *,
    hooks: dict[str, str],
    package: str = "project_hooks",
    kind: Literal["transform", "validate"] | None = None,
    exports: tuple[Literal["transform", "validate"], ...] = ("transform",),
    lock: bool = True,
    dependencies: list[str] | None = None,
    requires_hook_api: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "hooks").mkdir(parents=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    for module in hooks.values():
        module_path = root / "src" / Path(*module.split(".")).with_suffix(".py")
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(_hook_source(exports))
    hook_row_values = []
    for public_name, module in sorted(hooks.items()):
        if kind is None:
            hook_row_values.append(f'"{public_name}" = {{ module = "{module}" }}')
        else:
            hook_row_values.append(f'"{public_name}" = {{ kind = "{kind}", module = "{module}" }}')
    hook_rows = "\n".join(hook_row_values)
    dependency_rows = ", ".join(json.dumps(dependency) for dependency in dependencies or [])
    tool_table = (
        f'[tool.untaped_recipe]\nrequires_hook_api = "{requires_hook_api}"\n\n'
        if requires_hook_api is not None
        else ""
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{root.name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        f"dependencies = [{dependency_rows}]\n\n"
        f"{tool_table}"
        "[tool.untaped_recipe.hooks]\n"
        f"{hook_rows}\n"
    )
    if lock:
        (root / "uv.lock").write_text("version = 1\n")


def _hook_project(data: dict[str, object]) -> PackManifest:
    return PackManifest.from_pyproject(data, source=Path("pyproject.toml"), require_pack=False)


def _hook_source(exports: tuple[Literal["transform", "validate"], ...]) -> str:
    parts: list[str] = []
    if "transform" in exports:
        parts.append(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        )
    if "validate" in exports:
        parts.append(
            "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n"
        )
    return "\n".join(parts)


def test_hook_project_metadata_validates_pyproject_hook_table() -> None:
    metadata = _hook_project(
        {
            "tool": {
                "untaped_recipe": {
                    "hooks": {
                        "ansible.add_play_collections": {
                            "module": "project_hooks.hooks.add_play_collections",
                        }
                    }
                }
            }
        }
    )

    assert metadata.hooks["ansible.add_play_collections"].module == (
        "project_hooks.hooks.add_play_collections"
    )

    with pytest.raises(ValueError, match="invalid hook name"):
        _hook_project({"tool": {"untaped_recipe": {"hooks": {"bad-name": {"module": "pkg.hook"}}}}})

    with pytest.raises(ValueError, match="module is required"):
        _hook_project({"tool": {"untaped_recipe": {"hooks": {"check": {}}}}})

    with pytest.raises(ValueError, match="extra_forbidden"):
        _hook_project(
            {
                "tool": {
                    "untaped_recipe": {
                        "hooks": {"check": {"kind": "template", "module": "pkg.hook"}}
                    }
                }
            }
        )


def test_read_hook_project_rejects_unknown_hook_fields(tmp_path: Path) -> None:
    _write_hook_project(tmp_path, hooks={"x": "project_hooks.hooks.x"}, kind="transform")

    with pytest.raises(ValueError, match="extra_forbidden"):
        read_hook_project(tmp_path)


def test_hook_resolver_uses_recipe_local_then_builtin(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"pick": "local_hooks.hooks.pick"},
        package="local_hooks",
        exports=("validate",),
    )
    resolver = HookResolver()

    local = resolver.resolve("pick", recipe_dir)
    assert isinstance(local, UvHookRef)
    assert local.project_root == recipe_dir
    assert local.module == "local_hooks.hooks.pick"
    # Exports come from an AST scan of the module, not from metadata.
    assert local.exports == frozenset({"validate"})
    with pytest.raises(ValueError, match=r"does not export a transform\(\) function"):
        ensure_hook_supports(local.exports, "pick", verb="transform")

    builtin = resolver.resolve("yaml_edit", recipe_dir)
    assert isinstance(builtin, BuiltinHookRef)
    assert builtin.exports == frozenset({"transform"})


@pytest.mark.parametrize(
    "dependency",
    [
        "untaped>=4.0.0,<5",
        "untaped-recipe>=0.7",
        "Untaped_Recipe[hooks]>=0.7; python_version >= '3.14'",
        "untaped-recipe @ git+https://example.invalid/untaped-recipe.git",
        "untaped[recipe]>=4.0.0,<5",
    ],
)
def test_hook_resolver_rejects_runtime_cli_dependency(tmp_path: Path, dependency: str) -> None:
    _write_hook_project(
        tmp_path, hooks={"check": "project_hooks.hooks.check"}, dependencies=[dependency]
    )

    with pytest.raises(
        ValueError,
        match=r"must not depend on (?:untaped|untaped-recipe) at runtime",
    ) as exc_info:
        HookResolver().resolve("check", tmp_path)
    installed = Version(version("untaped"))
    assert "dependency-groups.dev" in str(exc_info.value)
    assert f"untaped>={installed.public},<{installed.major + 1}" in str(exc_info.value)


@pytest.mark.parametrize(
    ("options", "remove_module", "match"),
    [
        ({"lock": False}, False, r"missing uv\.lock"),
        (
            {"requires_hook_api": ">=99"},
            False,
            r"requires hook API >=99, but the untaped recipe capability provides 0\.10\.0",
        ),
        ({}, True, "hook module file not found"),
    ],
)
def test_hook_resolver_rejects_broken_hook_projects(
    tmp_path: Path,
    options: dict[str, object],
    remove_module: bool,
    match: str,
) -> None:
    _write_hook_project(tmp_path, hooks={"check": "project_hooks.hooks.check"}, **options)
    if remove_module:
        (tmp_path / "src" / "project_hooks" / "hooks" / "check.py").unlink()

    with pytest.raises(ValueError, match=match):
        HookResolver().resolve("check", tmp_path)


def test_hook_project_metadata_rejects_invalid_dependency_declarations() -> None:
    with pytest.raises(ValueError, match=r"\[project\]\.dependencies entry"):
        _hook_project(
            {
                "project": {"dependencies": ["not a valid @@@ requirement"]},
                "tool": {
                    "untaped_recipe": {
                        "hooks": {
                            "check": {
                                "module": "project_hooks.hooks.check",
                            }
                        }
                    }
                },
            }
        )


def test_hook_resolver_ignores_unrelated_local_project_contract_for_builtin(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={},
        dependencies=["untaped-recipe>=0.8"],
    )

    ref = HookResolver().resolve("yaml_edit", recipe_dir)

    assert isinstance(ref, BuiltinHookRef)
    assert ref.name == "yaml_edit"


def test_hook_resolver_caches_metadata_for_apply_lifetime(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"check": "project_hooks.hooks.check"})
    resolver = HookResolver()

    first = resolver.resolve("check", recipe_dir)
    (recipe_dir / "pyproject.toml").write_text("not toml = [\n")
    second = resolver.resolve("check", recipe_dir)

    assert isinstance(first, UvHookRef)
    assert isinstance(second, UvHookRef)
    assert second.module == "project_hooks.hooks.check"


def test_worker_response_validation_rejects_malformed_protocol_rows() -> None:
    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"ok": True, "result": "value"})

    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"id": "1", "ok": False})

    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"id": "1", "ok": False, "error": "bad", "result": ""})

    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"id": 1, "ok": True, "result": "value"})


def test_uv_hook_worker_reports_missing_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_popen(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("uv")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    with pytest.raises(ValueError, match="uv executable not found"):
        UvHookWorker(tmp_path)


def test_uv_hook_worker_excludes_dev_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    call_kwargs: list[dict[str, object]] = []

    def popen(args: list[str], **kwargs: object) -> _FakeProcess:
        calls.append(args)
        call_kwargs.append(kwargs)
        return _FakeProcess(stdout="")

    monkeypatch.setattr(subprocess, "Popen", popen)

    UvHookWorker(tmp_path)

    assert calls
    assert "--no-dev" in calls[0]
    assert calls[0].index("--no-dev") < calls[0].index("python")
    assert call_kwargs[0]["start_new_session"] is True


def test_uv_hook_worker_times_out_and_closes_hung_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _SlowProcess(delay=0.2)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path, hook_timeout_seconds=0.01)

    start = time.monotonic()
    with pytest.raises(worker_client.FatalHookWorkerError, match="timed out"):
        worker.request({"kind": "transform", "module": "hooks.sample"})

    assert time.monotonic() - start < 0.15
    assert fake.killed


def test_hook_timeout_starts_after_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _SlowProcess(
        ready_delay=0.1,
        lines=['{"id": "1", "ok": true, "result": "after"}\n'],
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path, hook_timeout_seconds=0.05, startup_timeout_seconds=5)

    result = worker.request({"kind": "transform", "module": "hooks.sample"})

    assert result.result == "after"


def test_startup_timeout_names_environment_not_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _SlowProcess(delay=0.2, ready=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path, hook_timeout_seconds=60, startup_timeout_seconds=0.01)

    with pytest.raises(worker_client.FatalHookWorkerError) as exc_info:
        worker.request({"kind": "transform", "module": "hooks.sample"})

    message = str(exc_info.value)
    assert "not ready" in message
    assert "environment" in message
    assert "hook worker timed out after" not in message
    assert fake.killed


def test_startup_notice_fires_once_per_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(
        stdout=(
            '{"id": "1", "ok": true, "result": "one"}\n{"id": "2", "ok": true, "result": "two"}\n'
        )
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    notices: list[Path] = []
    worker = UvHookWorker(tmp_path, startup_notice=notices.append)

    worker.request({"kind": "transform", "module": "hooks.sample"})
    worker.request({"kind": "transform", "module": "hooks.sample"})

    assert notices == [tmp_path]


def test_stale_lock_death_names_uv_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv_stderr = (
        "error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.\n"
    )
    fake = _FakeProcess(stdout="", ready=False, stderr=uv_stderr)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(worker_client.FatalHookWorkerError) as exc_info:
        worker.request({"kind": "transform", "module": "hooks.sample"}, settle_seconds=0.5)

    message = str(exc_info.value)
    assert message.startswith(f"pack lockfile is out of date — run 'uv lock' in {tmp_path}")
    assert "needs to be updated" in message
    assert "exited before ready" not in message


def test_worker_env_scrubs_virtual_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "outer-venv"))
    monkeypatch.setenv("PYTHONPATH", "existing")
    captured: dict[str, str] = {}

    def _capture(*args: object, **kwargs: object) -> _FakeProcess:
        captured.update(kwargs["env"])  # type: ignore[call-overload]
        return _FakeProcess(stdout='{"id": "1", "ok": true, "result": "after"}\n')

    monkeypatch.setattr(subprocess, "Popen", _capture)
    worker = UvHookWorker(tmp_path)

    result = worker.request({"kind": "transform", "module": "hooks.sample"})

    assert result.result == "after"
    assert "VIRTUAL_ENV" not in captured
    assert captured["PYTHONPATH"].endswith("existing")


def test_uv_hook_worker_rejects_non_json_serializable_request_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import datetime as dt

    fake = _FakeProcess(stdout='{"id": "1", "ok": true, "result": "after"}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(ValueError, match="is not JSON-serializable"):
        worker.request(
            {
                "kind": "transform",
                "module": "hooks.sample",
                "args": {"day": dt.date(2026, 6, 19)},
            }
        )

    assert fake.stdin.getvalue() == ""


def test_uv_hook_worker_discards_success_diagnostics_before_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(
        stdout=(
            '{"id": "1", "ok": true, "result": "after"}\n'
            '{"id": "2", "ok": false, "error": "failed"}\n'
        )
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)
    worker._stderr.put("success diagnostic\n")

    assert worker.request({"kind": "transform", "module": "hooks.sample"}).result == "after"
    worker._stderr.put("failure diagnostic\n")

    with pytest.raises(ValueError) as excinfo:
        worker.request({"kind": "transform", "module": "hooks.sample"})

    message = str(excinfo.value)
    assert "failed" in message
    assert "failure diagnostic" in message
    assert "success diagnostic" not in message


def test_uv_hook_worker_survives_non_utf8_stderr(tmp_path: Path) -> None:
    _write_hook_project(
        tmp_path,
        hooks={"noisy": "project_hooks.hooks.noisy"},
    )
    module = tmp_path / "src" / "project_hooks" / "hooks" / "noisy.py"
    module.write_text(
        "import sys\n\n"
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    sys.stderr.buffer.write(b'\\xff\\xfe\\n')\n"
        "    sys.stderr.flush()\n"
        "    return content\n"
    )
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    (tmp_path / "uv.lock").unlink()
    subprocess.run(["uv", "lock", "--project", str(tmp_path)], check=True, env=env)
    target = tmp_path / "target"
    target.mkdir()
    worker = UvHookWorker(tmp_path)
    try:
        result = worker.request(
            {
                "kind": "transform",
                "module": "project_hooks.hooks.noisy",
                "content": "before",
                "target": str(target),
                "file": str(target / "config.txt"),
                "inputs": {},
                "args": {},
            },
            diagnostic_limit=None,
            settle_seconds=0.1,
        )
    finally:
        worker.close()

    assert result.result == "before"
    assert "\ufffd\ufffd" in result.diagnostics


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_close_kills_the_whole_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "child.pid"
    script = tmp_path / "ignore_term.py"
    script.write_text(
        "import pathlib\n"
        "import signal\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "while True:\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )

    def start(self: UvHookWorker) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(script), str(marker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )

    monkeypatch.setattr(UvHookWorker, "_start", start)
    worker = UvHookWorker(tmp_path)
    pgid = os.getpgid(worker._process.pid)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()

        worker.close()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)


class _CannedWorkers:
    """Worker pool stand-in that records payloads and returns one canned reply."""

    def __init__(self, result: object, *, diagnostics: str = "", warnings: tuple[str, ...] = ()):
        self.reply = HookWorkerCallResult(result=result, diagnostics=diagnostics, warnings=warnings)
        self.payloads: list[dict[str, object]] = []

    def request(
        self,
        ref: UvHookRef,
        payload: dict[str, object],
        *,
        diagnostic_limit: int | None = 4000,
        settle_seconds: float = 0,
    ) -> HookWorkerCallResult:
        self.payloads.append(payload)
        return self.reply


def test_hook_executor_dispatches_builtin_without_worker(tmp_path: Path) -> None:
    workers = _CannedWorkers("unused")

    result = HookExecutor(HookResolver(), workers=workers).transform(
        "yaml_edit",
        "enabled: false\n",
        local_hook_project=None,
        target=tmp_path,
        file=tmp_path / "config.yml",
        inputs={},
        args={"edits": [{"op": "set", "path": ["enabled"], "value": True}]},
    )

    assert "enabled: true" in result.result
    assert result.diagnostics == ""
    assert workers.payloads == []


@pytest.mark.parametrize("capture_diagnostics", [False, True])
def test_hook_executor_sends_external_transform_to_worker(
    tmp_path: Path, capture_diagnostics: bool
) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"suffix": "project_hooks.hooks.suffix"})
    workers = _CannedWorkers("after\n", diagnostics="diagnostic\n")

    result = HookExecutor(HookResolver(), workers=workers).transform(
        "suffix",
        "before\n",
        local_hook_project=recipe_dir,
        target=tmp_path / "target",
        file=tmp_path / "target" / "local.yml",
        inputs={"service": "api"},
        args={"flag": True},
        capture_diagnostics=capture_diagnostics,
    )

    assert result.result == "after\n"
    assert result.diagnostics == ("diagnostic\n" if capture_diagnostics else "")
    [payload] = workers.payloads
    assert payload["kind"] == "transform"
    assert payload["content"] == "before\n"
    assert payload["target"] == str(tmp_path / "target")
    assert payload["file"] == str(tmp_path / "target" / "local.yml")


def _run_worker_script(
    tmp_path: Path, modules: dict[str, str], request: dict[str, object]
) -> tuple[dict[str, object], str]:
    """Send one request to the real worker script and return its response and stderr."""
    for relative, source in {"worker_hooks/__init__.py": "", **modules}.items():
        path = tmp_path / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    worker = Path(__file__).parents[3] / "src/untaped/capabilities/recipe/_worker/hook_worker.py"
    proc = subprocess.run(
        [sys.executable, str(worker)],
        cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path / "src")},
        input=json.dumps({"id": "1", "target": str(tmp_path), "args": {}, **request}) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    ready, response = proc.stdout.splitlines()
    assert json.loads(ready) == {"ready": True}
    return json.loads(response), proc.stderr


def test_worker_script_executes_hooks_and_redirects_prints_to_stderr(tmp_path: Path) -> None:
    response, stderr = _run_worker_script(
        tmp_path,
        {
            "worker_hooks/sample.py": (
                "print('diagnostic from import')\n"
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    print('diagnostic from hook')\n"
                "    return helpers.dump_yaml(\n"
                "        {'content': content, 'suffix': inputs['suffix']},\n"
                "        options={'explicit_start': True, 'width': 4096},\n"
                "    )\n"
            )
        },
        {
            "kind": "transform",
            "module": "worker_hooks.sample",
            "content": "before ",
            "inputs": {"suffix": "after"},
            "file": str(tmp_path / "local.yml"),
        },
    )

    assert response == {
        "id": "1",
        "ok": True,
        "result": "---\ncontent: 'before '\nsuffix: after\n",
        "warnings": [],
    }
    assert "diagnostic from import" in stderr
    assert "diagnostic from hook" in stderr


def test_worker_script_prefers_cli_sibling_modules_over_hook_env_package(tmp_path: Path) -> None:
    constants = (
        "ID",
        "KIND",
        "MODULE",
        "VALIDATE",
        "TRANSFORM",
        "INPUTS",
        "TARGET",
        "ARGS",
        "CONTENT",
        "FILE",
    )
    response, stderr = _run_worker_script(
        tmp_path,
        {
            "worker_hooks/sample.py": (
                "def validate(*, inputs, target, args, helpers):\n"
                "    return helpers.pass_('from real worker protocol')\n"
            ),
            "untaped_recipe/__init__.py": "",
            "untaped_recipe/worker_protocol.py": "".join(
                f"{name} = 'bad_{name.lower()}'\n" for name in constants
            ),
            "untaped_recipe/helpers.py": "raise RuntimeError('fake helpers imported')\n",
        },
        {"kind": "validate", "module": "worker_hooks.sample", "inputs": {}},
    )

    assert response == {
        "id": "1",
        "ok": True,
        "result": {"status": "pass", "message": "from real worker protocol"},
        "warnings": [],
    }
    assert "fake helpers imported" not in stderr


def test_worker_script_rejects_invalid_validate_return_object(tmp_path: Path) -> None:
    response, stderr = _run_worker_script(
        tmp_path,
        {
            "worker_hooks/bad_validate.py": (
                "def validate(*, inputs, target, args, helpers):\n    return object()\n"
            )
        },
        {"kind": "validate", "module": "worker_hooks.bad_validate", "inputs": {}},
    )

    assert response["ok"] is False
    assert "invalid validate verdict" in str(response["error"])
    assert "invalid validate verdict" in stderr


def test_hook_executor_validates_worker_verdicts_and_collects_warnings(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir, hooks={"check": "project_hooks.hooks.check"}, exports=("validate",)
    )

    def validate(workers: _CannedWorkers) -> object:
        return HookExecutor(HookResolver(), workers=workers).validate(
            "check", local_hook_project=recipe_dir, target=tmp_path, inputs={}, args={}
        )

    with pytest.raises(ValueError, match="status"):
        validate(_CannedWorkers({"status": "warn", "message": "check this"}))
    result = validate(
        _CannedWorkers(
            {"status": "skip", "message": "not applicable"}, warnings=("noticed something",)
        )
    )
    assert result.result == Verdict(status="skip", message="not applicable")
    assert result.warnings == ("noticed something",)


_READY_LINE = '{"ready": true}\n'


class _FakeProcess:
    def __init__(self, *, stdout: str, ready: bool = True, stderr: str = "") -> None:
        self.stdin = StringIO()
        self.stdout = StringIO((_READY_LINE if ready else "") + stdout)
        self.stderr = StringIO(stderr)

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


class _BrokenStdin:
    def write(self, line: str) -> int:
        raise BrokenPipeError

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _SlowStdout:
    """Ready arrives after ``ready_delay`` (never if not ``ready``), then ``lines``, then stalls."""

    def __init__(self, delay: float, *, ready: bool, ready_delay: float, lines: list[str]) -> None:
        self._delay = delay
        self._ready_pending = ready
        self._ready_delay = ready_delay
        self._lines = list(lines)

    def readline(self) -> str:
        if self._ready_pending:
            self._ready_pending = False
            time.sleep(self._ready_delay)
            return _READY_LINE
        if self._lines:
            return self._lines.pop(0)
        time.sleep(self._delay)
        return ""


class _SlowProcess:
    def __init__(
        self,
        *,
        delay: float = 60,
        ready: bool = True,
        ready_delay: float = 0,
        lines: list[str] | None = None,
    ) -> None:
        self.stdin = StringIO()
        self.stdout = _SlowStdout(delay, ready=ready, ready_delay=ready_delay, lines=lines or [])
        self.stderr = StringIO()
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if self.killed:
            return 0
        raise subprocess.TimeoutExpired("slow", timeout)

    def terminate(self) -> None:
        self.killed = True

    def kill(self) -> None:
        self.killed = True


@pytest.mark.parametrize("shadowed", ["settings", "cli", "domain", "worker_protocol"])
def test_uv_hook_worker_launch_does_not_shadow_pack_top_level_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shadowed: str,
) -> None:
    project = tmp_path / "project"
    src = project / "src"
    (src / "worker_hooks").mkdir(parents=True)
    (src / "worker_hooks" / "__init__.py").write_text("")
    (src / "worker_hooks" / "sample.py").write_text(
        f"import {shadowed}\n\n"
        "def validate(*, inputs, target, args, helpers):\n"
        f"    return helpers.pass_({shadowed}.MARKER)\n"
    )
    (src / f"{shadowed}.py").write_text("MARKER = 'pack module'\n")
    calls: list[list[str]] = []

    def popen(args: list[str], **kwargs: object) -> _FakeProcess:
        calls.append(args)
        return _FakeProcess(stdout="")

    monkeypatch.setattr(subprocess, "Popen", popen)
    UvHookWorker(project)
    monkeypatch.undo()
    # Re-run the exact interpreter arguments the client launches uv with.
    python_args = calls[0][calls[0].index("python") + 1 :]
    proc = subprocess.run(
        [sys.executable, *python_args],
        cwd=project,
        env={"PYTHONPATH": str(src)},
        input=json.dumps(
            {
                "id": "1",
                "kind": "validate",
                "module": "worker_hooks.sample",
                "inputs": {},
                "target": str(tmp_path),
                "args": {},
            }
        )
        + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    lines = proc.stdout.splitlines()
    assert json.loads(lines[0]) == {"ready": True}, proc.stderr
    response = json.loads(lines[1])
    assert response["ok"] is True, response
    assert response["result"] == {"status": "pass", "message": "pack module"}


FatalError = worker_client.FatalHookWorkerError
_OK_LINE = '{"id": "1", "ok": true, "result": "after"}\n'


@pytest.mark.parametrize(
    ("stdout", "ready", "stderr", "broken_stdin", "error", "match"),
    [
        ("not-json\n", True, "", False, ValueError, "malformed hook worker response"),
        (
            '{"id": "wrong", "ok": true, "result": "after"}\n',
            True,
            "",
            False,
            ValueError,
            "response id mismatch",
        ),
        ("hello\n", False, "", False, FatalError, "malformed hook worker handshake"),
        ("", False, "", False, FatalError, "exited before ready"),
        ("", False, "ImportError: boom\n", False, FatalError, "exited before ready"),
        ("", True, "", True, FatalError, "exited before request"),
        ("", True, "", False, FatalError, "exited before response"),
    ],
)
def test_uv_hook_worker_protocol_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    ready: bool,
    stderr: str,
    broken_stdin: bool,
    error: type[Exception],
    match: str,
) -> None:
    fake = _FakeProcess(stdout=stdout, ready=ready, stderr=stderr)
    if broken_stdin:
        fake.stdin = _BrokenStdin()  # type: ignore[assignment]
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(error, match=match):
        worker.request({"kind": "transform", "module": "hooks.sample"}, settle_seconds=0.1)


@pytest.mark.parametrize(
    ("diagnostic_limit", "expected"),
    [(10_000, "12345\n67890"), (7, "67890")],
)
def test_uv_hook_worker_request_returns_result_json_payload_and_bounded_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnostic_limit: int,
    expected: str,
) -> None:
    payload_args = {
        "enabled": True,
        "count": 2,
        "labels": ["api", "worker"],
        "options": {"mode": "strict", "empty": None},
    }
    fake = _FakeProcess(stdout=_OK_LINE, stderr="12345\n67890\n")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    result = worker.request(
        {"kind": "transform", "module": "hooks.sample", "args": payload_args},
        diagnostic_limit=diagnostic_limit,
        settle_seconds=0.05,
    )

    assert result.result == "after"
    assert expected in result.diagnostics
    assert len(result.diagnostics) <= diagnostic_limit
    assert json.loads(fake.stdin.getvalue())["args"] == payload_args


class _FakeWorker:
    """Stand-in for ``UvHookWorker`` that records its lifecycle."""

    def __init__(
        self,
        worker_id: int,
        respond: Callable[[_FakeWorker], object],
        *,
        hook_timeout_seconds: float,
        close_error: BaseException | None,
    ) -> None:
        self.worker_id = worker_id
        self.hook_timeout_seconds = hook_timeout_seconds
        self.calls = 0
        self.closed = False
        self._respond = respond
        self._close_error = close_error

    def request(
        self,
        payload: dict[str, object],
        *,
        diagnostic_limit: int | None = 4000,
        settle_seconds: float = 0,
    ) -> HookWorkerCallResult:
        self.calls += 1
        return HookWorkerCallResult(result=self._respond(self), diagnostics="")

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


def _fake_workers(
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[_FakeWorker], object] = lambda worker: worker.worker_id,
    *,
    close_errors: dict[int, BaseException] | None = None,
) -> list[_FakeWorker]:
    workers: list[_FakeWorker] = []

    def start(project_root: Path, *, hook_timeout_seconds: float, **kwargs: object) -> _FakeWorker:
        worker_id = len(workers) + 1
        worker = _FakeWorker(
            worker_id,
            respond,
            hook_timeout_seconds=hook_timeout_seconds,
            close_error=(close_errors or {}).get(worker_id),
        )
        workers.append(worker)
        return worker

    monkeypatch.setattr(worker_client, "UvHookWorker", start)
    return workers


def _sample_ref(tmp_path: Path) -> UvHookRef:
    return UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )


def test_uv_hook_worker_pool_reuses_idle_workers_and_passes_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers = _fake_workers(monkeypatch)
    ref = _sample_ref(tmp_path)

    with UvHookWorkerPool(max_workers_per_project=3, hook_timeout_seconds=12) as pool:
        results = [pool.request(ref, {"kind": "transform"}).result for _ in range(3)]

    assert results == [1, 1, 1]
    assert [(worker.hook_timeout_seconds, worker.closed) for worker in workers] == [(12, True)]


def test_uv_hook_worker_pool_leases_parallel_workers_and_close_survives_one_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = Barrier(3)

    def wait_for_all(worker: _FakeWorker) -> int:
        barrier.wait(timeout=5)
        return worker.worker_id

    workers = _fake_workers(
        monkeypatch,
        wait_for_all,
        close_errors={2: BrokenPipeError("worker stdin already closed")},
    )
    ref = _sample_ref(tmp_path)
    pool = UvHookWorkerPool(max_workers_per_project=3)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = [
            result.result
            for result in executor.map(lambda _: pool.request(ref, {"kind": "transform"}), range(3))
        ]

    assert sorted(results) == [1, 2, 3]
    with pytest.raises(BrokenPipeError, match="worker stdin already closed"):
        pool.close()
    assert [worker.closed for worker in workers] == [True, True, True]


@pytest.mark.parametrize(
    ("failure", "replaced"),
    [
        (FatalError("malformed hook worker response"), True),
        (ValueError("validate hook failed"), False),
    ],
)
def test_uv_hook_worker_pool_retires_workers_only_after_fatal_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    replaced: bool,
) -> None:
    def fail_first_call(worker: _FakeWorker) -> int:
        if worker.worker_id == 1 and worker.calls == 1:
            raise failure
        return worker.worker_id

    workers = _fake_workers(monkeypatch, fail_first_call)
    ref = _sample_ref(tmp_path)

    with UvHookWorkerPool(max_workers_per_project=1) as pool:
        with pytest.raises(ValueError, match=str(failure)):
            pool.request(ref, {"kind": "validate"})
        assert pool.request(ref, {"kind": "validate"}).result == (2 if replaced else 1)

    assert len(workers) == (2 if replaced else 1)
    assert all(worker.closed for worker in workers)


def test_uv_hook_worker_pool_wakes_waiters_after_fatal_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_request_started = Event()
    fail_first_request = Event()

    def respond(worker: _FakeWorker) -> int:
        if worker.worker_id == 1:
            first_request_started.set()
            assert fail_first_request.wait(timeout=5)
            raise FatalError("hook worker timed out")
        return worker.worker_id

    workers = _fake_workers(monkeypatch, respond)
    ref = _sample_ref(tmp_path)

    with (
        UvHookWorkerPool(max_workers_per_project=1) as pool,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(pool.request, ref, {"kind": "transform"})
        assert first_request_started.wait(timeout=5)
        second = executor.submit(pool.request, ref, {"kind": "transform"})
        time.sleep(0.05)

        fail_first_request.set()

        with pytest.raises(FatalError, match="timed out"):
            first.result(timeout=5)
        try:
            assert second.result(timeout=1).result == 2
        except FutureTimeoutError as exc:
            msg = "waiting request was not woken after fatal retirement"
            raise AssertionError(msg) from exc

    assert [worker.closed for worker in workers] == [True, True]

"""Tests for the built-in yaml_edit hook, bulk planning, transactional writes, and uv probes."""

from __future__ import annotations

import errno
import subprocess
from pathlib import Path

import pytest

import untaped.capabilities.recipe.infrastructure.file_writer as file_writer_module
from untaped.capabilities.recipe.application.apply_recipe import ApplyRecipe
from untaped.capabilities.recipe.application.run_bulk import RunBulkApply
from untaped.capabilities.recipe.application.targets import Target
from untaped.capabilities.recipe.builtins.hooks import yaml_edit
from untaped.capabilities.recipe.domain.plan import FileChange
from untaped.capabilities.recipe.domain.recipe import Recipe
from untaped.capabilities.recipe.hook_worker import HookHelpers, dump_yaml, load_yaml
from untaped.capabilities.recipe.infrastructure import uv_project
from untaped.capabilities.recipe.infrastructure.file_writer import ApplyWriteError, flush_changes
from untaped.capabilities.recipe.infrastructure.hook_executor import HookExecutor
from untaped.capabilities.recipe.infrastructure.hook_resolver import HookResolver
from untaped.capabilities.recipe.infrastructure.hook_worker_client import UvHookWorkerPool


def _runner() -> RunBulkApply:
    return RunBulkApply(ApplyRecipe(HookExecutor(HookResolver(), workers=UvHookWorkerPool())))


def test_builtin_yaml_edit_preserves_round_trip_yaml(tmp_path: Path) -> None:
    helpers = HookHelpers()

    result = yaml_edit.transform(
        "# top\nservices:\n  - name: api\n    config:\n      old: true\n"
        '  - name: web\nquoted: "keep me"\n',
        inputs={"owner": "platform"},
        target=tmp_path,
        file=tmp_path / "config.yml",
        args={
            "edits": [
                {
                    "op": "merge",
                    "path": ["services", {"where": {"name": "api"}}, "config"],
                    "value": {"owner": "{{ owner }}"},
                },
                {
                    "op": "delete",
                    "path": ["services", {"where": {"name": "web"}}],
                },
                {"op": "set", "path": ["enabled"], "value": True},
            ]
        },
        helpers=helpers,
    )
    assert "# top" in result
    assert 'quoted: "keep me"' in result
    assert "owner: platform" in result
    assert "enabled: true" in result
    assert "name: web" not in result

    assert dump_yaml(load_yaml(result)) == result

    empty = yaml_edit.transform(
        "",
        inputs={},
        target=tmp_path,
        file=tmp_path / "empty.yml",
        args={"edits": [{"op": "set", "path": ["enabled"], "value": True}]},
        helpers=helpers,
    )
    assert "enabled: true" in empty


def test_builtin_yaml_edit_forwards_unknown_token_policy(tmp_path: Path) -> None:
    helpers = HookHelpers()
    args = {
        "unknown_tokens": "keep",
        "edits": [
            {
                "op": "set",
                "path": ["ref"],
                "value": "${{ github.ref }}",
            }
        ],
    }

    result = yaml_edit.transform(
        "{}\n",
        inputs={},
        target=tmp_path,
        file=tmp_path / "config.yml",
        args=args,
        helpers=helpers,
    )

    assert "ref: ${{ github.ref }}" in result


def _ensure(
    content: str, edit: dict[str, object], *, inputs: dict[str, object] | None = None
) -> str:
    return yaml_edit.transform(
        content,
        inputs=inputs or {},
        target=Path("."),
        file=Path("requirements.yml"),
        args={"edits": [edit]},
        helpers=HookHelpers(),
    )


@pytest.mark.parametrize(
    ("content", "edit", "expected"),
    [
        pytest.param(
            "---\ncollections:\n  - name: community.general\n",
            {
                "op": "ensure",
                "path": ["collections"],
                "value": {"name": "acme.required"},
                "match": ["name"],
            },
            "collections:\n- name: community.general\n- name: acme.required\n",
            id="block-list-append-by-name",
        ),
        pytest.param(
            "deps: [alpha, beta]  # inline\n",
            {"op": "ensure", "path": ["deps"], "value": "gamma"},
            "deps: [alpha, beta, gamma] # inline\n",
            id="flow-string-list-scalar-append",
        ),
        pytest.param(
            "# top comment\ndefaults: &def\n  retries: 3\nservers:\n  - <<: *def\n    name: a\n",
            {"op": "ensure", "path": ["servers"], "value": {"name": "b"}, "match": ["name"]},
            "# top comment\ndefaults: &def\n  retries: 3\n"
            "servers:\n- <<: *def\n  name: a\n- name: b\n",
            id="anchors-comments-merge-keys-survive",
        ),
        pytest.param(
            "---\nother: 1\n",
            {
                "op": "ensure",
                "path": ["collections"],
                "value": {"name": "acme.required"},
                "match": ["name"],
            },
            "other: 1\ncollections:\n- name: acme.required\n",
            id="missing-collections-key-created",
        ),
        pytest.param(
            "settings:\n  a: 1\n",
            {"op": "ensure", "path": ["settings"], "value": {"a": 9, "b": 2}},
            "settings:\n  a: 1\n  b: 2\n",
            id="mapping-set-if-absent",
        ),
        pytest.param(
            "top: 1\n",
            {"op": "ensure", "path": ["settings"], "value": {"a": 1, "b": 2}},
            "top: 1\nsettings:\n  a: 1\n  b: 2\n",
            id="missing-mapping-path-created",
        ),
        pytest.param(
            "items:\n  - plain\n  - name: keep\n",
            {"op": "ensure", "path": ["items"], "value": {"name": "plain"}, "match": ["name"]},
            "items:\n- plain\n- name: keep\n- name: plain\n",
            id="mixed-entry-mapping-never-matches-string",
        ),
        pytest.param(
            "top: 1\n",
            {"op": "ensure", "path": ["tags"], "value": "release"},
            "top: 1\ntags:\n- release\n",
            id="scalar-into-missing-path-creates-list",
        ),
        pytest.param(
            "enabled: 1\n",
            {"op": "set", "path": ["enabled"], "value": True},
            "enabled: true\n",
            id="set-bool-over-int-is-a-change",
        ),
        pytest.param(
            "top: 1\n",
            {"op": "set", "path": ["spec", "replicas"], "value": 1},
            "top: 1\nspec:\n  replicas: 1\n",
            id="set-missing-path",
        ),
        pytest.param(
            "settings:\n  a: 1\n",
            {"op": "merge", "path": ["settings"], "value": {"a": 2}},
            "settings:\n  a: 2\n",
            id="merge-different-value",
        ),
    ],
)
def test_builtin_yaml_edit_applies_real_changes(
    content: str,
    edit: dict[str, object],
    expected: str,
) -> None:
    assert _ensure(content, edit) == expected


@pytest.mark.parametrize(
    ("content", "edit"),
    [
        pytest.param(
            "---\ncollections:\n  - name: community.general\n",
            {
                "op": "ensure",
                "path": ["collections"],
                "value": {"name": "community.general"},
                "match": ["name"],
            },
            id="mapping-already-present",
        ),
        pytest.param(
            "deps: [alpha, beta]  # inline\n",
            {"op": "ensure", "path": ["deps"], "value": "beta"},
            id="scalar-already-present",
        ),
        pytest.param(
            "settings:\n  a: 1\n",
            {"op": "ensure", "path": ["settings"], "value": {"a": 9}},
            id="mapping-key-already-present",
        ),
        pytest.param(
            "---\nimage:   nginx  # pinned\nreplicas: 2\n",
            {"op": "set", "path": ["image"], "value": "nginx"},
            id="set-same-scalar",
        ),
        pytest.param(
            "spec:\n  replicas: 2\n",
            {"op": "set", "path": ["spec", "replicas"], "value": 2},
            id="set-same-nested-int",
        ),
        pytest.param(
            "items:\n  - name: a\n    tag: v1\n",
            {"op": "set", "path": ["items", {"where": {"name": "a"}}, "tag"], "value": "v1"},
            id="set-same-in-list-item",
        ),
        pytest.param(
            "settings:   {a: 1, b: two}\n",
            {"op": "merge", "path": ["settings"], "value": {"b": "two"}},
            id="merge-already-present",
        ),
    ],
)
def test_builtin_yaml_edit_noop_is_byte_identical(
    content: str,
    edit: dict[str, object],
) -> None:
    assert _ensure(content, edit) == content


def test_builtin_yaml_edit_ensure_renders_value_tokens() -> None:
    result = _ensure(
        "collections: []\n",
        {
            "op": "ensure",
            "path": ["collections"],
            "value": {"name": "{{ col }}"},
            "match": ["name"],
        },
        inputs={"col": "acme.web"},
    )
    assert result == "collections:\n- name: acme.web\n"


@pytest.mark.parametrize(
    ("content", "edit", "message"),
    [
        pytest.param(
            "tags: [a]\n",
            {"op": "ensure", "path": ["tags"], "value": "x", "match": ["name"]},
            "match requires a mapping value",
            id="scalar-value-forbids-match",
        ),
        pytest.param(
            "settings:\n  z: 1\n",
            {"op": "ensure", "path": ["settings"], "value": {"a": 1}, "match": ["a"]},
            "match is not valid for a mapping path",
            id="mapping-path-forbids-match",
        ),
        pytest.param(
            "x: []\n",
            {"op": "ensure", "path": ["x"], "value": [1, 2]},
            "value must be a scalar or mapping",
            id="list-value-rejected",
        ),
    ],
)
def test_builtin_yaml_edit_ensure_load_errors(
    content: str,
    edit: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _ensure(content, edit)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({}, "edits"),
        ({"edits": [{"op": "explode", "path": ["a"], "value": 1}]}, "invalid op"),
        (
            {
                "edits": [
                    {
                        "op": "set",
                        "path": ["items", {"where": {"name": "missing"}}],
                        "value": 1,
                    }
                ]
            },
            "no list item",
        ),
        (
            {"edits": [{"op": "set", "path": ["a"], "value": "{{ missing }}"}]},
            "template input",
        ),
    ],
)
def test_builtin_yaml_edit_reports_bad_args(
    args: dict[str, object],
    message: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=message):
        yaml_edit.transform(
            "items:\n  - name: api\n",
            inputs={},
            target=tmp_path,
            file=tmp_path / "config.yml",
            args=args,
            helpers=HookHelpers(),
        )


def test_parallel_bulk_plan_returns_ordered_errors_and_flushes_atomically(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("hello\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "template", "template": "template.txt", "dest": "nested/out.txt"}],
        }
    )
    good = tmp_path / "good"
    good.mkdir()
    missing = tmp_path / "missing"
    plans = _runner().plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=None,
        targets=[Target(path=good), Target(path=missing)],
        inputs={},
        parallel=2,
    )

    assert [plan.target for plan in plans] == [good, missing]
    assert [plan.status for plan in plans] == ["planned", "error"]
    flush_changes(plans[0].changes)
    assert (good / "nested" / "out.txt").read_text() == "hello\n"

    removable = good / "legacy.txt"
    removable.write_text("old\n")
    flush_changes(
        (
            FileChange(
                target=good,
                relative_path=Path("legacy.txt"),
                before="old\n",
                after=None,
            ),
        )
    )
    assert not removable.exists()


def test_bulk_plan_resolves_per_target_inputs_and_dedupes_repeated_targets(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("service={{ service }}\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {
                    "type": "str",
                    "required": True,
                    "from": ["{{ record.repo }}", "{{ target.name }}"],
                },
                "token": {
                    "type": "str",
                    "scope": "global",
                    "sensitive": True,
                    "required": True,
                },
            },
            "steps": [{"type": "template", "template": "template.txt", "dest": "out.txt"}],
        }
    )
    target = tmp_path / "api"
    target.mkdir()
    other = tmp_path / "worker"
    other.mkdir()
    plans = _runner().plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=None,
        targets=[
            Target(path=target, record={"repo": "first"}),
            Target(path=other, record={"repo": "second"}),
            Target(path=target, record={"repo": "third"}),
        ],
        inputs={"token": "secret"},
        parallel=3,
    )

    # A repeated target directory is planned once (first-seen record wins):
    # planning it twice would make the second flush fail "changed since planning".
    assert [plan.target for plan in plans] == [target, other]
    assert [plan.display_inputs["token"] for plan in plans] == ["***", "***"]
    assert [plan.display_inputs["service"] for plan in plans] == ["first", "second"]
    assert [plan.changes[0].after for plan in plans] == [
        "service=first\n",
        "service=second\n",
    ]


def test_bulk_plan_error_rows_preserve_resolved_input_display(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("service={{ service }} token={{ token }}\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "required": True, "from": "{{ target.name }}"},
                "token": {
                    "type": "str",
                    "scope": "global",
                    "sensitive": True,
                    "required": True,
                },
            },
            "steps": [{"type": "template", "template": "template.txt", "dest": "out.txt"}],
        }
    )
    missing = tmp_path / "missing"
    plans = _runner().plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=None,
        targets=[Target(path=missing)],
        inputs={"token": "secret"},
    )

    assert plans[0].status == "error"
    assert plans[0].display_inputs == {"service": "missing", "token": "***"}


def test_bulk_plan_input_resolution_errors_have_empty_inputs(tmp_path: Path) -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "required": True, "from": "{{ record.repo }}"},
            },
            "steps": [],
        }
    )
    target = tmp_path / "api"
    target.mkdir()
    plans = _runner().plan(
        recipe=recipe,
        recipe_dir=tmp_path,
        local_hook_project=None,
        targets=[Target(path=target)],
        inputs={},
    )

    assert plans[0].status == "error"
    assert plans[0].display_inputs == {}


def test_flush_changes_reports_rollback_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("one-before\n")
    second.write_text("two-before\n")
    original_replace = file_writer_module.os.replace

    def fail_second_write_and_first_rollback(source: Path, dest: Path) -> None:
        source_path = Path(source)
        dest_path = Path(dest)
        if ".rollback." in source_path.name:
            raise OSError("rollback denied")
        if dest_path.name == "two.txt":
            raise OSError("write failed")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_write_and_first_rollback)

    with pytest.raises(ApplyWriteError) as excinfo:
        flush_changes(
            (
                FileChange(
                    target=tmp_path,
                    relative_path=Path("one.txt"),
                    before="one-before\n",
                    after="one-after\n",
                ),
                FileChange(
                    target=tmp_path,
                    relative_path=Path("two.txt"),
                    before="two-before\n",
                    after="two-after\n",
                ),
            )
        )

    message = str(excinfo.value)
    assert "write failed" in message
    assert "rollback incomplete" in message
    assert "rollback denied" in message


def test_flush_changes_rejects_stale_files_without_overwriting(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    config = target / "config.yml"
    config.write_text("before\n")
    change = FileChange(
        target=target,
        relative_path=Path("config.yml"),
        before="before\n",
        after="after\n",
    )

    config.write_text("user edit\n")

    with pytest.raises(ApplyWriteError, match="changed since planning"):
        flush_changes((change,))
    assert config.read_text() == "user edit\n"


def test_flush_changes_stages_replacements_next_to_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    change = FileChange(
        target=target,
        relative_path=Path("nested/out.txt"),
        before=None,
        after="new\n",
    )
    original_replace = file_writer_module.os.replace
    observed: list[tuple[Path, Path]] = []

    def replace_requires_same_parent(src: Path, dst: Path) -> None:
        source = Path(src)
        destination = Path(dst)
        observed.append((source, destination))
        if source.parent != destination.parent:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        original_replace(source, destination)

    monkeypatch.setattr(file_writer_module.os, "replace", replace_requires_same_parent)

    flush_changes((change,))

    assert (target / "nested" / "out.txt").read_text() == "new\n"
    assert observed == [(observed[0][0], target / "nested" / "out.txt")]
    assert observed[0][0].parent == target / "nested"


def test_flush_changes_rejects_target_symlink_escape(tmp_path: Path) -> None:
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "linked").symlink_to(outside, target_is_directory=True)
    change = FileChange(
        target=target,
        relative_path=Path("linked/out.txt"),
        before=None,
        after="escaped\n",
    )

    with pytest.raises(ApplyWriteError, match="symlink"):
        flush_changes((change,))
    assert not (outside / "out.txt").exists()


def test_file_change_kind_reports_create_modify_and_remove(tmp_path: Path) -> None:
    target = tmp_path / "target"

    create = FileChange(target=target, relative_path=Path("a"), before=None, after="x")
    modify = FileChange(target=target, relative_path=Path("a"), before="x", after="y")
    remove = FileChange(target=target, relative_path=Path("a"), before="x", after=None)

    assert create.kind == "create"
    assert modify.kind == "modify"
    assert remove.kind == "remove"


def test_flush_changes_preserves_executable_mode(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    script = target / "run.sh"
    script.write_text("#!/bin/sh\necho old\n")
    script.chmod(0o755)

    flush_changes(
        (
            FileChange(
                target=target,
                relative_path=Path("run.sh"),
                before="#!/bin/sh\necho old\n",
                after="#!/bin/sh\necho new\n",
            ),
        )
    )

    assert script.read_text() == "#!/bin/sh\necho new\n"
    assert script.stat().st_mode & 0o777 == 0o755


def test_flush_changes_rolls_back_every_change_when_a_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    script = target / "run.sh"
    script.write_text("old\n")
    script.chmod(0o755)
    first = target / "first.txt"
    first.write_text("old first\n")
    (target / "second.txt").write_text("old second\n")
    original_replace = file_writer_module.os.replace

    def fail_second(src: Path, dst: Path) -> None:
        if Path(dst).name == "second.txt" and ".rollback." not in Path(src).name:
            raise OSError("disk full")
        original_replace(src, dst)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second)

    def change(name: str, before: str | None, after: str | None) -> FileChange:
        return FileChange(target=target, relative_path=Path(name), before=before, after=after)

    with pytest.raises(ApplyWriteError, match="disk full") as excinfo:
        flush_changes(
            (
                change("run.sh", "old\n", None),
                change("first.txt", "old first\n", "new first\n"),
                change("created/dir/out.txt", None, "new\n"),
                change("second.txt", "old second\n", "new second\n"),
            )
        )

    assert not excinfo.value.rollback_incomplete
    assert script.read_text() == "old\n"
    assert script.stat().st_mode & 0o777 == 0o755
    assert first.read_text() == "old first\n"
    assert (target / "second.txt").read_text() == "old second\n"
    assert not (target / "created").exists()


@pytest.mark.parametrize(
    ("returncode", "stderr", "message"),
    [
        (0, "", None),
        (
            2,
            "error: The lockfile at `uv.lock` needs to be updated\n",
            "lockfile is stale — run 'uv lock' in {root}: "
            "error: The lockfile at `uv.lock` needs to be updated",
        ),
        (
            2,
            "error: failed to fetch package metadata\n",
            "could not verify lockfile freshness in {root}: "
            "error: failed to fetch package metadata",
        ),
        (2, "", "could not verify lockfile freshness in {root}"),
        (None, "", "uv executable not found for project lock"),
    ],
)
def test_check_lock_reports_stale_or_unverifiable_lockfiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int | None,
    stderr: str,
    message: str | None,
) -> None:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if returncode is None:
            raise FileNotFoundError("uv")
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(uv_project.subprocess, "run", run)

    if message is None:
        uv_project.check_lock(tmp_path)
        return
    with pytest.raises(ValueError) as exc_info:
        uv_project.check_lock(tmp_path)
    assert str(exc_info.value) == message.format(root=tmp_path)

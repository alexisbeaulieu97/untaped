"""Contract tests for the GitHub Actions workflows.

They pin the tag-triggered release's routing (only a pushed tag reaches PyPI),
its least-privilege jobs and step order, CI's use of ``scripts/release.py``,
and the supply-chain hygiene of every workflow.
"""

from __future__ import annotations

import functools
from typing import Any

import pytest
import yaml

from repo.support import REPO_ROOT

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = ["ci.yml", "pr.yml", "release.yml"]
EXPECTED_UV_VERSION = "0.11.26"
# action -> (reviewed release tag, the full commit SHA it must be pinned to)
EXPECTED_ACTION_REFS = {
    "actions/cache": ("v6.1.0", "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"),
    "actions/checkout": ("v7.0.0", "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"),
    "actions/download-artifact": ("v8.0.1", "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"),
    "actions/upload-artifact": ("v7.0.1", "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"),
    "astral-sh/setup-uv": ("v8.2.0", "fac544c07dec837d0ccb6301d7b5580bf5edae39"),
    "pypa/gh-action-pypi-publish": ("v1.14.0", "cef221092ed1bacb1cc03d23a2d87d1d172e277b"),
}
PRODUCTION = "${{ github.event_name == 'push' && github.ref_type == 'tag' }}"
IS_PRODUCTION = "needs.build.outputs.production == 'true'"
INDEX = "${{ needs.build.outputs.index }}"
SCRIPT = "uv run --no-sync python scripts/release.py"


@functools.cache
def _workflow(name: str) -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
    return workflow


def _steps(workflow: str, job: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _workflow(workflow)["jobs"][job]["steps"]
    return steps


def _all_steps(workflow: str) -> list[dict[str, Any]]:
    return [step for job in _workflow(workflow)["jobs"] for step in _steps(workflow, job)]


def _find(
    steps: list[dict[str, Any]],
    *,
    uses: str | None = None,
    run: str | None = None,
    id: str | None = None,
) -> int:
    """The index of the one step that matches every given filter.

    ``uses`` is a substring of the action; ``run`` is one whole (stripped)
    line of the script; ``id`` is the step id.
    """

    def matches(step: dict[str, Any]) -> bool:
        lines = [line.strip() for line in str(step.get("run", "")).splitlines()]
        return (
            (uses is None or uses in str(step.get("uses", "")))
            and (run is None or run in lines)
            and (id is None or step.get("id") == id)
        )

    found = [i for i, step in enumerate(steps) if matches(step)]
    assert len(found) == 1, f"{uses=} {run=} {id=} match steps {found}, not exactly one"
    return found[0]


def test_workflow_actions_are_pinned_to_reviewed_release_shas() -> None:
    offenders: list[str] = []
    for name in WORKFLOWS:
        for step in _all_steps(name):
            uses = step.get("uses")
            if not uses:
                continue
            action, ref = str(uses).rsplit("@", 1)
            expected = EXPECTED_ACTION_REFS.get(action)
            if expected is None:
                offenders.append(f"{name}: unreviewed action {action}")
            elif ref != expected[1]:
                offenders.append(f"{name}: {action}@{ref} is not {expected[0]} ({expected[1]})")

    assert not offenders, "GitHub Action pins are stale or unpinned:\n" + "\n".join(offenders)


def test_checkout_and_setup_uv_steps_are_hardened() -> None:
    offenders: list[str] = []
    for name in WORKFLOWS:
        for step in _all_steps(name):
            uses = str(step.get("uses", ""))
            options = step.get("with", {})
            if (
                uses.startswith("actions/checkout@")
                and options.get("persist-credentials") is not False
            ):
                offenders.append(f"{name}: checkout must set persist-credentials: false")
            if (
                uses.startswith("astral-sh/setup-uv@")
                and options.get("version") != EXPECTED_UV_VERSION
            ):
                offenders.append(f"{name}: setup-uv must pin uv {EXPECTED_UV_VERSION}")

    assert not offenders, "\n".join(offenders)


def test_release_workflow_shape() -> None:
    """Triggers, least-privilege permissions and each job's ``needs``."""
    workflow = _workflow("release.yml")
    jobs = workflow["jobs"]
    assert workflow["on"] == {"push": {"tags": ["v*"]}, "workflow_dispatch": {}}
    assert workflow["permissions"] == {"contents": "read"}
    assert {name: (job.get("needs"), job.get("permissions")) for name, job in jobs.items()} == {
        "build": (None, None),
        "publish": ("build", {"id-token": "write"}),
        "verify": (["build", "publish"], None),
        "github-release": (["build", "verify"], {"contents": "write"}),
    }
    assert not any("actions/checkout" in str(s.get("uses", "")) for s in jobs["publish"]["steps"])


def test_one_expression_routes_every_job() -> None:
    """Only a pushed tag is production; everything else is a TestPyPI rehearsal.

    Truth table: push/tag -> PyPI + GitHub release; workflow_dispatch/tag,
    workflow_dispatch/branch and push/branch -> TestPyPI, no GitHub release.
    A dispatch that targets a tag is a rehearsal, so the expression checks the
    event as well as the ref type. The build's route step evaluates it once;
    its later steps read ``$PRODUCTION``/``$INDEX`` and later jobs the build
    outputs.
    """
    text = (WORKFLOW_DIR / "release.yml").read_text(encoding="utf-8")
    assert (text.count("github.event_name"), text.count("github.ref_type")) == (1, 1)
    workflow = _workflow("release.yml")
    jobs = workflow["jobs"]
    assert "env" not in workflow
    build = _steps("release.yml", "build")
    route = build[_find(build, id="route")]
    assert route["env"] == {"PRODUCTION": PRODUCTION}
    assert route["run"].splitlines() == [
        "set -euo pipefail",
        'if [ "$PRODUCTION" = true ]; then index=pypi; else index=testpypi; fi',
        'echo "PRODUCTION=$PRODUCTION" >> "$GITHUB_ENV"',
        'echo "INDEX=$index" >> "$GITHUB_ENV"',
        'echo "production=$PRODUCTION" >> "$GITHUB_OUTPUT"',
        'echo "index=$index" >> "$GITHUB_OUTPUT"',
    ]
    assert jobs["build"]["outputs"] == {
        "version": "${{ steps.version.outputs.version }}",
        "production": "${{ steps.route.outputs.production }}",
        "index": "${{ steps.route.outputs.index }}",
    }
    assert jobs["publish"]["environment"] == INDEX
    assert jobs["verify"]["env"]["INDEX"] == INDEX
    assert jobs["github-release"]["if"] == IS_PRODUCTION
    index = build[_find(build, run=f'{SCRIPT} index "$VERSION" --dist dist --index "$INDEX"')]
    assert "env" not in index


def test_each_index_publish_is_routed_and_skips_existing_with_attestations() -> None:
    steps = [s for s in _steps("release.yml", "publish") if "pypi-publish" in str(s.get("uses"))]
    assert {
        s["name"]: (
            s["if"],
            s["with"].get("repository-url"),
            s["with"]["skip-existing"],
            s["with"]["attestations"],
        )
        for s in steps
    } == {
        "Publish to PyPI": (IS_PRODUCTION, None, True, True),
        "Publish to TestPyPI": (
            "needs.build.outputs.production != 'true'",
            "https://test.pypi.org/legacy/",
            True,
            True,
        ),
    }


def test_build_guards_and_checks_run_before_anything_is_uploaded() -> None:
    steps = _steps("release.yml", "build")
    uploads = [i for i, step in enumerate(steps) if "upload-artifact" in str(step.get("uses"))]
    build = _find(steps, run="uv build --all-packages --no-sources --out-dir dist")
    order = [
        _find(steps, run="uv sync --locked"),
        _find(
            steps, run='echo "SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)" >> "$GITHUB_ENV"'
        ),
        _find(steps, id="route"),
        _find(steps, id="version"),
        _find(steps, run=f'{SCRIPT} check "$VERSION"'),
        _find(steps, run=f'{SCRIPT} notes "$VERSION" > release-notes.md'),
        _find(steps, run=f'{SCRIPT} readmes "$VERSION"'),
        build,
        _find(steps, run=f'{SCRIPT} check "$VERSION" --dist dist'),
        _find(steps, run=f'{SCRIPT} smoke "$RUNNER_TEMP/smoke/bin/untaped" "$VERSION"'),
        _find(steps, run=f'{SCRIPT} index "$VERSION" --dist dist --index "$INDEX"'),
        min(uploads),
    ]
    assert order == sorted(order)
    assert len(set(order)) == len(order)
    assert steps[0]["with"]["fetch-depth"] == 0
    lines = steps[build]["run"].splitlines()
    assert (
        lines.index("rm -rf dist")
        < lines.index("uv build --all-packages --no-sources --out-dir dist")
        < lines.index("rm -f dist/.gitignore")
    )


def test_a_production_version_names_the_tag_and_checks_main() -> None:
    """Only production passes ``--tag``, which also refuses a commit not on main."""
    steps = _steps("release.yml", "build")
    step = steps[_find(steps, id="version")]
    assert step["env"] == {"REF_NAME": "${{ github.ref_name }}"}
    assert "if" not in step
    assert step["run"].splitlines()[:7] == [
        "set -euo pipefail",
        'if [ "$PRODUCTION" = true ]; then',
        "  # --tag also fails unless the tagged commit is on main.",
        f'  version="$({SCRIPT} version --tag "$REF_NAME")"',
        "else",
        f'  version="$({SCRIPT} version)"',
        "fi",
    ]


@pytest.mark.parametrize("job", ["build", "verify", "github-release"])
def test_each_job_syncs_the_lock_before_running_the_script(job: str) -> None:
    steps = _steps("release.yml", job)
    sync = _find(steps, run="uv sync --locked")
    scripts = [i for i, s in enumerate(steps) if "scripts/release.py" in str(s.get("run", ""))]
    assert scripts and sync < min(scripts)
    text = "\n".join(str(steps[i]["run"]) for i in scripts)
    assert text.count("scripts/release.py") == text.count(f"{SCRIPT} ")


def test_verify_requires_the_complete_index_and_smokes_the_install() -> None:
    steps = _steps("release.yml", "verify")
    index = _find(steps, run=f'{SCRIPT} index "$VERSION" --dist dist --index "$INDEX" --complete')
    install = _find(
        steps,
        run='if env "${index_env[@]}" uv pip install --python "$RUNNER_TEMP/published/bin/python"'
        ' --refresh "untaped[all]==$VERSION"; then',
    )
    assert index < install
    script = steps[install]["run"]
    lines = [line.strip() for line in script.splitlines()]
    smoke = f'{SCRIPT} smoke "$RUNNER_TEMP/published/bin/untaped" "$VERSION"'
    assert _find(steps, run=smoke) == install
    # The TestPyPI override is scoped to the one install command, not exported.
    assert (
        "index_env=(UV_INDEX=https://test.pypi.org/simple/ UV_INDEX_STRATEGY=unsafe-best-match)"
        in lines
    )
    assert "export" not in script
    # index --complete already waited, so the install retries only briefly.
    assert "for attempt in 1 2 3; do" in lines
    assert 'if [ "$attempt" = 3 ]; then' in lines
    assert 'echo "::error::untaped[all]==$VERSION is not installable from $INDEX"' in lines


@pytest.mark.parametrize(
    ("workflow", "job", "smoke"),
    [
        ("release.yml", "build", f'{SCRIPT} smoke "$RUNNER_TEMP/smoke/bin/untaped" "$VERSION"'),
        (
            "release.yml",
            "verify",
            f'{SCRIPT} smoke "$RUNNER_TEMP/published/bin/untaped" "$VERSION"',
        ),
        (
            "ci.yml",
            "wheel-matrix",
            f'{SCRIPT} smoke "$RUNNER_TEMP/bare/bin/untaped" "$VERSION" --expect ""',
        ),
    ],
)
def test_every_smoke_runs_under_an_isolated_home(workflow: str, job: str, smoke: str) -> None:
    steps = _steps(workflow, job)
    assert _find(steps, run='echo "HOME=$home_dir" >> "$GITHUB_ENV"') < _find(steps, run=smoke)


def test_github_release_runs_the_script_on_the_built_artifacts() -> None:
    job = _workflow("release.yml")["jobs"]["github-release"]
    runs = [str(s["run"]).strip() for s in job["steps"] if s.get("run")]
    assert runs == [
        "uv sync --locked",
        f'{SCRIPT} github-release "$VERSION" --tag "$TAG"'
        ' --repo "$GITHUB_REPOSITORY" --dist dist --notes release-notes.md',
    ]
    downloads = {
        s["with"]["name"] for s in job["steps"] if "download-artifact" in str(s.get("uses", ""))
    }
    assert downloads == {"dist", "release-notes"}
    assert job["env"] == {
        "TAG": "${{ github.ref_name }}",
        "VERSION": "${{ needs.build.outputs.version }}",
    }
    assert [s.get("env") for s in job["steps"]] == [None] * (len(job["steps"]) - 1) + [
        {"GH_TOKEN": "${{ github.token }}"}
    ]


def test_ci_runs_the_release_check_and_smoke() -> None:
    ci = _workflow("ci.yml")
    assert ci["on"] == {
        "push": {"branches": ["main"]},
        "pull_request": None,
        "workflow_dispatch": None,
    }
    assert ci["permissions"] == {"contents": "read"}
    gate = _steps("ci.yml", "lint-and-test")
    pins = gate[_find(gate, run="uv lock --check")]
    assert pins["run"].splitlines() == [
        "set -euo pipefail",
        "uv lock --check",
        "uv run python scripts/release.py check",
    ]
    runs = "\n".join(str(s.get("run", "")) for s in _all_steps("ci.yml"))
    assert ".github/release" not in runs


FRESH_CACHE = 'echo "UV_CACHE_DIR=$RUNNER_TEMP/uv-cache" >> "$GITHUB_ENV"'
# venv -> (what is installed into it, the smoke's exact expectations)
WHEEL_MATRIX = {
    "bare": ('"$CORE"', '--expect ""'),
    "awx": ('--find-links dist "untaped[awx] @ file://$PWD/$CORE"', "--expect awx --skills"),
    "all": ('--find-links dist "untaped[all] @ file://$PWD/$CORE"', "--skills"),
    "hello": ('"$CORE" "$RUNNER_TEMP/hello-src" pytest', "--expect hello --skills"),
    "broken": ('"$CORE" "$RUNNER_TEMP/hello-broken"', '--expect "" --quarantined hello'),
}


@pytest.mark.parametrize("job", ["wheel-matrix", "core-only-tests"])
def test_ci_wheel_jobs_start_from_a_fresh_uv_cache(job: str) -> None:
    """No wheel from an earlier build is reused: setup-uv caches nothing, the
    job points uv at an empty cache after setup-uv, before any uv command."""
    steps = _steps("ci.yml", job)
    setup = _find(steps, uses="astral-sh/setup-uv")
    assert steps[setup]["with"]["enable-cache"] is False
    fresh = _find(steps, run=FRESH_CACHE)
    uv_runs = [i for i, s in enumerate(steps) if "uv " in str(s.get("run", ""))]
    assert setup < fresh <= min(uv_runs)


def test_ci_wheel_matrix_smokes_each_install_shape_from_the_built_wheels() -> None:
    steps = _steps("ci.yml", "wheel-matrix")
    build = _find(steps, run="uv build --all-packages --no-sources --out-dir dist")
    assert steps[build]["run"].splitlines() == [
        "set -euo pipefail",
        "# The release's build: README links pinned first, artifacts checked after.",
        f'version="$({SCRIPT} version)"',
        f'{SCRIPT} readmes "$version"',
        "uv build --all-packages --no-sources --out-dir dist",
        "rm -f dist/.gitignore",
        f'{SCRIPT} check "$version" --dist dist',
        'echo "VERSION=$version" >> "$GITHUB_ENV"',
        'echo "CORE=$(ls dist/untaped-*-py3-none-any.whl)" >> "$GITHUB_ENV"',
    ]
    for venv, (installs, expect) in WHEEL_MATRIX.items():
        python = f'"$RUNNER_TEMP/{venv}/bin/python"'
        install = _find(steps, run=f"uv pip install --python {python} {installs}")
        smoke = _find(
            steps, run=f'{SCRIPT} smoke "$RUNNER_TEMP/{venv}/bin/untaped" "$VERSION" {expect}'
        )
        assert build < install == smoke, venv
    hello = _find(steps, run='cp -r examples/untaped-hello "$RUNNER_TEMP/hello-src"')
    tests = '(cd "$RUNNER_TEMP/hello-src" && "$RUNNER_TEMP/hello/bin/python" -m pytest -q tests)'
    assert _find(steps, run=tests) == hello
    pair = _find(steps, run='cp -r examples/untaped-shelf "$RUNNER_TEMP/shelf-src"')
    for example in ("shelf", "library"):
        tests = (
            f'(cd "$RUNNER_TEMP/{example}-src" && '
            '"$RUNNER_TEMP/contracts/bin/python" -m pytest -q tests)'
        )
        assert _find(steps, run=tests) == pair
        check = (
            'UNTAPED_CONFIG="$RUNNER_TEMP/contracts.yml" '
            f'"$RUNNER_TEMP/contracts/bin/untaped" plugin check {example}'
        )
        assert _find(steps, run=check) == pair
    strict = (
        'UNTAPED_CONFIG="$RUNNER_TEMP/contracts.yml" "$RUNNER_TEMP/contracts/bin/untaped" '
        "plugin check library --format json | jq -e 'all(.[]; .status == \"pass\")'"
    )
    assert _find(steps, run=strict) == pair
    broken = _find(steps, run='cp -r examples/untaped-hello "$RUNNER_TEMP/hello-broken"')
    breaks = (
        "sed -i 's/untaped_hello:SPEC/untaped_hello:missing/'"
        ' "$RUNNER_TEMP/hello-broken/pyproject.toml"'
    )
    assert _find(steps, run=breaks) == broken


def test_ci_bare_install_runs_the_root_commands_and_shows_the_install_hint() -> None:
    steps = _steps("ci.yml", "wheel-matrix")
    smoke_line = f'{SCRIPT} smoke "$RUNNER_TEMP/bare/bin/untaped" "$VERSION" --expect ""'
    lines = [line.strip() for line in steps[_find(steps, run=smoke_line)]["run"].splitlines()]
    smoke = lines.index(smoke_line)
    exe = '"$RUNNER_TEMP/bare/bin/untaped"'
    commands = [
        f"{exe} doctor",
        f"{exe} skills list",
        f"{exe} config list",
        f"{exe} profile list",
        f'{exe} plugin list 2>&1 >/dev/null | grep -F "untaped[all]"',
    ]
    assert [line for line in lines[smoke + 1 :] if not line.startswith("#")] == commands
    assert lines[0] == "set -euo pipefail"


def test_ci_runs_core_tests_with_only_the_core_wheel() -> None:
    steps = _steps("ci.yml", "core-only-tests")
    step = steps[_find(steps, run="uv build --package untaped --no-sources --out-dir dist")]
    assert step["run"].splitlines() == [
        "set -euo pipefail",
        "uv build --package untaped --no-sources --out-dir dist",
        'uv venv -q --python 3.14 "$RUNNER_TEMP/core"',
        # --no-emit-workspace leaves out every first-party package (the dev
        # group's untaped[all] included); pruning each plugin package
        # also leaves out its third-party dependencies.
        'prune=(); for pkg in packages/untaped-*/; do prune+=(--prune "$(basename "$pkg")"); done',
        "uv export --frozen --only-group dev --no-hashes --no-emit-workspace"
        ' "${prune[@]}" > "$RUNNER_TEMP/dev-requirements.txt"',
        'uv pip install --python "$RUNNER_TEMP/core/bin/python"'
        ' dist/untaped-*-py3-none-any.whl -r "$RUNNER_TEMP/dev-requirements.txt"',
        # Plugin-only dependencies (awx and recipe's jinja2, recipe's tomlkit) are absent.
        '"$RUNNER_TEMP/core/bin/python" -c'
        " 'import importlib.util, sys;"
        ' leaked = [m for m in ("jinja2", "tomlkit") if importlib.util.find_spec(m)];'
        ' sys.exit(f"plugin dependencies installed: {leaked}" if leaked else 0)\'',
        '"$RUNNER_TEMP/core/bin/python" -m pytest -q -n auto -p no:cacheprovider'
        " --rootdir . -c pyproject.toml packages/untaped/tests",
    ]


def test_no_run_script_interpolates_expressions() -> None:
    for name in WORKFLOWS:
        for step in _all_steps(name):
            assert "${{" not in str(step.get("run", "")), (
                f"{name}: {step.get('name')} interpolates into shell"
            )


def test_ci_gates_the_coverage_of_a_pull_requests_changed_lines() -> None:
    gate = _steps("ci.yml", "lint-and-test")
    assert gate[_find(gate, uses="actions/checkout")]["with"]["fetch-depth"] == 0
    _find(gate, run="uv sync --frozen --python 3.14 --all-packages --group diff-coverage")
    tests = next(i for i, step in enumerate(gate) if step.get("name") == "Pytest")
    assert "--cov-report=xml" in gate[tests]["run"]
    diff = gate[tests + 1]
    assert diff["if"] == "github.event_name == 'pull_request'"
    assert diff["env"] == {"BASE_SHA": "${{ github.event.pull_request.base.sha }}"}
    assert diff["run"].strip() == (
        'uv run diff-cover coverage.xml --compare-branch "$BASE_SHA" --fail-under 90'
    )


def test_pr_workflow_checks_the_pull_request_body_and_changelog() -> None:
    pr = _workflow("pr.yml")
    assert pr["on"] == {
        "pull_request": {
            "types": ["opened", "edited", "synchronize", "reopened", "ready_for_review"]
        }
    }
    assert pr["permissions"] == {"contents": "read"}
    steps = _steps("pr.yml", "pr-checks")
    assert steps[_find(steps, uses="actions/checkout")]["with"]["fetch-depth"] == 0
    _find(steps, run='uv run --no-sync python scripts/check_pr.py "$GITHUB_EVENT_PATH"')
    draft = steps[
        _find(steps, run='uv run --no-sync python scripts/changelog.py draft --pr "$PR_NUMBER"')
    ]
    assert draft["env"] == {"PR_NUMBER": "${{ github.event.pull_request.number }}"}


def test_ci_derives_the_changelog_draft_on_main() -> None:
    steps = _steps("ci.yml", "lint-and-test")
    draft = steps[_find(steps, run="uv run python scripts/changelog.py draft")]
    assert draft["if"] == "github.event_name == 'push'"

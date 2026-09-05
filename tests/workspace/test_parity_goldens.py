"""Semantic parity goldens for the workspace import (Wave 1.5a).

Every PRESERVE row (P1-P48) of ``evidence/wave15/parity-matrix.md`` is
asserted here from fixtures ``f01``-``f17``. Comparison normalizes exactly
the volatile surface of spec §10 (timestamps → ``<timestamp>``, tmp paths
→ ``<tmp>``, ids → ``<id>``, ANSI stripped, trailing whitespace
collapsed) and nothing else: any remaining diff is a real parity failure.
The ``COVERS`` map plus ``test_parity_covers_every_preserve_row`` keep the
row coverage exact — a preserve row with no golden blocks retirement.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec
from untaped.capabilities.workspace import SPEC, build_app
from untaped.capabilities.workspace.application import Foreach
from untaped.capabilities.workspace.application.shell_init import ShellInit
from untaped.capabilities.workspace.cli.common import parallel_cap, workspace_settings
from untaped.capabilities.workspace.domain import Workspace, WorkspaceManifest, derive_repo_name
from untaped.capabilities.workspace.domain.prune_safety import (
    DIRTY_WORKTREE_BLOCKER,
    STASH_BLOCKER,
    UNREACHABLE_COMMITS_BLOCKER,
)
from untaped.capabilities.workspace.errors import GitError, WorkspaceError
from untaped.capabilities.workspace.infrastructure import (
    ManifestRepository,
    WorkspaceRegistryRepository,
)
from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for
from untaped.capabilities.workspace.infrastructure.git_runner import (
    DEFAULT_SLOW_TIMEOUT,
    DEFAULT_TIMEOUT,
    GitRunner,
)
from untaped.capabilities.workspace.infrastructure.system_adapters import (
    resolve_editor_argv,
    shell_runner,
)
from untaped.capabilities.workspace.settings import WorkspaceSettings, WorkspaceState
from untaped.cli import clamp_parallel
from untaped.pipe import (
    PIPE_ENVELOPE_VERSION,
    PIPE_MARKER_KEY,
    SUPPORTED_PIPE_VERSIONS,
    parse_envelope_line,
)
from untaped.settings import get_settings, resolve_config_path
from untaped.testing import CliInvoker, ScriptedPromptBackend

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "workspace" / "fixtures"

# test name -> P-rows it pins. Union must be exactly {P1..P48}.
COVERS: dict[str, list[str]] = {
    "test_f01_config_layout": ["P1"],
    "test_f02_profile_settings": ["P2", "P3", "P4", "P5"],
    "test_f03_registry": ["P6", "P7"],
    "test_f04_manifest": ["P8", "P9", "P10", "P11"],
    "test_f05_skill": ["P12"],
    "test_f06_pipe": ["P13", "P14"],
    "test_f07_row_schemas": ["P15", "P16", "P17", "P18", "P19", "P20", "P21"],
    "test_f08_messages": ["P22", "P23", "P24"],
    "test_f09_exit_codes": ["P25", "P26", "P27"],
    "test_f10_stdin": ["P28", "P29"],
    "test_f11_resolver_options": ["P30", "P31", "P32"],
    "test_f12_prune": ["P33", "P34", "P35"],
    "test_f13_git": ["P36", "P37"],
    "test_f14_editor_shell": ["P38", "P39"],
    "test_f15_shell_init": ["P40", "P41"],
    "test_f16_branch_adopt_parallel": ["P42", "P43", "P44"],
    "test_f17_path_output": ["P47"],
    "test_p45_capability_statics": ["P45"],
    "test_p46_runtime_deps": ["P46"],
    "test_p48_branch_set_apply": ["P48"],
}


def test_parity_covers_every_preserve_row() -> None:
    covered = sorted({row for rows in COVERS.values() for row in rows}, key=lambda r: int(r[1:]))
    expected = [f"P{n}" for n in range(1, 49)]
    assert covered == expected, (
        "golden coverage must pin every preserve row exactly once: "
        f"missing={[r for r in expected if r not in covered]}, "
        f"extra={[r for r in covered if r not in expected]}"
    )


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def norm(text: str, tmp: Path | None = None) -> str:
    """Normalize exactly the §10 volatile surface before diffing."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    if tmp is not None:
        text = text.replace(str(tmp), "<tmp>")
    text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*", "<timestamp>", text)
    text = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "<id>",
        text,
    )
    return "\n".join(line.rstrip() for line in text.splitlines())


@pytest.fixture(autouse=True)
def _golden_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    bootstrap._clear_for_tests()
    get_settings.cache_clear()
    yield cfg
    bootstrap._clear_for_tests()
    get_settings.cache_clear()


def _root() -> object:
    return bootstrap.build_root_app(builtins=(SPEC,), externals=())


def _run(argv: list[str], **kwargs: object) -> object:
    return CliInvoker().invoke(_root().meta, argv, **kwargs)  # type: ignore[union-attr]


def _bare_upstream(tmp_path: Path, name: str = "upstream.git", branch: str = "main") -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    bare = tmp_path / name
    subprocess.run(
        ["git", "init", "--bare", f"--initial-branch={branch}", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / f"_seed_{bare.stem}"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    (seed / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", branch], check=True, capture_output=True
    )
    shutil.rmtree(seed)
    return bare


def _registered(tmp_path: Path, name: str = "prod") -> Path:
    target = tmp_path / name
    result = _run(["workspace", "init", name, "--path", str(target)])
    assert result.exit_code == 0, result.output
    return target


def _push_commit(bare: Path, tmp_path: Path, filename: str, branch: str = "main") -> None:
    work = tmp_path / f"_push_{Path(filename).stem}"
    if work.exists():
        shutil.rmtree(work)
    subprocess.run(["git", "clone", "--quiet", str(bare), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "commit.gpgsign", "false"], check=True)
    (work / filename).write_text(filename)
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "--quiet", "--no-gpg-sign", "-m", filename],
        check=True,
    )
    subprocess.run(["git", "-C", str(work), "push", "--quiet", "origin", branch], check=True)
    shutil.rmtree(work)


# ── f01: config layout (P1) ──────────────────────────────────────────────


def test_f01_config_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f01-config-layout.json")
    assert fix["config_file_default"] == "~/.untaped/config.yml"
    assert fix["config_file_override_env"] == "UNTAPED_CONFIG"
    assert fix["config_section"] == "workspace"
    assert SPEC.config_section == "workspace"
    assert fix["env_override_shape"] == "UNTAPED_<SECTION>__<FIELD>"

    monkeypatch.delenv("UNTAPED_CONFIG")
    assert resolve_config_path() == Path("~/.untaped/config.yml").expanduser()
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    assert resolve_config_path() == tmp_path / "config.yml"
    # expanduser applies to the override
    monkeypatch.setenv("UNTAPED_CONFIG", "~/golden-config.yml")
    assert resolve_config_path() == Path("~/golden-config.yml").expanduser()

    # top-level workspace.workspaces is disjoint tool-managed state (P1/P4)
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    get_settings.cache_clear()
    result = _run(["config", "set", "workspace.workspaces", "[]"])
    assert result.exit_code != 0
    assert "workspaces" in norm(result.stderr, tmp_path)
    assert fix["state_rejection"]["exit_nonzero"] is True
    assert fix["state_rejection"]["stderr_contains"] in result.stderr


# ── f02: profile settings (P2-P5) ────────────────────────────────────────


def test_f02_profile_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f02-profile-settings.json")
    assert sorted(WorkspaceSettings.model_fields) == ["cache_dir", "workspaces_dir"]
    assert sorted(fix["bare_keys"]) == ["cache_dir", "workspaces_dir"]
    assert SPEC.profile_model is WorkspaceSettings
    assert SPEC.state_model is WorkspaceState

    defaults = WorkspaceSettings()
    assert defaults.cache_dir == Path(fix["defaults"]["cache_dir"])
    assert defaults.workspaces_dir == Path(fix["defaults"]["workspaces_dir"])
    # unexpanded Path defaults; expanduser() at use (P3)
    assert "~" in str(WorkspaceSettings.model_fields["cache_dir"].default)
    assert defaults.cache_dir.expanduser() == Path("~/.untaped/repositories").expanduser()

    # profiles.default layers beneath the active profile; --profile in any position
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n"
        "  default:\n"
        "    workspace:\n"
        "      cache_dir: /from/default\n"
        "      workspaces_dir: /ws-default\n"
        "  work:\n"
        "    workspace:\n"
        "      cache_dir: /from/work\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    assert workspace_settings().cache_dir == Path("/from/default")
    for argv in (
        ["config", "get", "workspace.cache_dir", "--profile", "work"],
        ["--profile", "work", "config", "get", "workspace.cache_dir"],
    ):
        result = _run(argv)
        assert result.exit_code == 0, (argv, result.output)
        assert result.stdout.strip() == "/from/work", (argv, result.output)
    # work inherits workspaces_dir from default (layering)
    result = _run(["--profile", "work", "config", "get", "workspace.workspaces_dir"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "/ws-default"

    # bare keys address the capability's own section via the typed accessor
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    get_settings.cache_clear()
    assert workspace_settings().cache_dir == Path("/from/work")
    monkeypatch.delenv("UNTAPED_PROFILE")

    # SDK env shape overrides profile fields (P5)
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()
    assert workspace_settings().cache_dir == tmp_path / "_cache"
    assert fix["layering"] == "profiles.default beneath profiles.<active>"
    assert fix["profile_flag"] == "--profile (any token position)"


# ── f03: registry (P6-P7) ────────────────────────────────────────────────


def _write_state(cfg: Path, entries: list[dict[str, str]]) -> None:
    payload = {"workspace": {"workspaces": entries}}
    cfg.write_text(yaml.safe_dump(payload), encoding="utf-8")
    get_settings.cache_clear()


def test_f03_registry(tmp_path: Path) -> None:
    fix = fixture("f03-registry.json")
    cfg = tmp_path / "config.yml"
    assert fix["state_key"] == "workspace.workspaces"
    assert fix["canonicalization"] == "expanduser().resolve() on read and write"

    repo = WorkspaceRegistryRepository()
    assert repo.entries() == []
    ws = repo.register(name="prod", path=tmp_path / "ws")
    assert ws.name == "prod"
    assert repo.get("prod").path == (tmp_path / "ws").expanduser().resolve()
    assert repo.find_by_path(tmp_path / "ws") is not None
    assert repo.find_by_path(tmp_path / "ws").name == "prod"
    assert [e.name for e in repo.entries()] == ["prod"]
    assert repo.unregister("prod") is True
    assert repo.entries() == []

    stored = str(tmp_path / "ws")
    _write_state(cfg, [{"name": "prod", "path": stored}])
    assert fix["entries"] == [{"name": "prod", "path": "<tmp>/ws"}]
    assert repo.get("prod").path == (tmp_path / "ws").expanduser().resolve()

    from untaped.capabilities.workspace.errors import RegistryError

    errors = list(fix["errors"])
    with pytest.raises(RegistryError, match=re.escape("unknown workspace: 'ghost'")):
        repo.get("ghost")
    with pytest.raises(
        RegistryError,
        match=re.escape(f"workspace name already registered: 'prod' → {stored}"),
    ):
        repo.register(name="prod", path=tmp_path / "other")
    with pytest.raises(
        RegistryError,
        match=re.escape(f"workspace path already registered: {stored} (as 'prod')"),
    ):
        repo.register(name="other", path=tmp_path / "ws")
    assert errors[3].startswith("invalid workspace registry entry: missing or empty 'name'")
    assert errors[4].startswith("invalid workspace registry entry 'prod'")
    assert sorted(fix["methods"]) == ["entries", "find_by_path", "get", "register", "unregister"]


# ── f04: manifest (P8-P11) ───────────────────────────────────────────────


def test_f04_manifest(tmp_path: Path) -> None:
    fix = fixture("f04-manifest.json")
    from untaped.capabilities.workspace.infrastructure.manifest_repo import MANIFEST_FILENAME

    assert MANIFEST_FILENAME == "untaped.yml" == fix["filename"]

    sample = fix["sample"]
    manifest = WorkspaceManifest(**sample)
    assert manifest.name == "prod"
    assert manifest.defaults.branch == "main"
    assert [r.name for r in manifest.repos] == ["api", "web"]

    # invariants: frozen, extra-forbid, tuple repos (P9)
    assert fix["invariants"] == {"extra": "forbid", "frozen": True, "repos_type": "tuple"}
    assert isinstance(manifest.repos, tuple)
    with pytest.raises(AttributeError, match=""):
        manifest.repos.append("x")  # type: ignore[attr-defined]
    with pytest.raises(Exception, match=""):
        WorkspaceManifest(name="x", bogus_field=True)  # type: ignore[call-arg]
    with pytest.raises(Exception, match=""):
        manifest.name = "changed"  # type: ignore[misc]

    # derive_repo_name (P9)
    for url, expected in fix["derive_repo_name"].items():
        assert derive_repo_name(url) == expected, url

    # duplicates rejected URL-before-name (P9)
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="duplicate repo url:"):
        WorkspaceManifest(
            repos=[{"url": "https://x/api.git", "name": "a"}, {"url": "https://x/api.git"}]
        )
    with pytest.raises(ValidationError, match="duplicate repo name:"):
        WorkspaceManifest(
            repos=[
                {"url": "https://x/a.git", "name": "api"},
                {"url": "https://x/b.git", "name": "api"},
            ]
        )
    assert fix["duplicate_precedence"] == "url before name"
    with pytest.raises(ValidationError, match="repo url cannot be empty"):
        WorkspaceManifest(repos=[{"url": "  "}])
    with pytest.raises(Exception, match="no repo matches 'typo'"):
        manifest.remove_repo("typo")

    # write: mkdirs parent, <name>.tmp, chmod 0o644, os.replace (P10)
    assert fix["write"] == {
        "chmod": "0o644",
        "mkdir_parents": True,
        "tmp_suffix": ".tmp",
        "via": "os.replace",
    }
    nested = tmp_path / "deep" / "ws"
    ManifestRepository().write(nested, manifest)
    written = nested / "untaped.yml"
    assert written.is_file()
    assert stat.S_IMODE(written.stat().st_mode) == 0o644
    assert ManifestRepository().read(nested) == manifest
    leftovers = list(nested.glob("*.tmp"))
    assert leftovers == []

    # errors (P11)
    from untaped.capabilities.workspace.errors import ManifestError

    with pytest.raises(ManifestError, match="run `untaped workspace init` first"):
        ManifestRepository().read(tmp_path / "missing")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "untaped.yml").write_text("repos: [unclosed\n")
    with pytest.raises(ManifestError, match="invalid YAML in"):
        ManifestRepository().read(tmp_path / "bad")
    (tmp_path / "bad2").mkdir()
    (tmp_path / "bad2" / "untaped.yml").write_text("repos: [{url: x}, {url: x}]\n")
    with pytest.raises(ManifestError, match="invalid manifest at"):
        ManifestRepository().read(tmp_path / "bad2")
    for message in fix["errors"]:
        assert isinstance(message, str)


# ── f05: skill (P12) ─────────────────────────────────────────────────────


def test_f05_skill() -> None:
    fix = fixture("f05-skill.json")
    (skill,) = SPEC.skills
    assert skill.name == fix["name"] == "untaped-workspace"
    assert skill.description == fix["description"] == "Use the untaped-workspace CLI."
    assert str(skill.source).endswith("skills/untaped-workspace")
    assert fix["source"] == "<pkg>/skills/untaped-workspace/SKILL.md"
    assert skill.source.joinpath("SKILL.md").is_file()
    header = (skill.source / "SKILL.md").read_text().splitlines()
    assert header[1] == "name: untaped-workspace"
    assert header[2] == "description: Use the untaped-workspace CLI."


# ── f06: pipe envelope + kinds (P13-P14) ─────────────────────────────────


def test_f06_pipe(tmp_path: Path) -> None:
    fix = fixture("f06-pipe.json")
    assert PIPE_MARKER_KEY == "untaped"
    assert PIPE_ENVELOPE_VERSION == "1"
    assert frozenset({"1"}) == SUPPORTED_PIPE_VERSIONS
    assert fix["envelope"] == {"keys": ["untaped", "kind", "record"], "untaped": "1"}

    target = _registered(tmp_path)
    result = _run(["workspace", "list", "--format", "pipe"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(lines) == 1
    (line,) = lines
    assert sorted(line) == ["kind", "record", "untaped"]
    assert line["untaped"] == "1"
    assert line["kind"] == "workspace.workspace"
    assert line["record"] == {"name": "prod", "path": str(target.expanduser().resolve())}

    # versions other than 1 rejected (P13)
    with pytest.raises(Exception, match="unsupported pipe version"):
        parse_envelope_line(1, '{"untaped": "2", "kind": "x", "record": {}}')

    # empty show emits a summary row with no target_path at all (P14)
    result = _run(["workspace", "show", "--workspace", "prod", "--format", "pipe"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(rows) == 1
    assert rows[0]["kind"] == "workspace.repo.summary"
    assert "target_path" not in rows[0]["record"]

    kinds = {line["kind"] for line in fix["lines"]}
    assert set(fix["kinds"]) == {
        "workspace.workspace",
        "workspace.repo",
        "workspace.repo.summary",
        "workspace.sync_outcome",
        "workspace.status",
        "workspace.foreach_outcome",
        "workspace.branch_outcome",
    }
    assert kinds <= set(fix["kinds"])
    assert fix["summary_convention"].startswith(".summary rows carry no target_path")


# ── f07: row schemas (P15-P21) ───────────────────────────────────────────


def _json_rows(result: object, tmp: Path) -> list[dict[str, object]]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_f07_row_schemas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f07-row-schemas.json")
    assert fix["formats"] == ["table", "json", "yaml", "raw", "pipe"]
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()

    # list: first key name, empty hint (P15)
    result = _run(["workspace", "list", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert fix["list"]["empty"] in result.output
    _registered(tmp_path)
    result = _run(["workspace", "list", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert [list(row) for row in rows] == [["name", "path"]]
    assert fix["list"]["first_key"] == "name"
    assert fix["list"]["columns"] == ["name", "path"]

    # show: first key workspace, target_path omitted when absent (P16)
    result = _run(["workspace", "show", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert len(rows) == 1
    assert next(iter(rows[0])) == "workspace"
    assert "target_path" not in rows[0]
    assert rows[0]["repo_count"] == 0
    assert fix["show"]["target_path"] == "omitted (not null) when absent"

    upstream = _bare_upstream(tmp_path)
    result = _run(["workspace", "add", f"file://{upstream}", "--workspace", "prod"])
    assert result.exit_code == 0, result.output
    result = _run(["workspace", "show", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert next(iter(rows[0])) == "workspace"
    assert rows[0]["target_path"].endswith("/upstream")
    assert rows[0]["repo_count"] == 1

    # sync: row keys, empty message (P17)
    result = _run(["workspace", "sync", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert [sorted(row) for row in rows] == [sorted(fix["sync"]["columns"])]
    assert next(iter(rows[0])) == "workspace"
    assert rows[0]["action"] == "clone"
    _run(["workspace", "init", "empty", "--path", str(tmp_path / "empty-ws")])
    result = _run(["workspace", "sync", "--workspace", "empty", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert fix["sync"]["empty"] in result.output

    # status: row keys, empty message (P18)
    result = _run(["workspace", "status", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert [sorted(row) for row in rows] == [sorted(fix["status"]["columns"])]
    assert next(iter(rows[0])) == "workspace"
    assert rows[0]["branch"] == "main"
    result = _run(["workspace", "status", "--workspace", "empty", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert fix["status"]["empty"] in result.output

    # foreach structured rows carry duration_s float (P19)
    result = _run(["workspace", "foreach", "echo hi", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert [sorted(row) for row in rows] == [sorted(fix["foreach_structured"]["columns"])]
    assert next(iter(rows[0])) == "workspace"
    assert isinstance(rows[0]["duration_s"], float)

    # branch apply: row keys, empty message (P20)
    result = _run(["workspace", "branch", "apply", "--workspace", "empty"])
    assert result.exit_code == 0, result.output
    assert fix["branch_apply"]["empty"] in result.output
    result = _run(["workspace", "branch", "apply", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert [sorted(r) for r in rows] == [sorted(fix["branch_apply"]["columns"])]
    assert next(iter(rows[0])) == "repo"
    assert rows[0]["action"] == "skip"
    assert rows[0]["detail"] == "no target branch"
    assert rows[0]["target_branch"] is None
    assert fix["branch_apply"]["first_key"] == "repo"

    # formats + columns contract (P21)
    for fmt in fix["formats"]:
        result = _run(["workspace", "list", "--format", fmt])
        assert result.exit_code == 0, (fmt, result.output)
    result = _run(["workspace", "list", "--format", "raw", "--columns", "name"])
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == ["empty", "prod"]
    result = _run(["workspace", "list", "--columns", "?"])
    assert "name" in result.stderr and "path" in result.stderr
    assert fix["columns_probe"].startswith("--columns ?")
    assert fix["theming"].startswith("only table resolves theme")


# ── f08: messages (P22-P24) ──────────────────────────────────────────────


def test_f08_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f08-messages.json")
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()
    upstream = _bare_upstream(tmp_path)

    result = _run(["workspace", "init", "prod", "--path", str(tmp_path / "ws")])
    assert result.exit_code == 0, result.output
    assert norm(result.stderr, tmp_path) == "initialised workspace 'prod' at <tmp>/ws"

    result = _run(["workspace", "add", f"file://{upstream}", "--workspace", "prod"])
    assert result.exit_code == 0, result.output
    assert norm(result.stderr, tmp_path) == "added upstream to 'prod'"

    # progress goes to stderr; pipe stdout stays pure NDJSON (P22)
    result = _run(["workspace", "sync", "--workspace", "prod", "--format", "pipe"])
    assert result.exit_code == 0, result.output
    for line in result.stdout.splitlines():
        assert sorted(json.loads(line)) == ["kind", "record", "untaped"]
    assert "Syncing repos…" in result.stderr
    for spinner in fix["progress_spinner_stderr"]:
        assert isinstance(spinner, str)

    result = _run(["workspace", "remove", "upstream", "--workspace", "prod"])
    assert result.exit_code == 0, result.output
    assert norm(result.stderr, tmp_path).splitlines()[-1] == "removed upstream from 'prod'"

    result = _run(
        ["workspace", "branch", "set", "main", "--workspace", "prod"],
    )
    assert result.exit_code == 0, result.output
    assert norm(result.stderr, tmp_path) == "set default branch for 'prod' to main"
    result = _run(["workspace", "branch", "unset", "--workspace", "prod"])
    assert result.exit_code == 0, result.output
    assert norm(result.stderr, tmp_path) == "unset default branch for 'prod'"

    result = _run(["workspace", "forget", "prod"])
    assert result.exit_code == 0, result.output
    assert norm(result.stderr, tmp_path).splitlines()[-1] == "forgot workspace 'prod'"

    # sync summaries: empty + nonzero-parts-only in fixed order (P24)
    _registered(tmp_path, "smoke")
    result = _run(["workspace", "sync", "--workspace", "smoke", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert "sync complete: 0 repos" in result.stderr
    assert fix["sync_summary"][0] == "sync complete: 0 repos"
    other = tmp_path / "other.git"
    shutil.copytree(upstream, other)
    _run(["workspace", "add", f"file://{upstream}", "--workspace", "smoke"])
    _run(["workspace", "add", f"file://{other}", "--repo-name", "ui", "--workspace", "smoke"])
    result = _run(["workspace", "sync", "--workspace", "smoke"])
    assert result.exit_code == 0, result.output
    assert "sync complete: 2 repos (2 cloned)" in result.stderr
    result = _run(["workspace", "sync", "--workspace", "smoke"])
    assert "sync complete: 2 repos (2 up to date)" in result.stderr

    # foreach table replay + no-match hint (P19/P23)
    result = _run(["workspace", "foreach", "echo hello", "--workspace", "smoke"])
    assert result.exit_code == 0, result.output
    assert "[upstream] hello" in result.stdout
    assert "[ui] hello" in result.stdout
    # --repo with no match stays strict (same as status --repo): the typed
    # UnmatchedRepoFilter names the offending identifier. The P19 table hint
    # fires only when the manifest itself yields no repos (no filter).
    result = _run(["workspace", "foreach", "echo hi", "--workspace", "smoke", "--repo", "typo"])
    assert result.exit_code != 0
    assert "unknown repo identifier(s) for --repo: typo" in result.stderr
    _registered(tmp_path, "empty")
    result = _run(["workspace", "foreach", "echo hi", "--workspace", "empty"])
    assert result.exit_code == 0, result.output
    assert fix["foreach_table_no_match_stderr"] in result.stderr
    assert fix["foreach_table_no_match_stderr"].startswith("No repos matched.")
    assert fix["foreach_table_replay_stdout"] == "[<repo>] <captured stdout line>"


# ── f09: exit codes (P25-P27) ────────────────────────────────────────────


def test_f09_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f09-exit-codes.json")
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()

    # usage errors: exit 2, `error: ` on stderr, empty stdout (P25)
    result = _run(["workspace", "init"])
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert fix["usage_error"] == {"exit": 2, "stderr_prefix": "error: ", "stdout": ""}

    # domain errors under report_errors: exit 1 (P25)
    result = _run(["workspace", "show", "--workspace", "ghost"])
    assert result.exit_code == 1, result.output
    assert fix["domain_error"] == {"exit": 1, "via": "report_errors"}

    upstream = _bare_upstream(tmp_path)
    _registered(tmp_path, "smoke")
    _run(["workspace", "add", f"file://{upstream}", "--workspace", "smoke"])
    _run(["workspace", "sync", "--workspace", "smoke"])

    # foreach exit outcomes (P27)
    result = _run(["workspace", "foreach", "exit 3", "--workspace", "smoke"])
    assert result.exit_code == 1, result.output
    assert fix["foreach_fail"] == {"exit": 1}
    result = _run(["workspace", "foreach", "exit 3", "--workspace", "smoke", "--ignore-errors"])
    assert result.exit_code == 0, result.output
    assert fix["foreach_ignore_errors"] == {"exit": 0}

    # edit forwards the editor exit code; missing binary is a clean error (P26)
    target = tmp_path / "smoke"
    result = _run(
        ["workspace", "edit", "--workspace", "smoke", "--editor", "definitely-missing-bin"]
    )
    assert result.exit_code == 1, result.output
    assert "editor not found: definitely-missing-bin" in result.stderr
    import untaped.capabilities.workspace.cli.ux_commands as ux

    monkeypatch.setattr(ux, "editor_runner", lambda argv: 3)
    with pytest.raises(SystemExit) as excinfo:
        from untaped.capabilities.workspace.application import EditWorkspace

        ws = Workspace(name="smoke", path=target.expanduser().resolve())
        rc = EditWorkspace(runner=ux.editor_runner)(ws, argv=("whatever", str(target)))
        if rc != 0:
            raise SystemExit(rc)
    assert excinfo.value.code == 3
    assert fix["success"] == {"exit": 0}


# ── f10: stdin (P28-P29) ─────────────────────────────────────────────────


def test_f10_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f10-stdin.json")
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()
    upstream = _bare_upstream(tmp_path)
    other = tmp_path / "other.git"
    shutil.copytree(upstream, other)
    _registered(tmp_path)

    # add --stdin reads one URL per line (P28)
    result = _run(
        ["workspace", "add", "--stdin", "--workspace", "prod"],
        input=f"file://{upstream}\nfile://{other}\n",
    )
    assert result.exit_code == 0, result.output
    result = _run(["workspace", "show", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert sorted(r["repo"] for r in rows) == ["other", "upstream"]
    assert fix["add_stdin"].startswith("one URL per line")

    # --repo-name with >1 URL is a usage error (P28)
    result = _run(
        ["workspace", "add", "--stdin", "--workspace", "prod", "--repo-name", "x"],
        input=f"file://{upstream}\nfile://{other}\n",
    )
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert (
        "--repo-name applies to a single URL; drop --repo-name or pass URLs one at a time."
        in result.stderr
    )
    assert fix["repo_name_rule"].startswith("--repo-name applies to a single URL")

    # --stdin has no --no-stdin negative alias (P28)
    for args in (
        ["workspace", "add", "--help"],
        ["workspace", "remove", "--help"],
        ["workspace", "path", "--help"],
    ):
        result = _run(args)
        assert result.exit_code == 0, (args, result.output)
        assert "--stdin" in result.output
        assert "--no-stdin" not in result.output
    assert fix["remove_stdin"] == "one identifier per line"

    # path --stdin: bare names + pipe round-trip (P29)
    assert fix["path_stdin"]["id_field"] == "name"
    assert fix["path_stdin"]["roundtrip"] == "list --format pipe | path --stdin"
    listed = _run(["workspace", "list", "--format", "pipe"])
    assert listed.exit_code == 0, listed.output
    result = _run(["workspace", "path", "--stdin"], input=listed.stdout)
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [(tmp_path / "prod").expanduser().resolve().as_posix()]


# ── f11: resolver + options (P30-P32) ────────────────────────────────────


def test_f11_resolver_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f11-resolver-options.json")
    assert fix["precedence"] == "--workspace > --path > cwd-walk"
    target = _registered(tmp_path)

    # --workspace (registry) resolves
    result = _run(["workspace", "show", "--workspace", "prod", "--format", "raw"])
    assert result.exit_code == 0, result.output
    # --path (manifest dir) resolves
    result = _run(["workspace", "show", "--path", str(target), "--format", "raw"])
    assert result.exit_code == 0, result.output
    # cwd walk resolves
    sub = target / "subdir"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = _run(["workspace", "show", "--format", "raw"])
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path)

    # unregistered on-disk manifest resolves with manifest name (beats dirname)
    alien = tmp_path / "odd-dirname"
    alien.mkdir()
    ManifestRepository().write(alien, WorkspaceManifest(name="realname"))
    result = _run(["workspace", "show", "--path", str(alien), "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert rows[0]["workspace"] == "realname"
    assert fix["unregistered_manifest"].startswith("resolves with manifest name:")

    # resolver errors (P31)
    result = _run(["workspace", "show", "--workspace", "prod", "--path", str(target)])
    assert result.exit_code == 2, result.output
    assert "--workspace and --path are mutually exclusive" in result.stderr
    result = _run(["workspace", "sync", "--all", "--workspace", "prod"])
    assert result.exit_code == 2, result.output
    assert "--all cannot be combined with --workspace or --path" in result.stderr
    lone = tmp_path / "lone"
    lone.mkdir()
    monkeypatch.chdir(lone)
    result = _run(["workspace", "show"])
    assert result.exit_code == 1, result.output
    assert "not inside a workspace" in result.stderr
    monkeypatch.chdir(tmp_path)
    result = _run(["workspace", "show", "--path", str(tmp_path / "elsewhere")])
    assert result.exit_code == 1, result.output
    assert "no workspace manifest at" in norm(result.stderr, tmp_path)
    result = _run(["workspace", "status", "--workspace", "prod", "--repo", "typo"])
    assert result.exit_code == 1, result.output
    assert "unknown repo identifier(s) for --repo: typo" in result.stderr

    # option validation (P32)
    result = _run(["workspace", "sync", "--workspace", "prod", "--timeout", "0"])
    assert result.exit_code == 2, result.output
    assert "--timeout must be positive" in result.stderr
    result = _run(["workspace", "sync", "--workspace", "prod", "--parallel", "0"])
    assert result.exit_code == 2, result.output
    assert "--parallel must be >= 1" in result.stderr
    for message in fix["errors"]:
        assert isinstance(message, str)

    # short flags (P32)
    shorts = fix["short_flags"]
    assert shorts["--workspace"] == "-w"
    result = _run(["workspace", "show", "-w", "prod", "--format", "raw"])
    assert result.exit_code == 0, result.output
    result = _run(["workspace", "show", "-p", str(target), "--format", "raw"])
    assert result.exit_code == 0, result.output
    result = _run(["workspace", "list", "-f", "raw", "-c", "name"])
    assert result.exit_code == 0, result.output
    assert "prod" in result.stdout.splitlines()
    result = _run(["workspace", "list", "-q"])
    assert result.exit_code == 0, result.output
    for flag, help_args in (
        ("-r", ["workspace", "status", "--help"]),
        ("-b", ["workspace", "init", "--help"]),
        ("-j", ["workspace", "sync", "--help"]),
        ("-y", ["workspace", "remove", "--help"]),
        ("-e", ["workspace", "edit", "--help"]),
    ):
        result = _run(help_args)
        assert result.exit_code == 0, (flag, result.output)
        assert flag in result.output, (flag, result.output)


# ── f12: prune (P33-P35) ─────────────────────────────────────────────────


def test_f12_prune(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f12-prune.json")
    assert fix["blockers"] == [
        "dirty working tree",
        "stash entries present",
        "local commits not reachable from any remote-tracking ref",
    ]
    assert DIRTY_WORKTREE_BLOCKER == "dirty working tree"
    assert STASH_BLOCKER == "stash entries present"
    assert UNREACHABLE_COMMITS_BLOCKER == (
        "local commits not reachable from any remote-tracking ref"
    )
    assert fix["offline_boundary"].startswith("no fetch during prune check")
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()
    upstream = _bare_upstream(tmp_path)
    target = _registered(tmp_path)
    _run(["workspace", "add", f"file://{upstream}", "--workspace", "prod"])
    _run(["workspace", "sync", "--workspace", "prod"])
    clone = target / "upstream"

    # dirty clone blocks prune with the unsafe-orphan detail shape
    (clone / "dirty.txt").write_text("dirty")
    result = _run(["workspace", "remove", "upstream", "--workspace", "prod", "--prune", "--yes"])
    assert result.exit_code == 1, result.output
    assert "unsafe local state: dirty working tree" in result.output
    assert clone.is_dir()
    (clone / "dirty.txt").unlink()

    # batch confirm: prompts once, decline exits cleanly without mutation (P34)
    assert fix["batch_confirm"]["skip"] == "--yes / -y"
    assert fix["sync_prune_prompt"] is False
    backend = ScriptedPromptBackend(confirms=[False])
    result = CliInvoker().invoke(
        _root().meta,  # type: ignore[union-attr]
        ["workspace", "remove", "upstream", "--workspace", "prod", "--prune"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert backend.calls == [("confirm", "Continue?")]
    assert clone.is_dir()
    assert fix["batch_confirm"]["decline"] == "exits cleanly, no mutation"

    # sync --prune never prompts (P34)
    result = _run(["workspace", "sync", "--workspace", "prod", "--prune"])
    assert result.exit_code == 0, result.output

    # bulk rows: unavailable + unmatched shapes (P35)
    _write_state(
        tmp_path / "config.yml",
        [
            {"name": "prod", "path": str(target)},
            {"name": "ghost", "path": str(tmp_path / "ghost")},
        ],
    )
    result = _run(["workspace", "status", "--all", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    unavailable = [r for r in rows if r["action"] == "unavailable"]
    assert len(unavailable) == 1
    assert unavailable[0]["repo"] == ""
    assert unavailable[0]["detail"].startswith("workspace manifest unavailable:")
    assert fix["bulk_rows"]["unavailable"]["repo"] == ""
    # sync --all --repo warns and emits per-workspace unmatched rows (status
    # --all --repo stays strict and rejects unknown identifiers)
    result = _run(["workspace", "sync", "--all", "--repo", "typo", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "warning: --all --repo filters per-workspace;" in result.stderr
    rows = json.loads(result.stdout)
    unmatched = [r for r in rows if r["action"] == "unmatched"]
    assert unmatched
    assert all(r["repo"] == "typo" for r in unmatched)
    assert all(r["detail"] == "not in this workspace's manifest" for r in unmatched)
    result = _run(["workspace", "status", "--all", "--repo", "typo", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert "unknown repo identifier(s) for --repo: typo" in result.stderr
    assert fix["prune_remove_detail"] == "no longer declared"


# ── f13: git (P36-P37) ───────────────────────────────────────────────────


def test_f13_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f13-git.json")
    assert DEFAULT_TIMEOUT == 60.0
    assert DEFAULT_SLOW_TIMEOUT == 600.0
    assert fix["timeouts"] == {
        "clone_fetch_default_s": 600.0,
        "local_default_s": 60.0,
        "single_--timeout": "caps both",
    }
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()

    # bare cache layout incl. _unknown fallback (P37)
    cache = tmp_path / "_cache"
    assert cache_path_for("https://github.com/acme/web.git", cache_dir=cache) == (
        cache.expanduser().resolve() / "github.com" / "acme" / "web.git"
    )
    assert cache_path_for("git@github.com:acme/api.git", cache_dir=cache) == (
        cache.expanduser().resolve() / "github.com" / "acme" / "api.git"
    )
    digest = hashlib.sha256(b"not a url at all").hexdigest()[:16]
    assert cache_path_for("not a url at all", cache_dir=cache) == (
        cache.expanduser().resolve() / "_unknown" / f"{digest}.git"
    )
    assert fix["bare_cache"]["layout"] == "<cache_dir>/<host>/<owner>/<name>.git"
    assert fix["bare_cache"]["fallback"] == "<cache>/_unknown/<sha16>.git"

    # error shapes (P37)
    missing = GitRunner(git="definitely-missing-git-xyz")
    with pytest.raises(GitError, match="`definitely-missing-git-xyz` not found on PATH"):
        missing.status(tmp_path)
    assert fix["errors"][0].endswith("not found on PATH")
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    runner = GitRunner()
    (tmp_path / "not-a-repo").mkdir()
    with pytest.raises(GitError, match=r"git .* failed: "):
        runner.status(tmp_path / "not-a-repo")
    assert "no stderr" in fix["errors"][1]

    # clone --reference for missing clones; existing clones reuse own remotes (P37)
    upstream = _bare_upstream(tmp_path)
    target = _registered(tmp_path)
    _run(["workspace", "add", f"file://{upstream}", "--workspace", "prod"])
    _run(["workspace", "sync", "--workspace", "prod"])
    bare = cache_path_for(f"file://{upstream}", cache_dir=cache.expanduser().resolve())
    assert bare.is_dir()
    alternates = target / "upstream" / ".git" / "objects" / "info" / "alternates"
    assert alternates.is_file()
    assert "objects" in alternates.read_text()
    shutil.rmtree(bare)
    result = _run(["workspace", "sync", "--workspace", "prod"])
    assert result.exit_code == 0, result.output
    assert "1 repo (1 up to date)" in result.stderr
    assert fix["clone"].startswith("git clone --reference <bare>")

    # sync skip details (P36)
    (target / "upstream" / "dirty.txt").write_text("dirty")
    result = _run(["workspace", "sync", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert rows[0]["action"] == "skip"
    assert rows[0]["detail"] == "dirty working tree"
    (target / "upstream" / "dirty.txt").unlink()
    # A detached clone is never checked out or pulled: skip (P36). Porcelain
    # v2 reports no ahead/behind while detached, so the live trigger is a
    # manifest target branch mismatching the detached HEAD; the bare
    # "detached head" detail (no target branch + behind) is pinned by the
    # stubbed use-case test instead.
    result = _run(["workspace", "branch", "set", "main", "--workspace", "prod"])
    assert result.exit_code == 0, result.output
    _push_commit(upstream, tmp_path, "behind.txt")
    subprocess.run(
        ["git", "-C", str(target / "upstream"), "checkout", "--detach", "--quiet"],
        check=True,
    )
    result = _run(["workspace", "sync", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert rows[0]["action"] == "skip"
    assert rows[0]["detail"] == "on detached, expected main"
    result = _run(["workspace", "status", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert rows[0]["branch"] is None
    assert fix["status_source"].startswith("status --porcelain=v2 --branch")
    subprocess.run(
        ["git", "-C", str(target / "upstream"), "checkout", "--quiet", "main"], check=True
    )
    result = _run(["workspace", "branch", "unset", "--workspace", "prod"])
    assert result.exit_code == 0, result.output

    # add --sync / import --sync clone only the touched repos (P36)
    other = tmp_path / "other.git"
    shutil.copytree(upstream, other)
    result = _run(["workspace", "add", f"file://{other}", "--workspace", "prod", "--sync"])
    assert result.exit_code == 0, result.output
    assert (target / "other").is_dir()


# ── f14: editor + shell (P38-P39, P26-P27 outcomes) ───────────────────────


def test_f14_editor_shell(tmp_path: Path) -> None:
    fix = fixture("f14-editor-shell.json")
    assert fix["editor_precedence"] == "--editor > $VISUAL > $EDITOR > vi"
    assert fix["dispatch"] == "argv + workspace path"

    assert resolve_editor_argv("code --wait", env={}) == ("code", "--wait")
    assert resolve_editor_argv(None, env={"VISUAL": "v", "EDITOR": "e"}) == ("v",)
    assert resolve_editor_argv(None, env={"EDITOR": "e"}) == ("e",)
    assert resolve_editor_argv(None, env={}) == ("vi",)
    with pytest.raises(WorkspaceError, match="editor command is empty"):
        resolve_editor_argv("   ", env={})
    with pytest.raises(WorkspaceError, match="could not parse editor command"):
        resolve_editor_argv("'unterminated", env={})
    assert fix["editor_errors"][2].startswith("editor not found:")

    # shell runner: shell=True pipes, DEVNULL stdin, buffered replay (P39)
    assert fix["shell"]["mode"] == "shell=True"
    assert fix["shell"]["stdin"] == "DEVNULL"
    assert fix["shell"]["default_timeout_s"] == 600.0
    from untaped.capabilities.workspace.domain import DEFAULT_FOREACH_TIMEOUT

    assert DEFAULT_FOREACH_TIMEOUT == 600.0
    completed = shell_runner("echo hi | tr a-z A-Z", cwd=tmp_path, timeout=60.0)
    assert completed.stdout.strip() == "HI"
    empty = shell_runner("cat", cwd=tmp_path, timeout=60.0)
    assert empty.returncode == 0
    assert empty.stdout == ""
    killed = shell_runner("sleep 30", cwd=tmp_path, timeout=0.05)
    assert killed.returncode == 124
    assert killed.stderr.endswith("timed out after 0.05s")

    # foreach outcome shapes via the application with stub ports (P27)
    from workspace.conftest import StubFilesystem, StubManifests

    ws = Workspace(name="smoke", path=tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    case = WorkspaceManifest.model_validate(
        {"name": "smoke", "repos": [{"url": "https://x/api.git", "name": "api"}]}
    )
    manifests = StubManifests({(tmp_path / "ws"): case})
    outcomes = Foreach(manifests, runner=shell_runner, fs=StubFilesystem())(
        ws, command="echo ok", timeout=60.0
    )
    assert [(o.repo, o.returncode) for o in outcomes] == [("api", -1)]
    assert outcomes[0].stderr.startswith("not cloned:")
    assert isinstance(outcomes[0].duration_s, float)
    assert fix["foreach_outcomes"]["not_cloned"]["returncode"] == -1

    (tmp_path / "ws" / "api").mkdir()
    outcomes = Foreach(
        manifests, runner=shell_runner, fs=StubFilesystem(dirs=[tmp_path / "ws" / "api"])
    )(ws, command="sleep 30", timeout=0.05)
    assert outcomes[0].returncode == 124
    assert outcomes[0].stderr.endswith("timed out after 0.05s")
    assert fix["foreach_outcomes"]["timeout"]["returncode"] == 124


# ── f15: shell-init (P40-P41) ────────────────────────────────────────────


def test_f15_shell_init() -> None:
    fix = fixture("f15-shell-init.json")
    assert ShellInit()("zsh") == fix["zsh"]
    assert ShellInit()("bash") == fix["bash"]
    assert ShellInit()("fish") == fix["fish"]
    assert ShellInit()("sh") != ShellInit()("zsh")
    assert "compdef" not in ShellInit()("sh")
    assert "complete" not in ShellInit()("sh")
    assert fix["sh_alias"] == "ShellInit('sh') returns POSIX body only"

    result = _run(["workspace", "shell-init", "zsh"])
    assert result.exit_code == 0, result.output
    assert result.stdout == fix["zsh"]

    result = _run(["workspace", "shell-init", "tcsh"])
    assert result.exit_code == 1, result.output
    assert "unsupported shell: 'tcsh'; supported: zsh, bash, fish" in result.stderr
    assert fix["error"].startswith("unsupported shell:")


# ── f16: branch cascade, adopt, parallelism (P42-P44) ─────────────────────


def test_f16_branch_adopt_parallel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix = fixture("f16-branch-adopt-parallel.json")
    assert fix["branch_cascade"] == "repos[].branch > defaults.branch > remote HEAD"
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()
    upstream = _bare_upstream(tmp_path)
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(tmp_path / "_push")], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path / "_push"), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "checkout", "--quiet", "-b", "develop"],
        check=True,
    )
    (tmp_path / "_push" / "d.txt").write_text("d")
    subprocess.run(["git", "-C", str(tmp_path / "_push"), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "commit", "--quiet", "--no-gpg-sign", "-m", "d"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "push", "--quiet", "origin", "develop"],
        check=True,
    )
    shutil.rmtree(tmp_path / "_push")

    _registered(tmp_path)
    _run(["workspace", "add", f"file://{upstream}", "--workspace", "prod"])
    _run(["workspace", "sync", "--workspace", "prod"])

    # cascade: per-repo branch wins over defaults.branch for target_branch
    _run(["workspace", "branch", "set", "main", "--workspace", "prod"])
    _run(["workspace", "branch", "set", "develop", "--repo", "upstream", "--workspace", "prod"])
    result = _run(["workspace", "show", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert rows[0]["repo_branch"] == "develop"
    assert rows[0]["target_branch"] == "develop"
    # branch apply touches only explicit targets: tracking branch from origin
    result = _run(["workspace", "branch", "apply", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert [sorted(r) for r in rows] == [["action", "detail", "repo", "target_branch", "workspace"]]
    assert rows[0]["action"] == "checkout"
    assert rows[0]["target_branch"] == "develop"
    assert next(iter(rows[0])) == "repo"
    assert fix["branch_apply"].startswith("explicit targets only")

    # sync never checks out: wrong-branch clone is skipped with a warning
    _run(["workspace", "branch", "unset", "--repo", "upstream", "--workspace", "prod"])
    result = _run(["workspace", "sync", "--workspace", "prod", "--format", "json"])
    rows = _json_rows(result, tmp_path)
    assert rows[0]["action"] == "skip"
    assert rows[0]["detail"] == "on develop, expected main"
    assert fix["sync_never_checkout"].startswith("skips dirty/diverged/wrong-branch")

    # adopt: existing manifest validates + registers, never rewrites (P43)
    adopted = tmp_path / "adopted"
    adopted.mkdir()
    manifest = WorkspaceManifest(name="prod", repos=[{"url": f"file://{upstream}"}])
    ManifestRepository().write(adopted, manifest)
    before = (adopted / "untaped.yml").read_bytes()
    result = _run(["workspace", "adopt", str(adopted), "--name", "adopted"])
    assert result.exit_code == 0, result.output
    assert (adopted / "untaped.yml").read_bytes() == before
    assert "adopted workspace 'adopted'" in result.stderr

    # adopt: missing manifest scans sorted children, warn-skips origin-less (P43).
    # Each child needs a distinct origin URL: P9 rejects duplicate repo urls,
    # so the two clones seed from two upstreams.
    scanned = tmp_path / "scanned"
    scanned.mkdir()
    upstream2 = _bare_upstream(tmp_path, name="upstream2.git")
    subprocess.run(["git", "clone", "--quiet", str(upstream2), str(scanned / "b-repo")], check=True)
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(scanned / "a-repo")], check=True)
    subprocess.run(["git", "init", "--quiet", str(scanned / "lonely")], check=True)
    result = _run(["workspace", "adopt", str(scanned)])
    assert result.exit_code == 0, result.output
    assert "warning:" in result.stderr
    assert fix["adopt"]["missing_manifest"].startswith("scan sorted immediate children")

    # parallelism contract (P44)
    import os as _os

    assert parallel_cap() == (_os.cpu_count() or 1) * 2
    assert fix["parallelism"]["default"] == 1
    assert fix["parallelism"]["cap"].startswith("2 * os.cpu_count()")
    capped = clamp_parallel(10**9, cap=parallel_cap(), policy="2 * os.cpu_count()")
    assert capped == parallel_cap()
    # foreach --parallel<=0 runs serially
    result = _run(["workspace", "foreach", "echo hi", "--workspace", "prod", "--parallel", "0"])
    assert result.exit_code == 0, result.output
    assert fix["parallelism"]["foreach_le_0"] == "serial"
    assert fix["parallelism"]["order"] == "manifest order"
    assert fix["quiet"]["flag"] == "--quiet / -q"


# ── f17: path output (P47) ───────────────────────────────────────────────


def test_f17_path_output(tmp_path: Path) -> None:
    fix = fixture("f17-path-output.json")
    assert fix["covers"] == "P47"
    first = _registered(tmp_path, "alpha")
    second = _registered(tmp_path, "beta")

    result = _run(["workspace", "path", "alpha", "beta"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        first.expanduser().resolve().as_posix(),
        second.expanduser().resolve().as_posix(),
    ]

    # resolve_each continues past failures; finish(any_failed) sets exit (P47)
    result = _run(["workspace", "path", "alpha", "ghost"])
    assert result.exit_code == 1, result.output
    assert result.stdout.splitlines() == [first.expanduser().resolve().as_posix()]
    assert "ghost" in result.stderr
    assert fix["partial_failure"]["exit"] == 1


# ── P45: capability statics ──────────────────────────────────────────────


def test_p45_capability_statics() -> None:
    assert isinstance(SPEC, CapabilitySpec)
    assert SPEC.name == "workspace"
    assert SPEC.config_section == "workspace"
    assert SPEC.profile_model is WorkspaceSettings
    assert SPEC.state_model is WorkspaceState
    assert set(WorkspaceSettings.model_fields) == {"cache_dir", "workspaces_dir"}
    assert set(WorkspaceState.model_fields) == {"workspaces"}
    (skill,) = SPEC.skills
    assert (skill.name, skill.description) == (
        "untaped-workspace",
        "Use the untaped-workspace CLI.",
    )
    assert SPEC.doctor_checks == ()

    composition = bootstrap.compose_root(builtins=(SPEC,), externals=())
    assert composition.quarantine == ()
    (registered,) = composition.capabilities
    assert registered.spec is SPEC
    assert registered.provider_ref.kind == "built-in"
    assert registered.provider_ref.distribution == "untaped"
    assert registered.provider_ref.entry_point == ""
    assert tuple(registered.provider_ref.api_requires) == (1.0, 2.0)

    factory_app = build_app()
    assert "workspace" in factory_app.name


# ── P46: runtime deps ────────────────────────────────────────────────────


def test_p46_runtime_deps() -> None:
    import tomllib

    expected = {
        "cyclopts>=4.16.0,<5",
        "pydantic>=2.13.3",
        "pyyaml>=6.0.3",
        "untaped>=3.0.0,<4",
    }
    declared = (REPO_ROOT / "pyproject.toml").read_text()
    data = tomllib.loads(declared)
    assert data["project"]["requires-python"] == ">=3.14"
    shipped = set(data["project"]["dependencies"])
    assert {"cyclopts>=4.16.0,<5", "pydantic>=2.13.3", "pyyaml>=6.0.3"} <= shipped

    records = tomllib.loads((REPO_ROOT / "docs" / "runtime-deps.toml").read_text())
    assert records["version"] == 1
    by_owner = [dep for dep in records["dep"] if dep["owner"] == "workspace"]
    assert {dep["requirement"] for dep in by_owner} == expected
    for dep in by_owner:
        assert dep["name"]
        assert dep["reason"].strip()


# ── P48: branch set --apply ──────────────────────────────────────────────


def test_p48_branch_set_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "_cache"))
    get_settings.cache_clear()
    upstream = _bare_upstream(tmp_path)
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(tmp_path / "_push")], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path / "_push"), "config", "user.name", "t"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "checkout", "--quiet", "-b", "develop"],
        check=True,
    )
    (tmp_path / "_push" / "d.txt").write_text("d")
    subprocess.run(["git", "-C", str(tmp_path / "_push"), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "commit", "--quiet", "--no-gpg-sign", "-m", "d"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path / "_push"), "push", "--quiet", "origin", "develop"],
        check=True,
    )
    shutil.rmtree(tmp_path / "_push")

    target = _registered(tmp_path)
    _run(["workspace", "add", f"file://{upstream}", "--workspace", "prod"])
    _run(["workspace", "sync", "--workspace", "prod"])

    # set --apply writes the manifest first, then checks out existing clones
    result = _run(["workspace", "branch", "set", "develop", "--workspace", "prod", "--apply"])
    assert result.exit_code == 0, result.output
    assert ManifestRepository().read(target).defaults.branch == "develop"
    current = subprocess.run(
        ["git", "-C", str(target / "upstream"), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current == "develop"
    assert "set default branch for 'prod' to develop" in result.stderr

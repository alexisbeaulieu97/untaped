"""End-to-end CLI tests for ``untaped github sweep``."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.github.cli import app
from untaped.capabilities.github.domain import (
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
)
from untaped.capabilities.github.settings import GithubSettings
from untaped.settings import get_settings, register_profile_settings
from untaped.testing import CliInvoker


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    register_profile_settings("github", GithubSettings)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yml"
    corpus = tmp_path / "corpus"
    cfg.write_text(
        f"profiles:\n  default:\n    github:\n      token: ghp_test\n      corpus_path: {corpus}\n"
    )
    return cfg


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _source_repo(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@example.com")
    _git(repo, "config", "user.name", "A")
    _git(repo, "config", "commit.gpgsign", "false")
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "branch", "-M", "main")
    return repo


def _commit_file(repo: Path, rel: str, content: str, message: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


def _repo(full_name: str, source: Path, *, clone_url: str | None = None) -> dict[str, object]:
    name = full_name.rsplit("/", 1)[1]
    return {
        "full_name": full_name,
        "name": name,
        "html_url": f"https://github.com/{full_name}",
        "clone_url": clone_url or source.as_uri(),
        "ssh_url": f"git@github.com:{full_name}.git",
        "default_branch": "main",
        "private": True,
        "archived": False,
        "fork": False,
    }


def test_sweep_table_has_predicate_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(
        tmp_path,
        "api",
        {"workflow.yml": "name: ci\nuses: acme/action@v1\n"},
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", source)])
        )
        result = CliInvoker().invoke(
            app,
            [
                "sweep",
                "--org",
                "acme",
                "--grep",
                "acme/action",
                "--has-file",
                "workflow.yml",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "acme/api" in result.stdout
    assert "grep:acme/action" in result.stdout
    assert "has-file:workflow.yml" in result.stdout


def test_sweep_repo_pipe_record_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "uses: acme/action@v1\n"})

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", source)])
        )
        result = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--grep", "acme/action", "--format", "pipe"],
        )

    assert result.exit_code == 0, result.output
    [line] = result.stdout.splitlines()
    envelope = json.loads(line)
    assert envelope["untaped"] == "1"
    assert envelope["kind"] == "github.sweep_repo"
    assert envelope["record"]["full_name"] == "acme/api"
    assert envelope["record"]["clone_url"] == source.as_uri()
    assert envelope["record"]["refs_matched"] == ["main"]
    assert envelope["record"]["hits"] == {"grep:acme/action": 1}


def test_sweep_match_pipe_record_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "uses: acme/action@v1\n"})

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", source)])
        )
        result = CliInvoker().invoke(
            app,
            [
                "sweep",
                "--org",
                "acme",
                "--grep",
                "acme/action",
                "--show",
                "matches",
                "--format",
                "pipe",
            ],
        )

    assert result.exit_code == 0, result.output
    [line] = result.stdout.splitlines()
    envelope = json.loads(line)
    assert envelope["kind"] == "github.sweep_match"
    assert envelope["record"] == {
        "full_name": "acme/api",
        "refs": ["main"],
        "path": "README.md",
        "line": 1,
        "text": "uses: acme/action@v1",
    }


def test_refs_column_only_when_selector_beyond_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "main\n"})
    _git(source, "checkout", "-q", "-b", "release/1")
    _commit_file(source, "release.txt", "release\n", "release")
    _git(source, "checkout", "-q", "main")

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", source)])
        )
        default = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--has-file", "README.md"],
        )
        beyond = CliInvoker().invoke(
            app,
            [
                "sweep",
                "--org",
                "acme",
                "--ref",
                "release/*",
                "--has-file",
                "release.txt",
            ],
        )

    assert default.exit_code == 0, default.output
    assert beyond.exit_code == 0, beyond.output
    assert "refs_matched" not in default.stdout
    assert "refs_matched" in beyond.stdout


def test_invalid_pattern_errors_before_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=[]))
        result = CliInvoker().invoke(app, ["sweep", "--org", "acme", "--grep", "["])

    assert result.exit_code != 0
    assert "--grep '['" in result.output
    assert "brackets" in result.output or "regular expression" in result.output
    assert mock.calls == []


def test_invalid_path_errors_before_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=[]))
        result = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--grep", "needle", "--path", ":(badmagic)foo"],
        )

    assert result.exit_code != 0
    assert "--path ':(badmagic)foo'" in result.output
    assert "Invalid pathspec magic" in result.output
    assert mock.calls == []


def test_path_without_content_suggests_has_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["sweep", "--org", "acme", "--path", "src/**"])

    assert result.exit_code != 0
    assert "use --has-file" in result.output


def test_sweep_parallel_help_documents_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(app, ["sweep", "--help"])

    assert result.exit_code == 0, result.output
    assert "--parallel" in result.output
    assert "32" in result.output


def test_parallel_must_be_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    result = CliInvoker().invoke(
        app,
        ["sweep", "--repo", "acme/api", "--has-file", "README.md", "--parallel", "0"],
    )

    assert result.exit_code != 0
    assert "--parallel must be positive" in result.output


def test_exit_code_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "nothing here\n"})

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", source)])
        )
        clean = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--grep", "needle"],
        )

    (source / "README.md").write_text("needle\n")
    _git(source, "add", "README.md")
    _git(source, "commit", "-q", "-m", "needle")
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", source)])
        )
        fail_on_match = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--grep", "needle", "--sync", "--fail-on-match"],
        )

    missing = _repo("acme/api", source, clone_url=(tmp_path / "missing").as_uri())
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=[missing]))
        unscanned_default = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--grep", "needle"],
        )
        unscanned_strict = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--grep", "needle", "--strict"],
        )

    assert clean.exit_code == 0, clean.output
    assert fail_on_match.exit_code == 1, fail_on_match.output
    assert unscanned_default.exit_code == 0, unscanned_default.output
    assert unscanned_strict.exit_code == 1, unscanned_strict.output


def test_stdin_full_names_enter_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(tmp_path, "api", {"README.md": "needle\n"})

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/repos/acme/api").mock(
            return_value=httpx.Response(200, json=_repo("acme/api", source))
        )
        result = CliInvoker().invoke(
            app,
            ["sweep", "--stdin", "--grep", "needle", "--format", "json"],
            input="acme/api\n",
        )

    assert result.exit_code == 0, result.output
    assert route.called
    assert json.loads(result.stdout)[0]["full_name"] == "acme/api"


def test_no_owners_skips_enrichment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))
    source = _source_repo(
        tmp_path,
        "api",
        {
            "README.md": "needle\n",
            ".github/CODEOWNERS": "* @all\nREADME.md @docs\n",
        },
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[_repo("acme/api", source)])
        )
        result = CliInvoker().invoke(
            app,
            ["sweep", "--org", "acme", "--grep", "needle", "--no-owners"],
        )

    assert result.exit_code == 0, result.output
    assert "owners" not in result.stdout
    assert "@docs" not in result.stdout


def test_content_modifiers_reach_validation_and_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_write_config(tmp_path)))

    class FakeCorpus:
        instance: FakeCorpus | None = None

        def __init__(self) -> None:
            FakeCorpus.instance = self
            self.validated: list[tuple[str, tuple[str, ...], bool]] = []
            self.grep_flags: list[tuple[bool, bool, bool]] = []

        def validate_pattern(
            self,
            *,
            root: Path,
            pattern: str,
            paths: tuple[str, ...],
            fixed_strings: bool,
        ) -> str | None:
            self.validated.append((pattern, paths, fixed_strings))
            return None

        def list_repos(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
            return (
                CorpusRepoResult(
                    repo="acme/api",
                    ref="main",
                    path=str(root / "api.git"),
                    clone_url="https://github.example.com/acme/api.git",
                    fetched_at="2026-07-06T12:00:00+00:00",
                ),
            )

        def repo_freshness(
            self,
            repo: CorpusRepoTarget,
            *,
            root: Path,
        ) -> CorpusFreshness | None:
            return None

        def local_refs(
            self,
            repo: CorpusRepoTarget,
            *,
            root: Path,
            selector: object,
        ) -> tuple[str, ...]:
            return ("main",)

        def grep_ref(
            self,
            repo: CorpusRepoTarget,
            *,
            root: Path,
            ref: str,
            pattern: str,
            paths: tuple[str, ...],
            ignore_case: bool,
            fixed_strings: bool,
            word_regexp: bool,
        ) -> tuple[GrepHit, ...]:
            self.grep_flags.append((ignore_case, fixed_strings, word_regexp))
            return (GrepHit(path="README.md", line=1, text=pattern, blob_oid="abc123"),)

        def tree_paths(self, repo: CorpusRepoTarget, *, root: Path, ref: str) -> tuple[str, ...]:
            return ()

        def read_blob(
            self,
            repo: CorpusRepoTarget,
            *,
            root: Path,
            ref: str,
            path: str,
        ) -> str | None:
            return None

    monkeypatch.setattr("untaped.capabilities.github.infrastructure.GitCorpusCache", FakeCorpus)

    result = CliInvoker().invoke(
        app,
        [
            "sweep",
            "--repo",
            "acme/api",
            "--no-sync",
            "--grep",
            "needle",
            "--path",
            "README.md",
            "-i",
            "-F",
            "-w",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert FakeCorpus.instance is not None
    assert FakeCorpus.instance.validated == [("needle", ("README.md",), True)]
    assert FakeCorpus.instance.grep_flags == [(True, True, True)]

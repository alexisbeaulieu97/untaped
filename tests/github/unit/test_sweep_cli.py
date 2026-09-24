"""End-to-end CLI tests for ``untaped github sweep`` against real local Git repos."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import httpx
import pytest
import respx

from untaped.bootstrap import build_root_app
from untaped.capabilities.github.cli import app
from untaped.testing import CliInvoker, CliResult, invoke_cli

SourceRepo = Callable[[str, dict[str, str | bytes]], Path]


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n  default:\n    github:\n      token: ghp_test\n"
        f"      corpus_path: {tmp_path / 'corpus'}\n"
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))


def _repo(full_name: str, source: Path, *, clone_url: str | None = None) -> dict[str, object]:
    return {
        "full_name": full_name,
        "name": full_name.rsplit("/", 1)[1],
        "html_url": f"https://github.com/{full_name}",
        "clone_url": clone_url or source.as_uri(),
        "default_branch": "main",
        "archived": False,
    }


def _sweep(
    args: Sequence[str],
    *,
    org: list[dict[str, object]] | None = None,
    repos: Mapping[str, httpx.Response] | None = None,
    input: str | None = None,
) -> tuple[CliResult, list[str]]:
    """Run ``sweep <args>`` and return the API paths it requested.

    ``org`` answers ``/orgs/acme/repos`` and ``repos`` answers each ``/repos/<name>``.
    """
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        if org is not None:
            mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=org))
        for name, response in (repos or {}).items():
            mock.get(f"/repos/{name}").mock(return_value=response)
        result = CliInvoker().invoke(app, ["sweep", *args], input=input)
        return result, [call.request.url.path for call in mock.calls]


def _json(result: CliResult) -> list[dict[str, object]]:
    assert result.exit_code == 0, result.output
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


@pytest.mark.parametrize(
    ("show", "kind", "records"),
    [
        (
            "repos",
            "github.sweep_repo",
            [{"refs_matched": ["main"], "hits": {"grep:needle": 3}, "owners": []}],
        ),
        (
            "files",
            "github.sweep_file",
            [
                {"path": "a.py", "refs": ["main"], "hits": 2},
                {"path": "b.py", "refs": ["main"], "hits": 1},
            ],
        ),
        (
            "matches",
            "github.sweep_match",
            [
                {"refs": ["main"], "path": "a.py", "line": 1, "text": "needle()"},
                {"refs": ["main"], "path": "a.py", "line": 2, "text": "needle()"},
                {"refs": ["main"], "path": "b.py", "line": 1, "text": "needle"},
            ],
        ),
    ],
)
def test_sweep_pipe_records_per_show_mode(
    source_repo: SourceRepo, show: str, kind: str, records: list[dict[str, object]]
) -> None:
    source = source_repo("api", {"a.py": "needle()\nneedle()\n", "b.py": "needle\n", "c.py": "x\n"})

    result, _ = _sweep(
        ["--org", "acme", "--grep", "needle", "--show", show, "--format", "pipe"],
        org=[_repo("acme/api", source)],
    )

    assert result.exit_code == 0, result.output
    envelopes = [json.loads(line) for line in result.stdout.splitlines()]
    assert {(env["untaped"], env["kind"]) for env in envelopes} == {("1", kind)}
    assert [env["record"]["full_name"] for env in envelopes] == ["acme/api"] * len(records)
    for envelope, expected in zip(envelopes, records, strict=True):
        assert envelope["record"].items() >= expected.items()
    if show == "repos":
        assert envelopes[0]["record"]["clone_url"] == source.as_uri()


def test_sweep_table_columns_follow_predicates_refs_and_owners(
    source_repo: SourceRepo, git: Callable[..., str], commit_file: Callable[..., None]
) -> None:
    source = source_repo(
        "api", {"workflow.yml": "uses: acme/action@v1\n", ".github/CODEOWNERS": "* @docs\n"}
    )
    git(source, "checkout", "-q", "-b", "release/1")
    commit_file(source, "release.txt", "release\n")
    git(source, "checkout", "-q", "main")
    listing = [_repo("acme/api", source)]
    predicates = ["--org", "acme", "--grep", "acme/action", "--has-file", "workflow.yml"]

    default, _ = _sweep(predicates, org=listing)
    beyond, _ = _sweep([*predicates, "--ref", "release/*"], org=listing)
    no_owners, _ = _sweep([*predicates, "--no-owners"], org=listing)

    assert default.exit_code == beyond.exit_code == no_owners.exit_code == 0
    assert "grep:acme/action" in default.stdout
    assert "has-file:workflow.yml" in default.stdout
    assert "@docs" in default.stdout
    assert "refs_matched" not in default.stdout
    assert "refs_matched" in beyond.stdout
    assert "owners" not in no_owners.stdout
    assert "@docs" not in no_owners.stdout


def test_content_modifiers_and_path_reach_the_grep(source_repo: SourceRepo) -> None:
    lines = "a NEEDLE.x b\nneedleXx\nzneedle.xz\n"
    source = source_repo("api", {"README.md": lines, "other.md": lines})

    flags = ["--path", "README.md", "-i", "-F", "--word-regexp", "--show", "matches"]

    result, _ = _sweep(
        ["--org", "acme", "--grep", "needle.x", *flags, "--format", "json"],
        org=[_repo("acme/api", source)],
    )

    assert [(row["path"], row["text"]) for row in _json(result)] == [("README.md", "a NEEDLE.x b")]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--org", "acme", "--grep", "["], "--grep '['"),
        (
            ["--org", "acme", "--grep", "needle", "--path", ":(badmagic)foo"],
            "--path ':(badmagic)foo': fatal: Invalid pathspec magic",
        ),
        (["--org", "acme", "--path", "src/**"], "use --has-file"),
        (["--repo", "acme/api", "--has-file", "README.md", "--parallel", "0"], "--parallel"),
        (["--grep", "needle"], "sweep requires --org, --team, --repo, or --stdin"),
    ],
)
def test_sweep_usage_errors_fail_before_any_request(args: list[str], message: str) -> None:
    result, requested = _sweep(args, org=[])

    assert result.exit_code == 2, result.output
    assert message in result.output
    assert requested == []


def test_sweep_stdin_rejects_records_of_another_kind() -> None:
    envelope = json.dumps({"untaped": "1", "kind": "github.code", "record": {"repo": "a/b"}})

    result, _ = _sweep(["--stdin", "--grep", "needle"], input=f"{envelope}\n")

    assert result.exit_code == 2, result.output
    assert "github.code" in result.stderr


def test_exit_code_matrix(
    tmp_path: Path, source_repo: SourceRepo, commit_file: Callable[..., None]
) -> None:
    source = source_repo("api", {"README.md": "nothing here\n"})
    args = ["--org", "acme", "--grep", "needle", "--refresh"]
    listing = [_repo("acme/api", source)]
    missing = [_repo("acme/api", source, clone_url=(tmp_path / "missing").as_uri())]

    clean, _ = _sweep([*args, "--fail-on-match"], org=listing)
    commit_file(source, "README.md", "needle\n")
    matched, _ = _sweep(args, org=listing)
    fail_on_match, _ = _sweep([*args, "--fail-on-match"], org=listing)
    unscanned, _ = _sweep(args, org=missing)
    unscanned_strict, _ = _sweep([*args, "--strict"], org=missing)

    assert clean.exit_code == 0, clean.output
    assert matched.exit_code == 0, matched.output
    assert fail_on_match.exit_code == 3, fail_on_match.output
    assert unscanned.exit_code == 0, unscanned.output
    assert unscanned_strict.exit_code == 3, unscanned_strict.output


def test_sweep_old_flag_spellings_are_deprecated_aliases() -> None:
    root = build_root_app(externals=[])

    cached = invoke_cli(
        root, ["github", "sweep", "--org", "acme", "--grep", "x", "-w", "--no-sync"]
    )
    refreshed = invoke_cli(root, ["github", "sweep", "--org", "acme", "--grep", "[", "--sync"])

    assert cached.exit_code == 1, cached.output
    assert "`-w` is deprecated" in cached.stderr
    assert "`--no-sync` is deprecated" in cached.stderr
    assert "corpus has no repos in scope; run without --cached" in cached.stderr
    assert refreshed.exit_code == 2, refreshed.output
    assert "`--sync` is deprecated" in refreshed.stderr


def test_missing_explicit_repo_is_unscanned_and_strict_fails(source_repo: SourceRepo) -> None:
    source = source_repo("api", {"README.md": "needle\n"})
    args = ["--repo", "acme/gone", "--repo", "acme/api", "--grep", "needle", "--format", "json"]
    routes = {
        "acme/gone": httpx.Response(404, json={"message": "Not Found"}),
        "acme/api": httpx.Response(200, json=_repo("acme/api", source)),
    }

    lenient, _ = _sweep(args, repos=routes)
    strict, _ = _sweep([*args, "--strict"], repos=routes)

    assert [row["full_name"] for row in _json(lenient)] == ["acme/api"]
    assert "warning: unscanned acme/gone" in lenient.stderr
    assert strict.exit_code == 3, strict.output


def test_every_explicit_repo_failing_resolution_exits_non_zero() -> None:
    result, _ = _sweep(
        ["--repo", "acme/a", "--repo", "acme/b", "--grep", "needle"],
        repos={
            "acme/a": httpx.Response(404, json={"message": "Not Found"}),
            "acme/b": httpx.Response(403, json={"message": "Must have admin rights"}),
        },
    )

    assert result.exit_code == 1, result.output
    assert "no requested repository could be resolved" in result.stderr
    assert "acme/a" in result.stderr
    assert "acme/b" in result.stderr


def test_failed_refresh_warns_that_cached_copy_was_scanned(source_repo: SourceRepo) -> None:
    source = source_repo("api", {"README.md": "needle\n"})
    listing = [_repo("acme/api", source)]
    args = ["--org", "acme", "--grep", "needle", "--format", "json"]

    first, _ = _sweep(args, org=listing)
    shutil.rmtree(source)
    refreshed, _ = _sweep([*args, "--refresh"], org=listing)

    assert first.exit_code == 0, first.output
    assert [row["full_name"] for row in _json(refreshed)] == ["acme/api"]
    assert "refresh failed for 1 repo; scanned cached copies" in refreshed.stderr
    assert "warning: stale acme/api:" in refreshed.stderr


def test_branch_and_tag_with_same_name_are_both_swept(
    source_repo: SourceRepo, git: Callable[..., str], commit_file: Callable[..., None]
) -> None:
    source = source_repo("api", {"README.md": "needle tag\n"})
    git(source, "tag", "x")
    git(source, "checkout", "-q", "-b", "x")
    commit_file(source, "README.md", "needle branch\n")
    git(source, "checkout", "-q", "main")
    commit_file(source, "README.md", "nothing\n")
    args = ["--org", "acme", "--refs", "all", "--grep", "needle", "--format", "json"]

    repos, _ = _sweep(args, org=[_repo("acme/api", source)])
    matches, _ = _sweep([*args, "--show", "matches", "--cached"])

    [row] = _json(repos)
    assert row["refs_matched"] == ["heads/x", "tags/x"]
    # Regression: table-only per-predicate columns must not leak into json
    # (they produced "unknown column" warnings and null keys).
    assert set(row) == {"full_name", "clone_url", "refs_matched", "hits", "owners", "synced_at"}
    assert row["hits"] == {"grep:needle": 1}
    assert "unknown column" not in repos.stderr
    assert sorted((m["refs"], m["text"]) for m in _json(matches)) == [  # type: ignore[type-var]
        (["heads/x"], "needle branch"),
        (["tags/x"], "needle tag"),
    ]


def test_piped_records_skip_lookups_and_bare_names_are_resolved(source_repo: SourceRepo) -> None:
    source = source_repo("api", {"README.md": "needle\n"})
    other = source_repo("web", {"README.md": "needle\n"})
    listed = {"untaped": "1", "kind": "github.repo", "record": _repo("acme/api", source)}
    swept = {"untaped": "1", "kind": "github.sweep_repo", "record": {"full_name": "acme/web"}}

    result, requested = _sweep(
        ["--stdin", "--grep", "needle", "--format", "json"],
        repos={"acme/web": httpx.Response(200, json=_repo("acme/web", other))},
        input=f"{json.dumps(listed)}\n{json.dumps(swept)}\n",
    )

    assert [row["full_name"] for row in _json(result)] == ["acme/api", "acme/web"]
    assert requested == ["/repos/acme/web"]

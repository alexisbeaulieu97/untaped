"""``ansible source``: save, patch, list, remove, refresh and report on sources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import respx

from ansible_index_support import (
    BAD_CREDENTIALS,
    PLATFORM,
    RATE_LIMIT_EXCEEDED,
    REQS,
    TEMPLATED,
    capture_git_refresh,
    fake_refresh,
    make_edge,
    mock_expansion,
    open_index,
    prod,
    seed,
    table,
)
from ansible_test_support import github_source, read_state, run, run_root, use_config
from untaped.runtime.settings import get_settings
from untaped.sdk import Failure, Message, Progress, UntapedError
from untaped.sdk.testing import progress_clock
from untaped_ansible import refresh
from untaped_ansible.application.refresh_git_index import RefreshResult
from untaped_ansible.domain.payloads import RepoFailure
from untaped_ansible.errors import RepoStoreError
from untaped_ansible.infrastructure import GitSourceStore
from untaped_git import api as git_api
from untaped_git.api import HostBudget


def _graphql_repo_node(repo: str, *, sha: str) -> dict[str, object]:
    empty_page = {"hasNextPage": False, "endCursor": None}
    return {
        "nameWithOwner": repo,
        "defaultBranchRef": {"name": "main"},
        "heads": {"pageInfo": empty_page, "nodes": [{"name": "main", "target": {"oid": sha}}]},
        "tags": {"pageInfo": empty_page, "nodes": []},
    }


def _rate_limit(remaining: int = 4900) -> dict[str, object]:
    return {"cost": 1, "remaining": remaining, "resetAt": "2026-01-01T00:00:00Z"}


def _mock_refresh_repos(
    mock: respx.MockRouter,
    repos: dict[str, str],
    *,
    missing: tuple[str, ...] = (),
    rate_limit_remaining: int = 4900,
    on_probe: Callable[[], object] | None = None,
) -> None:
    """Mock source-refresh expansion (REST) and the GraphQL ref probe.

    ``repos`` maps ``owner/name`` to the sha of its single ``heads/main``
    ref; ``missing`` repos expand fine but probe as NOT_FOUND. ``on_probe``
    runs as GitHub answers the probe.
    """
    names = sorted([*repos, *missing])
    mock_expansion(mock, names)
    data: dict[str, object] = {"rateLimit": _rate_limit(rate_limit_remaining)}
    errors: list[dict[str, object]] = []
    for index, full_name in enumerate(names):
        alias = f"r{index}"
        if full_name in missing:
            data[alias] = None
            errors.append(
                {
                    "type": "NOT_FOUND",
                    "path": [alias],
                    "message": f"Could not resolve to a Repository named {full_name!r}.",
                }
            )
        else:
            data[alias] = _graphql_repo_node(full_name, sha=repos[full_name])
    payload: dict[str, object] = {"data": data}
    if errors:
        payload["errors"] = errors
    response = httpx.Response(200, json=payload)

    def answer(request: httpx.Request) -> httpx.Response:
        if on_probe is not None:
            on_probe()
        return response

    mock.post("/graphql").mock(side_effect=answer)


def _flaky_graphql(request: httpx.Request) -> httpx.Response:
    """GraphQL probe that 502s for any batch containing ``acme/flaky``."""
    if "flaky" in request.content.decode():
        return httpx.Response(502, text="Bad Gateway")
    node = _graphql_repo_node("acme/ok", sha="sha-ok")
    return httpx.Response(200, json={"data": {"rateLimit": _rate_limit(), "r0": node}})


class _SeedGitCache:
    """Repo store stub: fetches succeed, ``files`` are the dependency files."""

    files: ClassVar[dict[str, str]] = {}

    def __init__(self, **_: Any) -> None:
        """Built as the refresh builds the store, for its plugin."""

    def fetch(self, url: str, refs: Any) -> None:
        return None

    def refusal(self, ref: Any) -> str | None:
        return None

    def read_files(self, url: str, shas: Sequence[str], paths: Sequence[str]) -> Any:
        found = {path: content for path, content in self.files.items() if path in paths}
        return dict.fromkeys(shas, found)

    def list_files(self, url: str, sha: str) -> list[tuple[str, str]]:
        return [(path, _blob(content)) for path, content in self.files.items()]

    def read_blobs(self, url: str, blobs: Sequence[str]) -> dict[str, str]:
        contents = {_blob(content): content for content in self.files.values()}
        return {blob: contents[blob] for blob in blobs}


@pytest.fixture(autouse=True)
def _remote(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``git ls-remote``: each repo's ``main`` is ``sha-NAME``; a ``flaky`` one is unreachable.

    Returns the URLs it was asked for.
    """
    asked: list[str] = []

    def ls_remote(url: str, patterns: Sequence[str] = ()) -> dict[str, str]:
        asked.append(url)
        name = url.removesuffix(".git").rsplit("/", maxsplit=1)[-1]
        if name == "flaky":
            raise UntapedError(f"cannot reach {url}", category="unavailable", system="git")
        return {"HEAD": f"sha-{name}", "refs/heads/main": f"sha-{name}"}

    monkeypatch.setattr(git_api, "ls_remote", ls_remote)
    monkeypatch.setattr(git_api, "default_branch", lambda url: "main")
    return asked


def _blob(content: str) -> str:
    """A content address for a fake store's file, as git's blob ids are."""
    return hashlib.sha1(content.encode(), usedforsecurity=False).hexdigest()


class _NoFetchGitCache(_SeedGitCache):
    """Repo store stub proving unchanged repos never fetch or scan."""

    def fetch(self, url: str, refs: Any) -> None:
        raise AssertionError(f"unexpected git fetch for {url}")

    def read_files(self, url: str, shas: Sequence[str], paths: Sequence[str]) -> Any:
        raise AssertionError("unexpected dependency file read")


def _seed_unchanged_scan(
    monkeypatch: pytest.MonkeyPatch,
    repos: dict[str, str],
    *,
    missing: tuple[str, ...] = (),
) -> None:
    """Seed scans a later refresh's probe will consider unchanged.

    Runs one ``source refresh prod`` through the public CLI (with the git
    transport stubbed out) so the cached scans carry exactly the metadata and
    fingerprints a subsequent refresh recomputes.
    """
    with monkeypatch.context() as patcher:
        patcher.setattr(refresh, "GitSourceStore", _SeedGitCache)
        with respx.mock(base_url="https://api.github.com") as mock:
            _mock_refresh_repos(mock, repos, missing=missing)
            result = run("source", "refresh", "prod")
    assert result.exit_code == (1 if missing else 0), result.output


def _selector(*flags: str) -> str:
    """What ``github repos selector FLAGS -f pipe`` prints: github's own selector record."""
    piped = run_root("github", "repos", "selector", *flags, "--format", "pipe")
    assert piped.exit_code == 0, piped.output
    return piped.stdout


def test_source_set_stores_the_selector_its_plugin_issued(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)  # no token: issuing a selector reads nothing
    selector = _selector("--org", "acme", "--team", "platform", "--repo", "acme/site")
    ref_flags = ["--ref-kind", "heads", "--ref-pattern", "release/*"]

    result = run(
        "source",
        "set",
        "prod",
        "--stdin",
        "--path",
        "deploy/requirements.yml",
        *ref_flags,
        "--ref-scan-default",
        "default_branch",
        input=selector,
    )
    assert result.exit_code == 0, result.output

    # A single source renders as a bare object: the selector as github issued it.
    result = run("source", "get", "prod", "--format", "json")
    record = {"orgs": ["acme"], "teams": ["acme/platform"], "repos": ["acme/site"]}
    assert json.loads(result.stdout) == {
        "name": "prod",
        "source": "github",
        "selects": "orgs acme; teams acme/platform; repos acme/site",
        "selector": {
            "match": [],
            "source": {"plugin": "github", "kind": "github.repo_selector", "record": record},
        },
        "dependency_paths": ["deploy/requirements.yml"],
        "ref_kinds": ["heads"],
        "ref_patterns": ["release/*"],
        "ref_scan_default": "default_branch",
    }

    # ... and as a vertical detail view, not a boxed one-row table.
    result = run("source", "get", "prod", "--format", "table")
    assert "name: prod" in result.stdout
    assert not any(ch in result.stdout for ch in "╭╮╰╯┌┐└┘│─")

    assert run("source", "remove", "prod", "--yes").exit_code == 0
    assert "sources" not in read_state()


def test_source_set_from_a_plugin_takes_its_listing_narrowed_by_match(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch)

    result = run("source", "set", "infra", "--from", "gitlab", "--match", "infra/*")

    assert result.exit_code == 0, result.output
    [row] = json.loads(run("source", "list", "--format", "json").stdout)
    assert (row["source"], row["selects"], row["selector"]) == (
        "gitlab",
        "match infra/*",
        {"match": ["infra/*"], "source": None},
    )


def test_source_set_match_narrows_a_piped_selector(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)

    result = run(
        "source",
        "set",
        "prod",
        "--stdin",
        "--match",
        "acme/api-*",
        input=_selector("--org", "acme"),
    )

    assert result.exit_code == 0, result.output
    [row] = json.loads(run("source", "list", "--format", "json").stdout)
    assert row["selects"] == "orgs acme; match acme/api-*"


def test_source_set_keeps_the_match_a_piped_selector_carries(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)
    issued = {"plugin": "github", "kind": "github.repo_selector", "record": {"orgs": ["acme"]}}
    record = {"match": ["acme/api-*"], "source": issued}
    selector = {"untaped": "1", "kind": "git.repo_selector", "record": record}

    result = run(
        "source",
        "set",
        "prod",
        "--stdin",
        "--match",
        "acme/web-*",
        "--match",
        "acme/api-*",
        input=json.dumps(selector) + "\n",
    )

    assert result.exit_code == 0, result.output
    [row] = json.loads(run("source", "list", "--format", "json").stdout)
    assert row["selector"]["match"] == ["acme/api-*", "acme/web-*"]


def test_source_set_replaces_a_damaged_row_of_its_name(tmp_path: Path, monkeypatch) -> None:
    # A row in an older shape reads as damaged; set replaces a definition whole.
    old = {"name": "prod", "orgs": ["acme"]}
    use_config(tmp_path, monkeypatch, {"sources": [old, github_source("other", orgs=["acme"])]})

    result = run("source", "set", "prod", "--from", "gitlab", "--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "updated"
    assert sorted(row["name"] for row in read_state()["sources"]) == ["other", "prod"]
    assert run("source", "get", "prod").exit_code == 0


def test_source_refresh_names_a_plugin_that_lists_no_repos(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)
    assert run("source", "set", "infra", "--from", "nosuch").exit_code == 0

    result = run("source", "refresh", "infra")

    assert result.exit_code == 4
    assert "no installed plugin named nosuch fills it" in result.stderr


@pytest.mark.parametrize(
    ("args", "piped", "message"),
    [
        ([], None, "source set needs --stdin (a selector a plugin issued) or --from PLUGIN"),
        (["--stdin", "--from", "gitlab"], ("--org", "acme"), "--from names the plugin of a source"),
        (["--stdin"], ("--org", "acme"), None),
    ],
)
def test_source_set_takes_one_selector_or_a_plugin(
    tmp_path: Path,
    monkeypatch,
    args: list[str],
    piped: tuple[str, ...] | None,
    message: str | None,
) -> None:
    use_config(tmp_path, monkeypatch)
    given = None if piped is None else _selector(*piped)
    if message is None:  # two selectors piped
        given = (given or "") * 2
        message = "source set reads one selector from stdin, got 2"

    result = run("source", "set", "prod", *args, input=given)

    assert result.exit_code == 2
    assert message in result.stderr
    assert "sources" not in read_state()


def test_source_set_refuses_a_selector_no_plugin_issued(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)
    row = {"untaped": "1", "kind": "git.repo_selector", "record": {"match": ["infra/*"]}}

    result = run("source", "set", "prod", "--stdin", input=json.dumps(row) + "\n")

    assert result.exit_code == 2
    assert "the piped selector names no plugin that issued it" in result.stderr


def test_source_remove_keeps_the_source_when_a_release_fails(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, prod(), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/site": "sha-site"})

    def fail(self: GitSourceStore, url: str) -> None:
        raise RepoStoreError(f"could not lock repo store for {url}", category="unavailable")

    monkeypatch.setattr(GitSourceStore, "release", fail)
    result = run("source", "remove", "prod", "--yes", "--format", "json")

    assert result.exit_code == 5  # the lock was unavailable: retry later
    row = json.loads(result.stdout)
    assert row["changes"] == ["failed to release acme/site"]
    assert row["error"]["category"] == "unavailable"
    assert row["error"]["message"] == "1 repo not released; source kept"
    failures = [d for d in result.diagnostics if isinstance(d, Failure)]
    assert [(d.item, d.message) for d in failures] == [
        ("acme/site", "could not lock repo store for https://github.com/acme/site.git")
    ]
    hints = [d.message for d in result.diagnostics if isinstance(d, Message) and d.level == "hint"]
    assert hints == ["run `untaped ansible source remove prod`"]
    # Kept, with its index rows, so the rerun finds what to release.
    assert read_state()["sources"][0]["name"] == "prod"
    assert open_index(tmp_path).status("source:prod") is not None


def test_source_patch_add_remove_and_clear_updates_state(tmp_path: Path, monkeypatch) -> None:
    source = github_source(
        "prod",
        orgs=["acme"],
        dependency_paths=["old/requirements.yml"],
        ref_kinds=["heads"],
        ref_patterns=["release/*"],
    )
    use_config(tmp_path, monkeypatch, {"sources": [source]})

    result = run(
        "source",
        "patch",
        "prod",
        *("--clear-path", "--add-path", REQS),
        *("--remove-ref-kind", "heads", "--add-ref-kind", "tags"),
        *("--clear-ref-pattern", "--add-ref-pattern", "v*"),
    )

    assert result.exit_code == 0, result.output
    assert "action: updated" in result.stdout
    assert "cleared path" in result.stdout
    assert "added ref-kind tags" in result.stdout
    assert read_state()["sources"] == [
        {
            **source,
            "dependency_paths": [REQS],
            "ref_kinds": ["tags"],
            "ref_patterns": ["v*"],
        }
    ]


def test_source_patch_leaves_the_selector_to_source_set(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, prod())

    result = run("source", "patch", "prod", "--add-repo", "acme/api")

    assert result.exit_code == 2
    assert read_state() == prod()


@pytest.mark.parametrize(
    ("args", "message", "exit_code"),
    [
        (["set", "prod", "--from", "x", "--ref-kind", "pulls"], "ref-kind must be heads or", 2),
        (["patch", "missing", "--add-path", REQS], "source not found: 'missing'", 1),
        (["patch", "prod", "--remove-path", "x.yml"], "source 'prod' has no path x.yml", 1),
        (["patch", "prod", "--add-ref-kind", "pulls"], "ref-kind must be heads or tags", 2),
    ],
)
def test_source_set_and_patch_reject_invalid_definitions(
    tmp_path: Path, monkeypatch, args: list[str], message: str, exit_code: int
) -> None:
    use_config(tmp_path, monkeypatch, prod())

    result = run("source", *args)

    assert result.exit_code == exit_code
    assert message in result.output
    assert read_state() == prod()


@pytest.mark.usefixtures("composed")
@pytest.mark.parametrize(
    ("args", "kept"),
    [
        (["set", "platform", "--stdin"], True),
        (["patch", "platform", "--add-ref-kind", "heads"], False),
        (["patch", "platform", "--clear-path"], True),
        (["set", "platform", "--stdin", "--match", "acme/*"], False),
        (["remove", "platform", "--yes"], False),
    ],
)
def test_source_changes_clear_its_cached_data_only_when_the_definition_changes(
    tmp_path: Path, monkeypatch, args: list[str], kept: bool
) -> None:
    seed(tmp_path, "source:platform", make_edge())
    use_config(tmp_path, monkeypatch, PLATFORM)

    result = run("source", *args, input=_selector("--repo", "acme/site"))

    assert result.exit_code == 0, result.output
    assert (open_index(tmp_path).status("source:platform") is not None) is kept


def test_source_status_classifies_sources_with_utc_timestamps(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:stale", scanned_at=datetime(2026, 1, 2, 3, 4, 5, 678, tzinfo=UTC))
    use_config(
        tmp_path,
        monkeypatch,
        {
            "sources": [
                github_source("unindexed", repos=["acme/site"]),
                github_source("stale", repos=["acme/base"]),
            ]
        },
    )

    result = run("source", "status", "--format", "json")
    missing = run("source", "status", "missing", "--format", "json")

    assert result.exit_code == 0, result.output
    rows = {row["source"]: row for row in json.loads(result.stdout)}
    assert rows["unindexed"]["state"] == "not_refreshed"
    assert rows["stale"]["state"] == "stale"
    assert rows["stale"]["scanned_at"] == "2026-01-02T03:04:05.000678Z"
    # Seeded from the index on first read: the last refresh's time.
    assert rows["stale"]["refreshed_at"] == "2026-01-02T03:04:05.000678Z"
    assert (rows["stale"]["stale"], rows["unindexed"]["refreshed_at"]) == (True, None)
    assert missing.exit_code == 1
    assert "source not found: 'missing'" in missing.output


def test_source_refresh_wires_aliases_and_defaults(tmp_path: Path, monkeypatch) -> None:
    # An invalid theme degrades the refresh's stderr lines instead of failing
    # an otherwise-valid refresh.
    use_config(
        tmp_path,
        monkeypatch,
        {**prod(), "aliases": {"common": "acme/common"}},
        token=True,
        ui={"theme": "missing"},
    )
    captured = capture_git_refresh(monkeypatch)

    result = run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert "refreshed source prod: 1 repo, 1 ref, 0 edges" in result.stderr
    assert captured["aliases"] == {"common": "acme/common"}
    assert captured["concurrency"] == 8


def test_source_refresh_reading_refs_with_ls_remote_skips_unchanged_fetches(
    tmp_path: Path, monkeypatch, _remote: list[str]
) -> None:
    use_config(tmp_path, monkeypatch, prod(), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/site": "sha-site"})
    monkeypatch.setattr(refresh, "GitSourceStore", _NoFetchGitCache)

    with respx.mock(base_url="https://api.github.com", assert_all_called=True) as mock:
        mock_expansion(mock, ["acme/site"])
        mock.post("/graphql").mock(return_value=RATE_LIMIT_EXCEEDED)
        result = run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert "refreshed source prod: 1 repo, 1 ref" in result.stderr
    assert _remote == ["https://github.com/acme/site.git"]


def test_source_refresh_partial_failure_exits_nonzero_and_saves_successes(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, prod("acme/gone", "acme/ok"), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"}, missing=("acme/gone",))

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {"acme/ok": "sha-ok"}, missing=("acme/gone",))
        result = run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "refreshed source prod:" in result.stderr
    assert "error: acme/gone: " in result.stderr
    assert "refresh completed with 1 repo failure; successes were saved" in result.output
    assert open_index(tmp_path).ref_scans("source:prod", "acme/ok", [("heads", "main")])


def test_source_refresh_all_failures_exits_nonzero_and_leaves_index_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, prod("acme/gone", "acme/ok"), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"}, missing=("acme/gone",))
    before = open_index(tmp_path).status("source:prod")
    assert before is not None

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {}, missing=("acme/gone", "acme/ok"))
        result = run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert "error: acme/gone: " in result.stderr
    assert "error: acme/ok: " in result.stderr
    assert "refresh failed for all 2 repos; index left unchanged" in result.output
    assert "unchanged repos skip Git fetch" not in result.stderr
    after = open_index(tmp_path).status("source:prod")
    assert after is not None
    assert after.scanned_at == before.scanned_at
    assert open_index(tmp_path).ref_scans("source:prod", "acme/ok", [("heads", "main")])


@pytest.mark.parametrize(
    ("files", "warning"),
    [
        (
            {REQS: TEMPLATED},
            f"warning: skipped acme/site@main {REQS}: could not parse dependency YAML",
        ),
        (
            {
                "requirements.yml": "roles:\n  - src: acme/base\n"
                "collections:\n  - community.general\n  - name: ansible.posix\n"
            },
            "warning: 2 collections in requirements files were ignored (only roles are graphed): "
            "ansible.posix, community.general",
        ),
        (
            {
                "requirements.yml": "roles:\n  - src: acme/base\n",
                "templates/a.j2": "{% if %}",
                "templates/b.j2": "{% for %}",
            },
            "warning: could not fully read 2 files for variables; "
            "`untaped ansible impact uses ROLE NAME` names those in the role's dependents",
        ),
    ],
)
def test_source_refresh_reports_dependency_file_warnings(
    tmp_path: Path, monkeypatch, files: dict[str, str], warning: str
) -> None:
    use_config(tmp_path, monkeypatch, prod(), token=True)
    monkeypatch.setattr(
        refresh, "GitSourceStore", type("Cache", (_SeedGitCache,), {"files": files})
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {"acme/site": "sha-site"})
        result = run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert warning in result.stderr


class _RefusingGitCache(_SeedGitCache):
    """Repo store stub refusing every ref name, as it refuses ``-wip``."""

    def refusal(self, ref: Any) -> str | None:
        return f"{ref.name!r} is not a branch or tag name"


def test_source_refresh_warns_about_a_ref_the_store_refuses(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, prod(), token=True)
    monkeypatch.setattr(refresh, "GitSourceStore", _RefusingGitCache)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {"acme/site": "sha-site"})
        result = run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert (
        "warning: skipped branch acme/site@main: 'main' is not a branch or tag name"
        in result.stderr
    )


def test_source_refresh_transient_probe_failure_prints_safe_rerun_hint(
    tmp_path: Path, monkeypatch
) -> None:
    """GitHub's 502 sends acme/flaky to ``ls-remote``, which cannot reach it either."""
    use_config(tmp_path, monkeypatch, prod("acme/ok"), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"})
    use_config(tmp_path, monkeypatch, prod("acme/ok", "acme/flaky"), token=True)
    get_settings.cache_clear()
    monkeypatch.setattr(refresh, "GitSourceStore", _NoFetchGitCache)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_expansion(mock, ["acme/ok", "acme/flaky"])
        mock.post("/graphql").mock(side_effect=_flaky_graphql)
        result = run("source", "refresh", "prod")

    assert result.exit_code == 5  # a transient probe failure: retry later
    assert "error: acme/flaky: ref probe failed: cannot reach" in result.stderr
    assert (
        "hint: rerun `untaped ansible source refresh prod`; unchanged repos skip Git fetch "
        "and dependency scan work"
    ) in result.stderr
    assert open_index(tmp_path).ref_scans("source:prod", "acme/ok", [("heads", "main")])


def test_source_refresh_failures_report_their_own_and_the_run_s_category(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, prod("acme/ok"), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"})
    use_config(tmp_path, monkeypatch, prod("acme/ok", "acme/flaky"), token=True)
    get_settings.cache_clear()
    monkeypatch.setattr(refresh, "GitSourceStore", _NoFetchGitCache)
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_expansion(mock, ["acme/ok", "acme/flaky"])
        mock.post("/graphql").mock(side_effect=_flaky_graphql)
        result = run("source", "refresh", "prod")

    errors = [
        line for line in map(json.loads, result.stderr.splitlines()) if line["level"] == "error"
    ]
    assert [(line.get("item"), line["category"]) for line in errors] == [
        ("acme/flaky", "unavailable"),
        (None, "unavailable"),
    ]
    assert (errors[-1]["exit_code"], errors[-1]["retryable"], result.exit_code) == (5, True, 5)


@pytest.mark.parametrize(
    ("repos", "graphql", "warning"),
    [
        (
            ("acme/a", "acme/b"),
            {"return_value": httpx.Response(502, text="Bad Gateway")},
            "warning: 2 repos fell back to git ls-remote, one network call per repo: "
            "github could not probe it (batch request failed: ",
        ),
        (
            ("acme/a", "acme/b"),
            {"return_value": RATE_LIMIT_EXCEEDED},
            "warning: 2 repos fell back to git ls-remote, one network call per repo: "
            "github's probe failed: ",
        ),
    ],
)
def test_source_refresh_reads_what_the_host_s_probe_could_not_with_ls_remote(
    tmp_path: Path,
    monkeypatch,
    repos: tuple[str, ...],
    graphql: dict[str, Any],
    warning: str,
) -> None:
    use_config(tmp_path, monkeypatch, prod(*repos), token=True)
    monkeypatch.setattr(refresh, "GitSourceStore", _SeedGitCache)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_expansion(mock, repos)
        mock.post("/graphql").mock(**graphql)
        result = run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert warning in result.stderr
    assert "error: acme/" not in result.stderr
    for repo in repos:
        assert open_index(tmp_path).ref_scans("source:prod", repo, [("heads", "main")])


def test_source_refresh_hard_failure_does_not_print_transient_rerun_hint(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, prod("acme/ok", "acme/bad"), token=True)
    failure = RepoFailure(repo="acme/bad", reason="git fetch failed: timeout")
    fake_refresh(
        monkeypatch,
        result=RefreshResult(source_key="", repos=2, refs=1, edges=0, failures=(failure,)),
    )

    result = run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert "error: acme/bad: git fetch failed: timeout" in result.stderr
    assert "unchanged repos skip Git fetch" not in result.stderr


def test_source_refresh_budget_pause_exits_nonzero_without_repo_failures(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, prod("acme/a", "acme/b"), token=True)
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    reset = datetime(2030, 1, 1, tzinfo=UTC)
    budget = HostBudget(
        plugin="github", host="github.com", remaining=200, reset_at=reset, exhausted=True
    )
    fake_refresh(
        monkeypatch,
        result=RefreshResult(
            source_key="",
            completed=False,
            pause_reason="the ref probe budget is spent on github.com (200 left)",
            repos=2,
            refs=1,
            edges=1,
            budgets=(budget,),
        ),
    )

    result = run("source", "refresh", "prod")

    assert result.exit_code == 5  # paused once the budget was spent: retry later
    assert result.stdout == ""
    [error] = [
        line for line in map(json.loads, result.stderr.splitlines()) if line["level"] == "error"
    ]
    assert error["message"] == "the ref probe budget is spent on github.com (200 left)"
    assert error["system"] == "github"
    assert error["hint"] == "rerun to resume; finished repos are skipped"
    assert error["details"]["retry_at"] == reset.isoformat()


def test_source_refresh_refused_probe_exits_once_without_per_repo_failures(
    tmp_path: Path, monkeypatch, _remote: list[str]
) -> None:
    use_config(tmp_path, monkeypatch, prod("acme/gone", "acme/ok"), token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_expansion(mock, ["acme/gone", "acme/ok"])
        mock.post("/graphql").mock(return_value=BAD_CREDENTIALS)
        result = run("source", "refresh", "prod")

    assert result.exit_code == 4  # a rejected token
    assert result.stdout == ""
    assert result.stderr.count("Bad credentials") == 1
    assert "error: acme/" not in result.stderr
    assert "refresh failed for all" not in result.stderr
    assert _remote == []


@pytest.mark.parametrize(("floor", "warned"), [(None, True), (100, False)])
def test_source_refresh_progress_and_spent_budget_warning_on_stderr(
    tmp_path: Path, monkeypatch, floor: int | None, warned: bool
) -> None:
    github = {} if floor is None else {"ref_probe": {"rate_limit_floor": floor}}
    use_config(tmp_path, monkeypatch, prod("acme/ok"), token=True, github=github)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"})

    with progress_clock() as clock, respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(
            mock,
            {"acme/ok": "sha-ok"},
            rate_limit_remaining=200,
            on_probe=lambda: clock.advance(2),  # a slow probe: past the quiet period
        )
        result = run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    shown = [d for d in result.diagnostics if isinstance(d, Progress)]
    assert [(d.phase, d.done, d.total, d.state) for d in shown] == [
        ("selecting repos", 1, 1, "running"),
        ("probing refs", 1, 1, "running"),
        ("fetching changes", 1, 1, "running"),
        (None, None, None, "done"),
    ]
    assert shown[2].message.endswith(" · fetching changes 1/1 · 0 changed (2s)")
    spent = "warning: the ref probe budget on github.com is spent: 200 left"
    assert (spent in result.stderr) is warned


def test_warn_probe_fallbacks_groups_repos_by_reason() -> None:
    reasons = ["busy", "busy", "gone", "slow", "late", "late"]
    result = RefreshResult(
        source_key="source:prod",
        repos=0,
        refs=0,
        edges=0,
        probe_fallbacks={f"acme/{index}": reason for index, reason in enumerate(reasons)},
    )

    warnings: list[str] = []
    refresh.warn_refresh(result, warnings.append)

    assert warnings == [
        "6 repos fell back to git ls-remote, one network call per repo: "
        "busy (2); late (2); gone (1); and 1 other reason"
    ]


def test_source_status_table_shows_its_default_columns(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:platform", make_edge())
    use_config(tmp_path, monkeypatch, PLATFORM)
    monkeypatch.setenv("COLUMNS", "200")

    header, _ = table(run("source", "status"))

    assert header == ["source", "state", "repos", "refs", "refreshed_at"]


def test_source_remove_clears_a_damaged_row_and_is_not_stopped_by_one(
    tmp_path: Path, monkeypatch
) -> None:
    # A row failing its model is what the damaged error tells the user to remove.
    sources = [
        github_source("good", repos=["acme/site"]),
        {"name": "bad", "orgs": ["acme"]},  # an older shape: no source, no selector
    ]
    use_config(tmp_path, monkeypatch, {"sources": sources})

    good = run("source", "remove", "good", "--yes", "--format", "json")
    assert good.exit_code == 0, good.output
    bad = run("source", "remove", "bad", "--yes", "--format", "json")
    assert bad.exit_code == 0, bad.output
    assert json.loads(bad.stdout)["action"] == "deleted"
    assert "sources" not in read_state()

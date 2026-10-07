"""Repo-wide pytest setup: the hermetic plugin plus checks only this repository runs."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from pkgutil import resolve_name
from typing import Any

import pytest
import release  # scripts/release.py (``pythonpath = ["scripts"]``)
from packaging.utils import canonicalize_name
from pydantic import BaseModel

from untaped import bootstrap, cli, repo_cache
from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate, discover_candidates
from untaped.git import GitResult
from untaped.records import table_columns_of

pytest_plugins = ["untaped.testing.plugin"]

_REPO_ROOT = Path(__file__).resolve().parent


def _first_party_distributions() -> frozenset[str]:
    """The workspace's own distribution names (Decision 8)."""
    return frozenset(release.packages(_REPO_ROOT))


@pytest.fixture(scope="session")
def first_party_candidates() -> tuple[ProviderCandidate, ...]:
    """Every installed first-party candidate, in name order."""
    names = _first_party_distributions()
    return tuple(
        sorted(
            (c for c in discover_candidates() if canonicalize_name(c.distribution) in names),
            key=lambda c: c.name,
        )
    )


@pytest.fixture(scope="session")
def first_party_specs(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> tuple[CapabilitySpec, ...]:
    """Every first-party spec, resolved from its discovered entry point, in name order."""
    return tuple(resolve_name(str(candidate.target))() for candidate in first_party_candidates)


@pytest.fixture(scope="session")
def broken_first_party_candidates() -> Callable[[], tuple[ProviderCandidate, ...]]:
    """A ``discover_candidates`` stand-in: first-party-looking candidates that do not resolve."""

    def candidates() -> tuple[ProviderCandidate, ...]:
        return tuple(
            ProviderCandidate(distribution="untaped", name=name, target=f"untaped_missing_{name}:p")
            for name in ("awx", "jira")
        )

    return candidates


@pytest.fixture(autouse=True)
def _no_writes_to_the_repo_root() -> Iterator[None]:
    """Fail the test that leaves a new file at the repository root.

    A subprocess that inherits the process cwd (the repo root under pytest)
    once committed junk such as ``core.sshCommand/HEAD``; write into
    ``tmp_path`` or ``monkeypatch.chdir`` there instead. Coverage data files
    are exempt: parallel workers write them there while other tests run.
    """
    before = set(os.listdir(_REPO_ROOT))
    yield
    leaked = sorted(
        name for name in set(os.listdir(_REPO_ROOT)) - before if not name.startswith(".coverage")
    )
    if leaked:
        pytest.fail(f"test left files at the repository root: {', '.join(leaked)}")


@pytest.fixture(autouse=True)
def _explicit_bare_repositories(
    _hermetic_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run every test the way hardened agent shells run untaped.

    GitHub Copilot CLI injects ``safe.bareRepository=explicit`` through
    ``GIT_CONFIG_COUNT``; git then refuses a bare repository it would find
    from ``cwd``, so a cache call that forgets ``--git-dir`` fails here
    first. A test that sets its own ``GIT_CONFIG_COUNT`` replaces this one.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.bareRepository")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "explicit")


@pytest.fixture
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the flattened config/profile stack at a temp config file."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


@pytest.fixture
def fresh_composition() -> Iterator[None]:
    """Start without a root composition and forget it (and its settings) after the test.

    For tests that compose the root, such as convention checks, so the
    capabilities they register do not leak into later tests.
    """
    bootstrap._clear_for_tests()
    yield
    bootstrap._clear_for_tests()


#: One ``RepoCache`` git call seen by ``spy_run_git``: subcommand, auth header, auth URL.
type GitCall = tuple[str, str | None, str | None]


@pytest.fixture
def spy_run_git(monkeypatch: pytest.MonkeyPatch) -> list[GitCall]:
    """Record each ``RepoCache`` git call's subcommand and auth; git still runs."""
    seen: list[GitCall] = []
    real = repo_cache.run_git

    def spy(args: Sequence[str], **kwargs: Any) -> GitResult:
        seen.append((args[0], kwargs.get("auth_header"), kwargs.get("auth_url")))
        return real(args, **kwargs)

    monkeypatch.setattr(repo_cache, "run_git", spy)
    return seen


@pytest.fixture
def rewrite_to(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., None]:
    """``rewrite_to(origin, *urls)`` points ``urls`` at the local ``origin`` (no network)."""

    def rewrite(origin: Path, *urls: str) -> None:
        config = tmp_path / "gitconfig"
        config.write_text(
            f'[url "{origin.as_uri()}"]\n' + "".join(f"\tinsteadOf = {url}\n" for url in urls)
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))

    return rewrite


@pytest.fixture(autouse=True)
def table_default_violations(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail the test whose command emits a wide record collection without default columns.

    Records with more than four fields (``error`` aside) need default table
    columns: their type's ``table_columns`` or the command's ``table_columns=``
    (``docs/reference/conventions.md#output-records``).
    """
    found: list[str] = []
    emit_with = cli.emit_with

    def checked(records: Any, **kwargs: Any) -> None:
        if not isinstance(records, BaseModel | Mapping) and not kwargs.get("table_columns"):
            found.extend(_lacking_default_columns(records))
        emit_with(records, **kwargs)

    monkeypatch.setattr(cli, "emit_with", checked)
    # Root commands bound the name at import.
    monkeypatch.setattr("untaped.management._render.emit_with", checked)
    yield found
    if found:
        pytest.fail(
            "record collections emitted without default table columns (declare "
            "`table_columns` on the record; see docs/reference/conventions.md#output-records):\n"
            + "\n".join(f"  {line}" for line in sorted(set(found)))
        )


def _lacking_default_columns(records: Sequence[object]) -> Iterator[str]:
    for model in dict.fromkeys(type(item) for item in records if isinstance(item, BaseModel)):
        fields = {*model.model_fields, *model.model_computed_fields} - {"error"}
        root = model.__module__.split(".")[0]
        own = root == "untaped" or root.startswith("untaped_")
        if own and len(fields) > 4 and not table_columns_of(model):
            yield f"{model.__module__}.{model.__qualname__}::no-default-columns"

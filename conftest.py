"""Repo-wide pytest setup: the hermetic plugin plus checks only this repository runs."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from pkgutil import resolve_name

import pytest
import release  # scripts/release.py (``pythonpath = ["scripts"]``)
from packaging.utils import canonicalize_name

from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate, PluginSpec, discover_candidates
from untaped.runtime.run import entered, start_run
from untaped.runtime.sink import drop_tasks
from untaped.sdk._run import Run

pytest_plugins = ["untaped.sdk.testing.plugin"]

_REPO_ROOT = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _no_leaked_progress_task() -> Iterator[None]:
    """Fail a test that leaves a progress task open, then drop it so the next test starts clean."""
    yield
    leaked = drop_tasks()
    assert leaked == 0, "a progress task was left open (a `progress()` block not exited)"


def _first_party_distributions() -> frozenset[str]:
    """The workspace's own distribution names (Decision 8)."""
    return frozenset(release.packages(_REPO_ROOT))


@pytest.fixture(scope="session")
def first_party_candidates() -> tuple[PluginCandidate, ...]:
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
    first_party_candidates: tuple[PluginCandidate, ...],
) -> tuple[PluginSpec, ...]:
    """Every first-party 11.x spec, resolved from its entry point, in name order.

    A plugin moved to the 12.0 shape is a ``Plugin`` class instead, left out
    here: its own ``test_plugin.py`` checks it, surface included.
    """
    resolved = (resolve_name(str(candidate.target)) for candidate in first_party_candidates)
    return tuple(spec for spec in resolved if isinstance(spec, PluginSpec))


@pytest.fixture(scope="session")
def broken_first_party_candidates() -> Callable[[], tuple[PluginCandidate, ...]]:
    """A ``discover_candidates`` stand-in: first-party-looking candidates that do not resolve."""

    def candidates() -> tuple[PluginCandidate, ...]:
        return tuple(
            PluginCandidate(distribution="untaped", name=name, target=f"untaped_missing_{name}:p")
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
    """Run every test with the git config GitHub Copilot CLI injects.

    Copilot sets these through ``GIT_CONFIG_COUNT``. ``safe.bareRepository=explicit``
    makes git refuse a bare repository it would find from ``cwd``, so a cache
    call that forgets ``--git-dir`` fails here first. A test that sets its own
    ``GIT_CONFIG_COUNT`` replaces this config.
    """
    injected = {
        "safe.bareRepository": "explicit",
        "credential.interactive": "never",
        "core.fsmonitor": "false",
    }
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(injected)))
    for index, (key, value) in enumerate(injected.items()):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)


@pytest.fixture
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the flattened config/profile stack at a temp config file."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    return cfg


@pytest.fixture
def home_run(request: pytest.FixtureRequest) -> Iterator[Run]:
    """A run over the test's home, as every invocation opens one, for code a test calls directly.

    Built after ``_isolated_config`` when the test uses it, so the run's home
    is the one the test points ``UNTAPED_HOME`` at. It composes nothing: the
    code under test reads the home (core's ``refs``), not a plugin's blocks.
    """
    if "_isolated_config" in request.fixturenames:
        request.getfixturevalue("_isolated_config")
    with entered(start_run()) as run:
        yield run


@pytest.fixture
def fresh_composition() -> Iterator[None]:
    """Start without a root composition and forget it (and its settings) after the test.

    For tests that compose the root, such as convention checks, so the
    plugins they register do not leak into later tests.
    """
    bootstrap._clear_for_tests()
    yield
    bootstrap._clear_for_tests()


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

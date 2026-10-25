"""Core's layout: three roots named by who reads them, and tests that mirror the code.

``untaped/sdk/`` is what a plugin may import, ``untaped/core/`` is untaped's
own plugin and ``untaped/runtime/`` is how a run happens. A plugin learns
the SDK without learning the run, so the SDK imports neither of the other
two. Today's SDK modules still reach into the runtime for the settings, the
sink and the composition; :data:`STILL_IMPORTS_THE_RUN` names each of them,
and an entry goes when that module's code stops needing the run.

A reader with a question finds the file without a search: the test of
``untaped/sdk/storage/cache.py`` is ``tests/untaped/sdk/storage/test_cache.py``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from repo.support import PACKAGES

CORE = PACKAGES / "untaped"
SRC = CORE / "src"
TESTS = CORE / "tests" / "untaped"
#: Folders of ``tests/untaped/`` with no source twin, named for what they hold.
NO_TWIN = frozenset({"scenarios", "snapshots"})

#: SDK modules that still import ``untaped.runtime`` or ``untaped.core``. The
#: list only shrinks: a module whose code no longer needs the run leaves it.
STILL_IMPORTS_THE_RUN = frozenset(
    {
        "untaped.sdk._app_context",
        "untaped.sdk._batch",
        "untaped.sdk._cli",
        "untaped.sdk._exports",
        "untaped.sdk._schema",
        "untaped.sdk._stdin",
        "untaped.sdk._ui",
        "untaped.sdk.contracts._cache",
        "untaped.sdk.contracts._declare",
        "untaped.sdk.contracts._gather",
        "untaped.sdk.contracts._providing",
        "untaped.sdk.contracts._select",
        "untaped.sdk.diagnostic",
        "untaped.sdk.errors",
        "untaped.sdk.health._setup_status",
        "untaped.sdk.health.deprecated_keys",
        "untaped.sdk.health.doctor",
        "untaped.sdk.marks",
        "untaped.sdk.network._credential_store",
        "untaped.sdk.network.auth",
        "untaped.sdk.network.credentials",
        "untaped.sdk.network.http",
        "untaped.sdk.plugin_records",
        "untaped.sdk.projects",
        "untaped.sdk.prompt.prompts",
        "untaped.sdk.storage._digest",
        "untaped.sdk.storage._refs",
        "untaped.sdk.storage.cache",
        "untaped.sdk.storage.holds",
        "untaped.sdk.storage.state",
        "untaped.sdk.storage.store",
        "untaped.sdk.testing",
        "untaped.sdk.testing.contracts",
        # the scripted terminal drives screens on a theme (runtime.theme), as
        # untaped.sdk.testing's 11.x backend did before it moved here
        "untaped.sdk.testing.fixtures",
        "untaped.sdk.testing.guards",
        # composes an install (runtime.bootstrap, runtime.registry) and runs its commands
        # through the mount (runtime.mount.run_built), as the root does
        "untaped.sdk.testing.install",
        "untaped.sdk.testing.plugin",
        "untaped.sdk.testing.screens",
    }
)


#: The top of ``untaped/``: the three roots, the screen kit and the entry point.
TOP = frozenset(
    {
        "__init__.py",
        "__main__.py",
        "core",
        "py.typed",
        "runtime",
        "screen",
        "sdk",
    }
)


def test_core_has_three_roots() -> None:
    top = {
        p.name
        for p in (SRC / "untaped").iterdir()
        if p.is_file() or any(p.rglob("*.py"))  # a folder a move left only caches in is gone
    }
    assert top == TOP, (
        f"untaped/ holds {sorted(top - TOP)}: a module lives in sdk/ (what a plugin "
        "may import), core/ (untaped's own plugin) or runtime/ (how a run happens)"
    )


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path) -> set[str]:
    """Every module ``path`` imports, at any depth, ``TYPE_CHECKING`` blocks included.

    ``from a import b`` counts as both ``a`` and ``a.b``: ``b`` may be a module.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _imports_the_run(path: Path) -> bool:
    return any(
        name == root or name.startswith(root + ".")
        for name in _imports(path)
        for root in ("untaped.runtime", "untaped.core")
    )


def test_the_sdk_imports_neither_the_runtime_nor_core() -> None:
    importing = {
        _module_name(path)
        for path in sorted((SRC / "untaped" / "sdk").rglob("*.py"))
        if _imports_the_run(path)
    }
    new = sorted(importing - STILL_IMPORTS_THE_RUN)
    assert not new, (
        f"{new} import untaped.runtime or untaped.core: the SDK is what a plugin may "
        "import, so it reaches per-run state without importing the run"
    )
    gone = sorted(STILL_IMPORTS_THE_RUN - importing)
    assert not gone, f"{gone} no longer import the run: drop them from STILL_IMPORTS_THE_RUN"


def test_importing_the_sdk_loads_nothing_else() -> None:
    code = (
        "import sys, untaped.sdk\nprint(sorted(m for m in sys.modules if m.startswith('untaped.')))"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "['untaped.sdk']"


def _names(package: Path) -> set[str]:
    """The modules and subpackages of ``package``, a leading ``_`` dropped."""
    return {
        each.stem.lstrip("_")
        for each in package.iterdir()
        if (each.suffix == ".py" and each.stem != "__init__") or (each / "__init__.py").exists()
    }


def _mirrors(test: Path) -> bool:
    folder = test.parent.relative_to(TESTS)
    if folder.parts and folder.parts[0] in NO_TWIN:
        return True
    twin = SRC / "untaped" / folder
    if not (twin / "__init__.py").exists():
        return False
    subject = test.stem.removeprefix("test_")
    return any(
        subject == name or subject.startswith(name + "_") for name in {*_names(twin), twin.name}
    )


def test_core_tests_mirror_the_source() -> None:
    misplaced = [
        test.relative_to(CORE).as_posix()
        for test in sorted(TESTS.rglob("test_*.py"))
        if not _mirrors(test)
    ]
    assert not misplaced, (
        f"{misplaced}: a test of untaped/<path>/<module>.py is "
        "tests/untaped/<path>/test_<module>[_<topic>].py, so its path says what it tests"
    )


def test_core_tests_live_under_the_mirror() -> None:
    outside = [
        test.relative_to(CORE).as_posix()
        for test in sorted((CORE / "tests").rglob("test_*.py"))
        if not test.is_relative_to(TESTS)
    ]
    assert not outside, f"{outside}: core's tests live under tests/untaped/, mirroring the source"


def test_the_mirror_has_no_packages() -> None:
    """An ``__init__.py`` under ``tests/untaped/`` would shadow the real ``untaped`` package."""
    inits = sorted(p.relative_to(CORE).as_posix() for p in TESTS.rglob("__init__.py"))
    assert not inits, f"{inits}: tests/untaped/ is on pytest's path; keep it free of packages"

"""Guards that heavy optional dependencies stay lazily loaded.

``prompt_toolkit`` (interactive prompts) and ``httpx`` (HTTP clients) are only
needed by tools that actually prompt or make requests. Importing the public API
— or rendering output — must not drag either into the interpreter, so commands
that only ``list``/``get``/pipe pay nothing for them.

It also holds ``untaped --help`` and ``untaped --version`` to a module-count
budget: the number of modules a run imports is deterministic for a given lock
file, unlike wall-clock time, so a startup regression fails here instead of
flaking on a slow CI runner.

These checks run in a **clean subprocess** (via ``sys.executable``): the pytest
process itself has long since imported both libraries, so an in-process
``sys.modules`` assertion would be meaningless.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _loaded_heavy_modules(snippet: str, env: dict[str, str] | None = None) -> str:
    """Run ``snippet`` in a fresh interpreter; return its stdout (the verdict)."""
    code = (
        "import sys\n"
        f"{snippet}\n"
        "heavy = sorted(m for m in ('prompt_toolkit', 'httpx') if m in sys.modules)\n"
        "print(','.join(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_public_api_import_does_not_load_prompt_toolkit_or_httpx() -> None:
    assert _loaded_heavy_modules("import untaped.sdk") == ""


def test_sdk_import_loads_no_screen_module() -> None:
    """The screen names are exported lazily: touching one loads core, never the adapter."""
    snippet = (
        "import untaped.sdk\n"
        "eager = [m for m in sys.modules if m.startswith('untaped.screen')]\n"
        "assert not eager, eager\n"
        "assert 'Screen' in dir(untaped.sdk)\n"
        "untaped.sdk.Screen\n"
        "assert 'untaped.screen.core' in sys.modules\n"
        "assert 'untaped.screen.terminal' not in sys.modules\n"
    )
    assert _loaded_heavy_modules(snippet) == ""


def test_screen_components_load_without_the_adapter_or_prompt_toolkit() -> None:
    """Components are plain Rich and core code: loading them never loads a terminal library."""
    snippet = (
        "import untaped.sdk\n"
        "untaped.sdk.TextInput\n"
        "untaped.sdk.Field\n"
        "assert 'untaped.screen.components.inputs' in sys.modules\n"
        "assert 'untaped.screen.components.fields' in sys.modules\n"
        "assert 'untaped.screen.terminal' not in sys.modules\n"
    )
    assert _loaded_heavy_modules(snippet) == ""
    every = (
        "import untaped.screen.components.inputs, untaped.screen.components.choices, "
        "untaped.screen.components.tabs, untaped.screen.components.buttons, "
        "untaped.screen.components.fields\n"
    )
    assert "prompt_toolkit" not in _loaded_heavy_modules(every)


def test_screen_core_does_not_load_prompt_toolkit() -> None:
    snippet = (
        "import untaped.screen.core, untaped.screen.runtime, untaped.screen.color\n"
        "import untaped.sdk.testing.screens\n"
    )
    assert "prompt_toolkit" not in _loaded_heavy_modules(snippet)


def test_building_a_terminal_and_rendering_does_not_load_prompt_toolkit() -> None:
    snippet = (
        "from untaped.sdk._ui import Ui, collection\n"
        "ctx = Ui()\n"
        "collection(ctx, [{'id': 1, 'name': 'alpha'}], fmt='json')\n"
        "collection(ctx, [], fmt='table', empty=True)\n"
    )
    assert "prompt_toolkit" not in _loaded_heavy_modules(snippet)


_DISPATCH_PROBE = (
    "import contextlib, io\n"
    "from untaped.runtime.bootstrap import main\n"
    "with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):\n"
    "    main({argv!r})\n"
    "code = sorted(\n"
    "    m for m in sys.modules\n"
    "    if m.startswith('untaped_')\n"
    "    and m.partition('.')[2].split('.')[0] in ('commands', 'application', 'api')\n"
    ")\n"
    "print(' '.join(code))\n"
)


def _plugin_code_modules(argv: list[str], home: Path) -> tuple[set[str], set[str]]:
    """Plugin command, application and API modules ``untaped <argv>`` imports: fresh, then kept.

    The first start in ``home`` composes afresh (importing each plugin class);
    the second reads the declaration cache, as a user's starts do. httpx is
    never imported.
    """
    env = {**os.environ, "UNTAPED_HOME": str(home)}
    probe = _DISPATCH_PROBE.format(argv=argv)
    starts = []
    for _start in ("fresh", "kept"):
        # The verdict's heavy-module line follows the code line; empty lines strip away.
        code_line, _, heavy_line = _loaded_heavy_modules(probe, env).partition("\n")
        assert "httpx" not in heavy_line.split(",")
        starts.append(set(code_line.split()))
    return starts[0], starts[1]


def test_a_kept_root_help_imports_no_plugin_code(tmp_path: Path) -> None:
    fresh, kept = _plugin_code_modules(["--help"], tmp_path)
    assert fresh  # the probe names modules a start can import
    assert kept == set()


_STARTUP_PROBE = (
    "import contextlib, io\n"
    "baseline = set(sys.modules)\n"
    "from untaped.runtime.bootstrap import main\n"
    "with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):\n"
    "    main({argv!r})\n"
    "new = set(sys.modules) - baseline\n"
    "print(len(new), sum(1 for m in new if m.partition('.')[0].startswith('untaped')))\n"
)

#: Modules imported on top of interpreter startup (a coverage run preloads
#: some, so it only lowers these). Measured 2026-10-10 on Python 3.14.6 with
#: eight plugins: 127 ``untaped.*`` for ``--help`` and ``--version``
#: (the git plugin adds two: its package and settings model). The stderr
#: sink (``untaped.output``: four modules for the two it replaced) adds two
#: more: 126 against 124 on its base, measured 2026-10-11 in a container.
#: Progress lines and JSON (#537) replace ``untaped.progress`` with
#: ``untaped.output.progress``: 127 on A2a and on its base, 2026-10-12.
#: Messages on ``ctx.diag`` with the user's theme (A2b): 127, unchanged.
#: The freshness SDK (#537 B1) adds ``untaped.cache`` (the call state moved
#: there from ``untaped.contracts``) and ``untaped.upstream`` (read by
#: ``untaped.http``): 129, 2026-10-12. The answer cache on ``Cache.serve()``
#: (#537 B3) adds none: 129 on it and on its base, 2026-10-13. The lint
#: plugin (#640) adds three (its package, settings and doctor row) and the SDK's
#: ``untaped.plugin_records`` one: 133, 2026-10-14. Core's record models in
#: ``untaped.management.records`` (#607) add one: 131 against 130 on its
#: base, 2026-10-16. The budget stays a few above the measure (six when it
#: was set) to absorb dependency and patch-release drift; a new built-in
#: plugin adds a few ``untaped.*`` modules (its SPEC and settings). Setting
#: marks (``untaped.setting_marks``, and ``untaped.setup_status`` read by
#: ``doctor`` and ``auth``) add two: the measure is 140 against 138 on their
#: base, 2026-10-20, so the budget goes from 139 to 143 to keep three spare.
#: The SDK's tree renderer (``untaped.tree``, #789) adds one: 144 against 143
#: on its base, 2026-10-21, so the budget goes to 147. Core's three roots and
#: the SDK's groups add their package modules (``untaped.core``,
#: ``untaped.runtime``, four groups), the SDK's name table
#: (``untaped.sdk._exports``) and the screen kit's package (the picker moved
#: into it), less ``untaped.output`` and ``untaped.plugins``: 150 against 144,
#: 2026-10-23, so the budget goes to 153, and the total (706 with them) to 710.
#: The 12.0 plugin classes (``untaped.sdk.plugin``, ``untaped.sdk.fields`` and
#: ``untaped.sdk._block``), which every plugin's entry point imports from 12.0
#: on, add three: 156 against 153 on their base, 2026-10-23, so the budget goes
#: to 159. Core's declaration cache, read at every start so a 12.0 plugin
#: class isn't imported (``untaped.core.plugin``, ``untaped.runtime.declaration``,
#: ``untaped.sdk.storage.installed_code`` and the data file), adds four: 161
#: against 157 on its base, 2026-10-24, so the budget goes to 163, and the
#: total (715 with them) to 718. Core composing its own class first, at every
#: start, adds the mount (``untaped.runtime.mount``) and, on a start that
#: composes afresh as this one does, composition (``untaped.runtime.composition``):
#: 164 against 162 on its base (a start reading the declaration cache: 162, as
#: ``untaped.core.plugin`` is no longer read), 2026-10-24, so the budget goes
#: to 167, and the total (717 with them) to 720. Setup on core's class adds,
#: on a start that composes afresh, its two command modules
#: (``untaped.core.commands.register``, until then imported when it ran, and
#: ``untaped.core.commands.setup_prune``): 168 against 166 on its base, while a
#: start reading the declaration cache drops twelve (setup's 11.x app and what it
#: imported), 2026-10-25, so the budget goes to 169, and the total (721 with
#: them) to 723.
#: From the jira move (12.0) on, the budget holds a start reading the
#: declaration cache, the start a user has but the first after an install
#: change: a start composing afresh imports each 12.0 plugin class, as core's
#: declaration cache design accepts. Core reads a 12.0 plugin's credential
#: names from its declaration, so the warm start imports jira's settings and
#: nothing more: 138 and 691, 2026-10-26, so the budget goes to 141 and the
#: total to 694. With dotfiles and git moved: 137 and 690, 2026-10-27. With
#: every plugin moved and ansible's settings no longer importing github's
#: application code: 107 and 649, 2026-10-29, so the budget goes to 110 and
#: the total to 652.
_TOTAL_BUDGET = 652
_UNTAPED_BUDGET = 110
#: A start composing afresh (the first after an install change) imports each
#: 12.0 plugin class, as the contracts and declaration cache designs accept,
#: so it has no budget here: it is a figure the plugin moves watch.


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_startup_stays_within_its_module_budget(flag: str, tmp_path: Path) -> None:
    env = {**os.environ, "UNTAPED_HOME": str(tmp_path)}
    probe = _STARTUP_PROBE.format(argv=[flag])
    # the first start composes afresh and keeps the declaration; the second reads it
    for start in ("fresh", "kept"):
        counts, _, heavy = _loaded_heavy_modules(probe, env).partition("\n")
        assert heavy == "", start
    total, own = map(int, counts.split())
    assert own <= _UNTAPED_BUDGET, f"a kept untaped {flag} imported {own} untaped.* modules"
    assert total <= _TOTAL_BUDGET, f"a kept untaped {flag} imported {total} modules"


def test_a_typed_cache_declared_at_startup_does_not_load_its_model(tmp_path: Path) -> None:
    """``model=`` takes a function for a model whose import would cost startup; ``--help`` too.

    The data file loads at every start (core reads its declaration cache), so
    only the model is held here. A start composing afresh imports each 12.0
    plugin class (awx's imports this model), so the second start is the one held.
    """
    env = {**os.environ, "UNTAPED_HOME": str(tmp_path)}
    _loaded_heavy_modules(_DISPATCH_PROBE.format(argv=["--help"]), env)
    snippet = (
        "from datetime import timedelta\n"
        "from untaped.sdk import Cache\n"
        "def model():\n"
        "    import untaped_awx.domain.lint.resources\n"
        "    raise AssertionError('resolved at declaration')\n"
        "Cache(label='x', max_age=timedelta(hours=1), model=model)\n"
        + _DISPATCH_PROBE.format(argv=["--help"])
        + "assert 'untaped_awx.domain.lint.resources' not in sys.modules\n"
    )
    assert "httpx" not in _loaded_heavy_modules(snippet, env)


def test_contracts_load_only_when_asked(tmp_path: Path) -> None:
    """``untaped.sdk.contracts`` is a group of its own: the SDK and a kept ``--help`` never load it.

    A start composing afresh imports each 12.0 plugin class, and a class
    offering a contract imports the group, so the second start is the one held.
    """
    env = {**os.environ, "UNTAPED_HOME": str(tmp_path)}
    _loaded_heavy_modules(_DISPATCH_PROBE.format(argv=["--help"]), env)
    snippet = (
        "import untaped.sdk\n"
        "assert 'untaped.sdk.contracts' not in sys.modules\n"
        + _DISPATCH_PROBE.format(argv=["--help"])
        + "assert not [m for m in sys.modules if m.startswith('untaped.sdk.contracts')]\n"
    )
    assert "httpx" not in _loaded_heavy_modules(snippet, env)


def test_testing_helpers_load_contracts_only_when_called() -> None:
    """``untaped.sdk.testing`` exports compose_with and assert_fills without loading contracts."""
    snippet = (
        "import untaped.sdk.testing\n"
        "untaped.sdk.testing.compose_with, untaped.sdk.testing.assert_fills\n"
        "assert not [m for m in sys.modules if m.startswith('untaped.sdk.contracts')]\n"
    )
    assert "prompt_toolkit" not in _loaded_heavy_modules(snippet)


_LISTED_PROBE = (
    "import os, sys\n"
    "from untaped.runtime.bootstrap import build_root_app\n"
    "from untaped.sdk import marks\n"
    "build_root_app()\n"
    "before = set(sys.modules)\n"
    "list(marks._listed())\n"
    "new = sorted(set(sys.modules) - before)\n"
    "core = ('untaped.core.commands', 'untaped.core.plugin')\n"
    "print(' '.join(m for m in new if m.startswith(core)))\n"
)


def test_asking_who_lists_a_behaviour_never_imports_core_s_commands(tmp_path: Path) -> None:
    """A warm start's behaviour listings (read when a plugin's behaviour first warns)
    import none of core's commands: the root's behaviours are the SDK's to list."""
    env = {**os.environ, "UNTAPED_HOME": str(tmp_path)}
    for _ in range(2):  # the second start reads the kept declaration
        proc = subprocess.run(
            [sys.executable, "-c", _LISTED_PROBE],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""

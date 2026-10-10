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

import subprocess
import sys

import pytest


def _loaded_heavy_modules(snippet: str) -> str:
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
        "import untaped.testing.screens\n"
    )
    assert "prompt_toolkit" not in _loaded_heavy_modules(snippet)


def test_building_and_rendering_a_ui_context_does_not_load_prompt_toolkit() -> None:
    snippet = (
        "from untaped.ui import UiContext\n"
        "ctx = UiContext()\n"
        "ctx.collection([{'id': 1, 'name': 'alpha'}], fmt='json')\n"
        "ctx.message('info', 'rendered')\n"
    )
    assert "prompt_toolkit" not in _loaded_heavy_modules(snippet)


_DISPATCH_PROBE = (
    "import contextlib, io\n"
    "from untaped.bootstrap import main\n"
    "with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):\n"
    "    main({argv!r})\n"
    "cli = sorted(\n"
    "    m for m in sys.modules\n"
    "    if m.startswith('untaped_') and 'cli' in m.split('.')\n"
    ")\n"
    "print(' '.join(cli))\n"
)


def _plugin_cli_modules(argv: list[str]) -> set[str]:
    """Plugin CLI modules a clean ``untaped <argv>`` run imports (httpx never)."""
    # The verdict's heavy-module line follows the CLI line; empty lines strip away.
    cli_line, _, heavy_line = _loaded_heavy_modules(_DISPATCH_PROBE.format(argv=argv)).partition(
        "\n"
    )
    assert "httpx" not in heavy_line.split(",")
    return set(cli_line.split())


def test_root_help_imports_no_plugin_cli() -> None:
    assert _plugin_cli_modules(["--help"]) == set()


def test_plugin_help_imports_only_its_own_cli() -> None:
    loaded = _plugin_cli_modules(["workspace", "--help"])
    assert loaded
    owners = {module.split(".")[0].removeprefix("untaped_") for module in loaded}
    assert owners == {"workspace"}


_STARTUP_PROBE = (
    "import contextlib, io\n"
    "baseline = set(sys.modules)\n"
    "from untaped.bootstrap import main\n"
    "with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):\n"
    "    main({argv!r})\n"
    "new = set(sys.modules) - baseline\n"
    "print(len(new), sum(1 for m in new if m.partition('.')[0].startswith('untaped')))\n"
)

#: Modules imported on top of interpreter startup (a coverage run preloads
#: some, so it only lowers these). Measured 2026-10-10 on Python 3.14.6 with
#: eight plugins: 127 ``untaped.*`` for ``--help`` and ``--version``
#: (the git plugin adds two: its package and settings model).
#: The budget sits six above that measurement, the same headroom the
#: previous one had, to absorb dependency and patch-release drift; a new
#: built-in plugin adds a few ``untaped.*`` modules (its SPEC and settings).
_TOTAL_BUDGET = 700
_UNTAPED_BUDGET = 133


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_startup_stays_within_its_module_budget(flag: str) -> None:
    counts, _, heavy = _loaded_heavy_modules(_STARTUP_PROBE.format(argv=[flag])).partition("\n")
    assert heavy == ""
    total, own = map(int, counts.split())
    assert own <= _UNTAPED_BUDGET, f"untaped {flag} imported {own} untaped.* modules"
    assert total <= _TOTAL_BUDGET, f"untaped {flag} imported {total} modules"


def test_contracts_load_only_when_asked() -> None:
    """``untaped.contracts`` is a module root of its own: the SDK and ``--help`` never load it."""
    snippet = (
        "import untaped.sdk\n"
        "assert 'untaped.contracts' not in sys.modules\n"
        + _DISPATCH_PROBE.format(argv=["--help"])
        + "assert not [m for m in sys.modules if m.startswith('untaped.contracts')]\n"
    )
    assert "httpx" not in _loaded_heavy_modules(snippet)

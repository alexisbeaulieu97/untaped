"""Guards that heavy optional dependencies stay lazily loaded.

``prompt_toolkit`` (interactive prompts) and ``httpx`` (HTTP clients) are only
needed by tools that actually prompt or make requests. Importing the public API
— or rendering output — must not drag either into the interpreter, so commands
that only ``list``/``get``/pipe pay nothing for them.

These checks run in a **clean subprocess** (via ``sys.executable``): the pytest
process itself has long since imported both libraries, so an in-process
``sys.modules`` assertion would be meaningless.
"""

from __future__ import annotations

import subprocess
import sys


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
    assert _loaded_heavy_modules("import untaped.api") == ""


def test_building_and_rendering_a_ui_context_does_not_load_prompt_toolkit() -> None:
    snippet = (
        "from untaped.ui import UiContext\n"
        "ctx = UiContext()\n"
        "ctx.collection([{'id': 1, 'name': 'alpha'}], fmt='json')\n"
        "ctx.message('info', 'rendered')\n"
    )
    assert "prompt_toolkit" not in _loaded_heavy_modules(snippet)

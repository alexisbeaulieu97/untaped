"""``untaped.sdk`` is the only SDK surface; the root re-exports nothing."""

import subprocess
import sys

import untaped


def test_package_root_has_no_star_export() -> None:
    """The root is not an SDK surface: no ``__all__`` for ``import *``."""
    assert "__all__" not in vars(untaped)


def test_package_import_loads_no_sdk_modules() -> None:
    code = (
        "import sys, untaped\nprint(sorted(m for m in sys.modules if m.startswith('untaped.')))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "[]"

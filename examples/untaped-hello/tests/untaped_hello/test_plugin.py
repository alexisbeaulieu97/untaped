"""The example plugin as a whole, tested the way any third-party plugin would be.

``install(Hello)`` composes the plugin into the test's own home; ``.run``
runs a built command, ``.cli`` a command line, and both return what it did.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable

from untaped_hello.plugin import Hello

from untaped.sdk.testing import Install, assert_surface, check
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_hello_passes_lint() -> None:
    assert_clean("hello")


def test_hello_passes_its_plugin_check(install: Installer) -> None:
    check(install(Hello))


def test_hello_keeps_its_surface() -> None:
    assert_surface(Hello)  # snapshots/surface.txt beside this file


def test_hello_loads_lazily() -> None:
    """A fresh interpreter after the first start: rendering the root help imports no command.

    The first start composes the plugin class, which imports its commands,
    and core keeps what it composed; every start after that reads it.
    """
    code = (
        "import sys; from untaped.sdk.testing import invoke_root; "
        "r = invoke_root(['--help']); "
        "print(r.exit_code, 'hello' in r.stdout, "
        "any(m.startswith('untaped_hello.commands') for m in sys.modules))"
    )
    first = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert first.stdout.split()[:2] == ["0", "True"]
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["0", "True", "False"]


def test_hello_is_listed_ready(install: Installer) -> None:
    listed = install(Hello).cli("plugin list --format json")
    assert {r["name"]: r["status"] for r in json.loads(listed.stdout)}["hello"] == "ready"


def test_hello_s_own_kind_has_a_schema(install: Installer) -> None:
    ran = install(Hello).cli("plugin schema hello.default_greeting")
    assert ran.exit == 0, ran.stderr
    assert "setting" in json.loads(ran.stdout)["properties"]

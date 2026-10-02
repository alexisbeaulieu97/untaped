"""The example plugin, tested the way any third-party plugin would be."""

from __future__ import annotations

import json
import subprocess
import sys

from untaped.testing import check_conventions, invoke_root


def test_hello_follows_the_conventions() -> None:
    check_conventions("hello")


def test_hello_loads_lazily() -> None:
    """A fresh interpreter: rendering the root help never imports the CLI."""
    code = (
        "import sys; from untaped.testing import invoke_root; "
        "r = invoke_root(['--help']); "
        "print(r.exit_code, 'hello' in r.stdout, 'untaped_hello.cli' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["0", "True", "False"]


def test_hello_is_listed_ready() -> None:
    rows = json.loads(invoke_root(["capabilities", "--format", "json"]).stdout)
    assert [(r["name"], r["status"]) for r in rows] == [("hello", "ready")]


def test_hello_greets_with_its_setting() -> None:
    result = invoke_root(["hello", "greet"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello from untaped-hello"


def test_hello_greets_with_a_configured_greeting() -> None:
    assert invoke_root(["config", "set", "hello.greeting", "hi"]).exit_code == 0
    result = invoke_root(["hello", "greet"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "hi"

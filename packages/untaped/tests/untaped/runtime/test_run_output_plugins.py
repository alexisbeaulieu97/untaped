"""A deprecated SDK name a plugin looks up warns naming the plugin, and is recorded.

Each test runs a real ``python`` with no ``-W`` flag, so its filters are
Python's startup defaults, and drives ``untaped`` as the console script does
(:func:`untaped.runtime.bootstrap.run_root`). The SDK root gains, for the run,
a deprecated function ``human_size`` and an old name ``size_words`` (both of
``size_text``, going in 14.0). ``acme`` imports ``human_size`` at module level
and never calls it, and its ``report`` command looks ``size_words`` up;
``beta`` imports ``human_size`` too. Their settings models live in modules
of their own, as composition loads a kept declaration's settings models.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import yaml

_PLUGIN = '''
    """The ``{name}`` plugin class."""

    from untaped.sdk import Command, Plugin, human_size  # noqa: F401 - never called

    from untaped_{name}.settings import {cls}Settings


    class Report(Command):
        """Report."""

        def run(self) -> None:
            from untaped.sdk import size_words

            size_words(3)


    class {cls}(Plugin):
        """{cls}."""

        settings = {cls}Settings
        commands = (Report,)
    '''

_SETTINGS = """
from pydantic import BaseModel, ConfigDict


class {cls}Settings(BaseModel):
    model_config = ConfigDict(frozen=True)
"""

_RUN = """
import sys, warnings
import untaped.sdk
import untaped.sdk._exports as exports
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import UntapedDeprecationWarning, deprecated, size_text
from untaped.sdk.marks import old_names

@deprecated(removed_in=14, replacement=size_text)
@warnings.deprecated(
    "human_size is deprecated and will be removed in 14.0; use `size_text`",
    category=UntapedDeprecationWarning,
)
def human_size(size: int) -> str:
    return size_text(size)

human_size.__module__ = "untaped.sdk._exports"
exports.human_size = human_size
exports.__all__.append("human_size")
exports.OLD_NAMES = old_names(size_words=deprecated(removed_in=14, replacement=size_text))

found = [
    PluginCandidate(
        distribution=f"untaped-{name}",
        name=name,
        target=f"untaped_{name}.plugin:{name.capitalize()}",
        distribution_version="1.0",
        requires_dist=("untaped>=1",),
    )
    for name in ("acme", "beta")
]
try:
    bootstrap.run_root(["acme", "report"], candidates=found)
except SystemExit as exc:
    assert not exc.code, exc.code
assert "human_size" not in vars(untaped.sdk), "a deprecated name is never kept"
"""


def _write(tmp_path: Path) -> None:
    for name in ("acme", "beta"):
        package = tmp_path / f"untaped_{name}"
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("")
        body = dedent(_PLUGIN).lstrip().format(name=name, cls=name.capitalize())
        (package / "plugin.py").write_text(body, encoding="utf-8")
        settings = _SETTINGS.format(cls=name.capitalize())
        (package / "settings.py").write_text(settings, encoding="utf-8")
        info = tmp_path / f"untaped_{name}-1.0.dist-info"
        info.mkdir(exist_ok=True)
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: untaped-{name}\nVersion: 1.0\n"
            "Requires-Dist: untaped>=1\n"
        )


def _run(tmp_path: Path) -> list[dict[str, Any]]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONWARNINGS", "PYTHONDEVMODE")
    }
    env.update(
        PYTHONPATH=os.pathsep.join([str(tmp_path), *sys.path]),
        UNTAPED_DIAGNOSTICS="json",
        UNTAPED_HOME=str(tmp_path / "home"),
    )
    done = subprocess.run(
        [sys.executable, "-c", _RUN],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    lines = [json.loads(line) for line in done.stderr.splitlines() if line.startswith("{")]
    return [line for line in lines if line["kind"] == "untaped.deprecation"]


def _rows(tmp_path: Path) -> list[tuple[str, str, str]]:
    state = tmp_path / "home" / "plugins" / "untaped" / "state.yml"
    rows = (yaml.safe_load(state.read_text()) or {}).get("run_uses", [])
    return sorted((row["plugin"], row["id"], row["command"]) for row in rows)


_HUMAN = "`untaped.sdk.human_size`, which is deprecated and will be removed in 14.0"


def test_each_plugin_looking_a_deprecated_name_up_is_named_once_and_recorded(
    tmp_path: Path,
) -> None:
    _write(tmp_path)
    # the first run composes: both plugins' modules are imported while it does
    lines = _run(tmp_path)
    assert sorted((line["plugin"], line["id"], line["message"]) for line in lines) == [
        (
            "acme",
            "sdk untaped.sdk.human_size",
            f"untaped-acme 1.0 uses {_HUMAN}; use `size_text`",
        ),
        (
            "acme",
            "sdk untaped.sdk.size_words",
            "untaped-acme 1.0 uses `untaped.sdk.size_words`, which is deprecated and will be "
            "removed in 14.0; use `size_text`",
        ),
        (
            "beta",
            "sdk untaped.sdk.human_size",
            f"untaped-beta 1.0 uses {_HUMAN}; use `size_text`",
        ),
    ]
    assert _rows(tmp_path) == [
        ("acme", "sdk untaped.sdk.human_size", "untaped acme report"),
        ("acme", "sdk untaped.sdk.size_words", "untaped acme report"),
        ("beta", "sdk untaped.sdk.human_size", "untaped acme report"),
    ]
    # the next run reads the kept declaration: only acme's plugin module loads, to dispatch
    again = _run(tmp_path)
    assert sorted((line["plugin"], line["id"]) for line in again) == [
        ("acme", "sdk untaped.sdk.human_size"),
        ("acme", "sdk untaped.sdk.size_words"),
    ]

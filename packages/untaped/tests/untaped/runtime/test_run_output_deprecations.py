"""A deprecated Python API used while a command runs warns the user, under Python's own filters.

Python hides a ``DeprecationWarning`` raised outside ``__main__``; each test
runs a real ``python`` with no ``-W`` flag, so its filters are Python's
startup defaults, not pytest's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

_LAMP = {
    "untaped_lamp/__init__.py": "",
    "untaped_lamp/_bulbs.py": '''
        """Where the API is defined; plugins import it from ``api``."""

        import warnings
        from typing import Annotated

        from untaped.sdk import UntapedDeprecationWarning, deprecated, deprecated_parameters


        def bright() -> str:
            return "bright"


        @deprecated(removed_in=3, replacement=bright)
        @warnings.deprecated(
            "dim is deprecated and will be removed in untaped-lamp 3.0; use `bright`",
            category=UntapedDeprecationWarning,
        )
        def dim() -> str:
            return "dim"


        @deprecated_parameters
        def glow(*, fade: Annotated[bool, deprecated(removed_in=3)] = False) -> str:
            return "glow"
        ''',
    "untaped_lamp/api.py": '''
        """What a plugin may import from lamp."""

        from untaped_lamp._bulbs import bright, dim, glow

        __all__ = ["bright", "dim", "glow"]
        ''',
    "untaped_lamp/plugin.py": '''
        """The ``lamp`` plugin, whose command uses each deprecated kind twice."""

        from pydantic import BaseModel

        from untaped.sdk import Command, DeprecatedBehaviour, Plugin
        from untaped_lamp.api import dim, glow


        class LampSettings(BaseModel):
            """Nothing to configure."""


        class Shine(Command):
            """Shine."""

            def run(self) -> None:
                for _ in range(2):
                    dim()
                    glow(fade=True)
                    Lamp.old_way.warn("shining through a shade", stacklevel=1)


        class Lamp(Plugin):
            """Light."""

            settings = LampSettings
            commands = (Shine,)
            old_way = DeprecatedBehaviour(removed_in=3, replacement="bright")
        ''',
}

_RUN = """
import json, sys, warnings
from untaped.runtime import bootstrap
from untaped.sdk.testing import invoke_cli, plugin_candidate
from untaped_lamp.plugin import Lamp

before = list(warnings.filters)
root = bootstrap.build_root_app(candidates=[plugin_candidate(Lamp)])
result = invoke_cli(root.meta, ["lamp", "shine"])
assert result.exit_code == 0, result.output
assert warnings.filters == before, warnings.filters
sys.stdout.write(result.stderr)
"""


def _run(tmp_path: Path, *flags: str) -> list[dict[str, object]]:
    for name, body in _LAMP.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")
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
        [sys.executable, *flags, "-c", _RUN],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    return [json.loads(line) for line in done.stdout.splitlines()]


def test_each_deprecated_kind_warns_once_under_python_s_own_filters(tmp_path: Path) -> None:
    lines = _run(tmp_path)
    assert [
        (line["kind"], line["id"], line["removed_in"], line["distribution"], line["replacement"])
        for line in lines
    ] == [
        ("untaped.deprecation", "sdk untaped_lamp.api.dim", 3, "untaped-lamp", "bright"),
        ("untaped.deprecation", "sdk untaped_lamp.api.glow(fade=)", 3, "untaped-lamp", None),
        ("untaped.deprecation", "behaviour lamp.old-way", 3, "untaped-lamp", "bright"),
    ]
    assert lines[0]["message"] == (
        "dim is deprecated and will be removed in untaped-lamp 3.0; use `bright`"
    )


@pytest.mark.parametrize(
    ("flag", "shown"),
    [("ignore::DeprecationWarning", 0), ("always::DeprecationWarning", 3)],
)
def test_a_filter_someone_set_decides_instead(tmp_path: Path, flag: str, shown: int) -> None:
    lines = _run(tmp_path, "-W", flag)
    assert len([line for line in lines if line["kind"] == "untaped.deprecation"]) == shown

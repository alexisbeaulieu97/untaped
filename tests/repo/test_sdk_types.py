"""What a type checker sees of ``untaped.sdk``: typos are errors, names keep their types.

The SDK and its groups resolve their names lazily (a module-level
``__getattr__``), which must stay hidden from the type checker: visible, it
types every other attribute as ``object`` and a plugin's misspelled import
passes mypy.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from repo.support import REPO_ROOT

SNIPPET = """\
import untaped.sdk
from untaped.sdk import Terminal, plural
from untaped.sdk import Termnal  # line 3: a typo in an import
from untaped.sdk import emmit  # line 4: a typo in a helper

untaped.sdk.Termnal  # line 6: a typo as an attribute
from untaped.sdk.storage import Cach  # line 7: a typo in a group


def count(ui: Terminal) -> str:
    return plural(2, "repo")


reveal_type(plural)  # line 14
"""


def _mypy(tmp_path: Path, snippet: str = SNIPPET) -> tuple[int, str]:
    source = tmp_path / "plugin.py"
    source.write_text(snippet, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    return proc.returncode, proc.stdout


def test_a_typo_in_an_sdk_name_is_a_type_error_and_names_keep_their_types(
    tmp_path: Path,
) -> None:
    code, output = _mypy(tmp_path)
    errors = [line for line in output.splitlines() if ": error:" in line]
    assert code == 1, output
    assert sorted(line.split(":")[1] for line in errors) == ["3", "4", "6", "7"], output
    assert all("attr-defined" in line for line in errors), output
    # plural is the real function, not ``object``.
    assert "def (" in output.split("Revealed type is", 1)[1], output


TYPED = """\
from collections.abc import Mapping
from datetime import timedelta

from pydantic import BaseModel

from untaped.sdk.storage import (
    NO_PROGRESS, Cache, DataFile, DataRead, Filled, Found, ProgressHandle
)


class Snapshot(BaseModel):
    items: list[str]


def _model() -> type[Snapshot]:
    return Snapshot


TYPED = Cache(label="x", max_age=timedelta(hours=1), model=_model)
PLAIN = Cache(label="x", max_age=timedelta(hours=1))


def fill(key: str, task: ProgressHandle) -> Filled[Snapshot]:
    return Filled(value=Snapshot(items=[key]))


def plain_fill(key: str, task: ProgressHandle) -> Filled:
    return Filled()


def snapshot(url: str) -> Snapshot:
    served = TYPED.serve({url: url}, refresh=None, fill=fill, partition="token")
    return served.value[url]


def names() -> list[str]:
    return PLAIN.serve({"a": "a"}, refresh=None, fill=plain_fill, read=lambda: ["a"]).value


def read(path: object) -> Snapshot | None:
    found: DataRead[Snapshot] = DataFile(path, Snapshot).read()  # type: ignore[arg-type]
    match found:
        case Found(value=value):
            return value
    return None


reveal_type(TYPED)  # line 48
reveal_type(TYPED.serve({}, refresh=None, fill=fill).value)  # line 49
reveal_type(TYPED.cached("a"))  # line 50
reveal_type(PLAIN.cached("a", lambda: 1))  # line 51
"""


def test_a_fill_returning_a_bare_filled_is_refused_by_a_typed_cache(tmp_path: Path) -> None:
    """A bare ``Filled`` is ``Filled[None]``: mypy refuses it as a ``Cache[M]`` fill."""
    _code, output = _mypy(tmp_path, TYPED + "TYPED.serve({}, refresh=None, fill=plain_fill)\n")
    errors = [line for line in output.splitlines() if ": error:" in line]
    assert len(errors) == 1 and "arg-type" in errors[0], output
    assert f":{TYPED.count(chr(10)) + 1}:" in errors[0], output  # the appended line


def test_a_typed_cache_types_its_values_under_strict_mypy(tmp_path: Path) -> None:
    _code, output = _mypy(tmp_path, TYPED)
    assert ": error:" not in output, output
    revealed = {
        int(line.split(":")[1]): line.split("Revealed type is ", 1)[1]
        for line in output.splitlines()
        if "Revealed type is" in line
    }
    assert revealed[48] == '"untaped.sdk.storage.cache.Cache[plugin.Snapshot]"', output
    assert revealed[49] == '"typing.Mapping[str, plugin.Snapshot]"', output
    assert "Served[plugin.Snapshot" in revealed[50], output
    assert "Served[int" in revealed[51], output


def test_the_output_policy_stays_out_of_the_sdk() -> None:
    """A plugin that could read the policy would branch on JSON around the sink."""
    import untaped.sdk

    assert "OutputPolicy" not in untaped.sdk.__all__
    assert not hasattr(untaped.sdk, "OutputPolicy")

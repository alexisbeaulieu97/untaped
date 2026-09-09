"""Public configured-editor process behavior, including literal argument handling."""

import shlex
import sys
from pathlib import Path

import pytest

import untaped.capability_api as api


def test_editor_prefers_visual_and_never_interprets_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "editor script.py"
    script.write_text("import pathlib, sys\npathlib.Path(sys.argv[-1]).write_text(sys.argv[1])\n")
    target = tmp_path / "edited file.yml"
    literal = "literal $(touch never) ; value"
    monkeypatch.setenv("VISUAL", shlex.join([sys.executable, str(script), literal]))
    monkeypatch.setenv("EDITOR", "does-not-exist")
    assert hasattr(api, "run_editor")
    api.run_editor(target)
    assert target.read_text() == literal


@pytest.mark.parametrize("editor", ["", "does-not-exist", "'unclosed", "python -c 'exit(7)'"])
def test_editor_failures_are_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editor: str
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", editor)
    assert hasattr(api, "run_editor")
    with pytest.raises(api.ConfigError):
        api.run_editor(tmp_path / "file")


def test_editor_explicit_argv_and_streams(tmp_path: Path) -> None:
    target = tmp_path / "file"
    assert hasattr(api, "run_editor")
    with (tmp_path / "terminal").open("w+") as terminal:
        api.run_editor(
            target,
            argv=[
                sys.executable,
                "-c",
                "import sys; print('editor output'); print('error', file=sys.stderr)",
            ],
            stdin=terminal,
            stdout=terminal,
            stderr=terminal,
        )
        terminal.seek(0)
        assert "editor output" in terminal.read()

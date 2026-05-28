"""Tests for the ``scripts/sync_plugins.py`` regenerator.

The script keeps five blocks in ``pyproject.toml`` (Import Linter +
mypy) in sync with the single ``[tool.untaped].plugin_modules`` list. Tests
pin two things: (a) the regen output is correct for a synthetic
fixture, and (b) the round trip on the real, committed
``pyproject.toml`` is byte-identical — so adopting the script does
not silently rewrite the file.

The regenerated output is asserted by parsing it back with ``tomllib``;
re-implementing an ad-hoc array parser here would mirror the bug
surface of the rewriter under test.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sync_plugins.py"


def _load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("sync_plugins", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sync_plugins() -> types.ModuleType:
    return _load_module()


SAMPLE_PYPROJECT = """\
[project]
name = "untaped"
requires-python = ">=3.14"

[tool.untaped]
plugin_modules = [
    "untaped_alpha",
    "untaped_beta",
]

[tool.importlinter]
root_packages = [
    "untaped",
    "untaped_alpha",
    "untaped_beta",
]
exclude_type_checking_imports = true
# load-bearing comment that must survive regen
[[tool.importlinter.contracts]]
name = "Sibling plugins are mutually independent"
type = "independence"
modules = [
    "untaped_alpha",
    "untaped_beta",
]

[[tool.importlinter.contracts]]
name = "Per-plugin layers (cli > application | infrastructure > domain)"
type = "layers"
containers = [
    "untaped_alpha",
    "untaped_beta",
]
layers = [
    "cli",
    "application | infrastructure",
    "domain",
]

[[tool.importlinter.contracts]]
name = "untaped core does not statically import plugins"
type = "forbidden"
source_modules = ["untaped"]
forbidden_modules = [
    "untaped_alpha",
    "untaped_beta",
]

[tool.mypy]
strict = true
packages = [
    "untaped",
    "untaped_alpha",
    "untaped_beta",
]
"""


def _regenerated_lists(text: str) -> dict[str, list[str]]:
    """Parse regen output back with tomllib and return the 5 target lists."""
    data = tomllib.loads(text)
    importlinter = data["tool"]["importlinter"]
    contracts_by_name = {c["name"]: c for c in importlinter["contracts"]}
    return {
        "root_packages": importlinter["root_packages"],
        "modules": contracts_by_name["Sibling plugins are mutually independent"]["modules"],
        "containers": contracts_by_name[
            "Per-plugin layers (cli > application | infrastructure > domain)"
        ]["containers"],
        "forbidden_modules": contracts_by_name["untaped core does not statically import plugins"][
            "forbidden_modules"
        ],
        "packages": data["tool"]["mypy"]["packages"],
    }


def test_regen_idempotent_when_in_sync(sync_plugins: types.ModuleType) -> None:
    """Running regen on already-synced text returns it unchanged."""
    out = sync_plugins.regen(SAMPLE_PYPROJECT, ["untaped_alpha", "untaped_beta"])
    assert out == SAMPLE_PYPROJECT


def test_regen_propagates_new_plugin_to_all_five_blocks(
    sync_plugins: types.ModuleType,
) -> None:
    """Adding a plugin to the source list updates exactly the five target blocks."""
    plugin_modules = ["untaped_alpha", "untaped_beta", "untaped_gamma"]
    lists = _regenerated_lists(sync_plugins.regen(SAMPLE_PYPROJECT, plugin_modules))

    assert lists["root_packages"] == ["untaped", *plugin_modules]
    assert lists["modules"] == plugin_modules
    assert lists["containers"] == plugin_modules
    assert lists["forbidden_modules"] == plugin_modules
    assert lists["packages"] == ["untaped", *plugin_modules]

    # Untouched blocks survive: layers and load-bearing comments.
    out = sync_plugins.regen(SAMPLE_PYPROJECT, plugin_modules)
    assert "load-bearing comment that must survive regen" in out
    assert "application | infrastructure" in out
    assert 'source_modules = ["untaped"]' in out


def test_regen_drops_removed_plugin(sync_plugins: types.ModuleType) -> None:
    """A plugin dropped from the input list disappears from every target block."""
    lists = _regenerated_lists(sync_plugins.regen(SAMPLE_PYPROJECT, ["untaped_alpha"]))
    for items in lists.values():
        assert "untaped_beta" not in items


def test_regen_preserves_root_tokens(sync_plugins: types.ModuleType) -> None:
    """`untaped` is prepended; it is not in `[tool.untaped].plugin_modules`."""
    lists = _regenerated_lists(sync_plugins.regen(SAMPLE_PYPROJECT, ["untaped_alpha"]))
    assert lists["root_packages"][0] == "untaped"
    assert lists["packages"][0] == "untaped"
    assert lists["forbidden_modules"] == ["untaped_alpha"]


def test_real_pyproject_round_trips_byte_identical(sync_plugins: types.ModuleType) -> None:
    """Running regen on the committed ``pyproject.toml`` produces no diff.

    Pins the zero-behaviour-change requirement: adopting the script
    cannot silently rewrite the working tree.
    """
    original = (REPO_ROOT / "pyproject.toml").read_text()
    plugin_modules = sync_plugins.read_plugin_modules(original)
    assert plugin_modules, "expected `[tool.untaped].plugin_modules` to be populated"
    assert sync_plugins.regen(original, plugin_modules) == original


def test_read_plugin_modules_requires_source_list(sync_plugins: types.ModuleType) -> None:
    """Missing `[tool.untaped].plugin_modules` raises rather than silently defaulting to []."""
    with pytest.raises(KeyError):
        sync_plugins.read_plugin_modules('[project]\nname = "x"\n')


def test_main_reports_misconfiguration_without_traceback(
    sync_plugins: types.ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing source list exits 1 with a one-line message (not a traceback)."""
    broken = tmp_path / "pyproject.toml"
    broken.write_text('[project]\nname = "x"\n')
    rc = sync_plugins.main(["--check", "--path", str(broken)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "sync-plugins:" in err
    assert "tool.untaped" in err


def test_main_reports_structural_drift_without_traceback(
    sync_plugins: types.ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Anchor failures (target block restructured) also exit 1 with a one-liner.

    Pins the friendly-error path for ``regen`` failures too — not only
    ``read_plugin_modules``. Reformatting a target block out from under the
    anchor must not show a Python traceback in pre-commit output.
    """
    broken = tmp_path / "pyproject.toml"
    # Valid source list but the `[tool.mypy] packages = [...]` block has
    # been removed → `_replace_list_after` can't anchor.
    broken.write_text(
        '[tool.untaped]\nplugin_modules = ["untaped_alpha"]\n\n'
        '[tool.importlinter]\nroot_packages = [\n    "untaped",\n'
        '    "untaped_alpha",\n]\n'
    )
    rc = sync_plugins.main(["--check", "--path", str(broken)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "sync-plugins:" in err
    assert "Traceback" not in err


def _drift_pyproject(text: str) -> str:
    """Add a new plugin to the source list only — target blocks lag behind."""
    return text.replace(
        '    "untaped_beta",\n]\n\n[tool.importlinter]',
        '    "untaped_beta",\n    "untaped_gamma",\n]\n\n[tool.importlinter]',
    )


def test_check_exits_nonzero_on_drift(
    sync_plugins: types.ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """In-process ``main(["--check", ...])`` returns 1 when the file is drifted."""
    drifted = tmp_path / "pyproject.toml"
    drifted.write_text(_drift_pyproject(SAMPLE_PYPROJECT))
    rc = sync_plugins.main(["--check", "--path", str(drifted)])
    assert rc == 1
    assert "drift" in capsys.readouterr().err.lower()


def test_write_rewrites_drifted_file(sync_plugins: types.ModuleType, tmp_path: Path) -> None:
    """``main(["--write", ...])`` makes the file canonical."""
    drifted = tmp_path / "pyproject.toml"
    drifted.write_text(_drift_pyproject(SAMPLE_PYPROJECT))
    assert sync_plugins.main(["--write", "--path", str(drifted)]) == 0
    after = drifted.read_text()
    assert sync_plugins.regen(after, sync_plugins.read_plugin_modules(after)) == after
    for items in _regenerated_lists(after).values():
        assert "untaped_gamma" in items


def test_cli_entrypoint_smoke() -> None:
    """One subprocess test for the shebang / ``__main__`` plumbing."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()

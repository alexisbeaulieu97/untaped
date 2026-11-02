"""Which composed plugin's code a module is, as run time attributes it."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from untaped.runtime import plugin_frames
from untaped.runtime.declaration import Declaration, PluginDecl


def _result(*plugins: tuple[str, str], lockstep: str = "", root: str = "") -> Any:
    decls = [
        PluginDecl(
            name=name,
            target=target,
            package=target.split(".")[0],
            summary="",
            doc="",
            settings="",
            lockstep=name == lockstep,
            root=name == root,
        )
        for name, target in plugins
    ]
    return SimpleNamespace(
        mount=SimpleNamespace(declaration=Declaration(plugins=tuple(decls))),
        candidates=[
            SimpleNamespace(
                name=each.name,
                distribution=f"untaped-{each.name}",
                distribution_version="2.0",
                requires_dist=("untaped>=12.0",),
            )
            for each in decls
        ],
    )


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    plugin_frames.forget()
    yield
    plugin_frames.forget()


def test_the_plugin_is_the_one_whose_package_is_deepest() -> None:
    """Two plugins in one namespace package, and one nested in another's package."""
    result = _result(
        ("alpha", "nsp.alpha:Alpha"),
        ("beta", "nsp.beta:Beta"),
        ("host", "untaped_host:Host"),
        ("guest", "untaped_host.plugins.guest:Guest"),
    )
    beta = plugin_frames.plugin_at("nsp.beta.report", result)
    assert beta == plugin_frames.PluginAt("beta", "untaped-beta", "2.0", ("untaped>=12.0",))
    guest = plugin_frames.plugin_at("untaped_host.plugins.guest.words", result)
    assert guest is not None
    assert guest.name == "guest"
    host = plugin_frames.plugin_at("untaped_host.cli", result)
    assert host is not None
    assert host.name == "host"


def test_core_a_lockstep_plugin_and_other_code_own_nothing() -> None:
    result = _result(
        ("untaped", "untaped.core.plugin:Core"),
        ("git", "untaped_git:Git"),
        ("acme", "untaped_acme:Acme"),
        lockstep="git",
        root="untaped",
    )
    assert plugin_frames.plugin_at("untaped.sdk", result) is None
    assert plugin_frames.plugin_at("untaped_git.api", result) is None
    assert plugin_frames.plugin_at("requests", result) is None
    assert plugin_frames.plugin_at("untaped_acme", result) is not None


def test_a_file_is_the_module_imported_from_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = types.ModuleType("nsp.beta.report")
    loaded.__file__ = str(tmp_path / "report.py")
    monkeypatch.setitem(sys.modules, "nsp.beta.report", loaded)
    assert plugin_frames.module_of(str(tmp_path / "report.py")) == "nsp.beta.report"
    assert plugin_frames.module_of(str(tmp_path / "elsewhere.py")) is None

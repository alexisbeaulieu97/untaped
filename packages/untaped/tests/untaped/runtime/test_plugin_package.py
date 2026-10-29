"""Where a candidate's code is: its package, read from its entry point's target."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from untaped.runtime.plugin_package import candidate_package


@pytest.mark.parametrize(
    ("target", "package"),
    [
        ("untaped.core:Plugin", "untaped.core"),  # the module is the package
        ("untaped.core.plugin:Core", "untaped.core"),  # a class in a leaf module
        ("untaped_nowhere:Plugin", "untaped_nowhere"),  # not installed: the name as written
        ("untaped_nowhere.plugin:X", "untaped_nowhere.plugin"),
        ("no-colon", None),
    ],
)
def test_a_targets_package_is_its_module_or_the_package_holding_it(
    target: str, package: str | None
) -> None:
    assert candidate_package(target) == package


def test_a_package_that_fails_to_import_is_found_without_importing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "acme_broken"
    folder.mkdir()
    (folder / "__init__.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (folder / "plugin.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert candidate_package("acme_broken.plugin:Acme") == "acme_broken"
    assert "acme_broken" not in sys.modules


def test_a_namespace_subpackage_and_a_compiled_leaf_name_their_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "acme_ns" / "sub").mkdir(parents=True)
    (tmp_path / "acme_ns" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "acme_ns" / "sub" / "plugin.cpython-314-x86_64-linux-gnu.so").write_bytes(b"")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert candidate_package("acme_ns.sub.plugin:Acme") == "acme_ns.sub"
    assert candidate_package("acme_ns.sub:Plugin") == "acme_ns.sub"

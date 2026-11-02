"""The lookup: what each recorded import finds in the installed modules."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import pytest

from untaped.runtime.plugin_imports import ImportRecord
from untaped.runtime.plugin_uses import Cascade, Missing, Owner, look_up, owner_of

_OTHER = Owner("untaped-other", "other")
_OWNERS = {"untaped": Owner("untaped", "untaped", core=True), "untaped_other": _OTHER}


@pytest.fixture
def api() -> Iterator[types.ModuleType]:
    """``untaped_other.api``, made in memory: ``kept``, and ``lazy`` through ``__getattr__``."""
    package = types.ModuleType("untaped_other")
    package.__path__ = []
    module = types.ModuleType("untaped_other.api")
    module.kept = 1  # type: ignore[attr-defined]

    def __getattr__(name: str) -> object:
        if name == "lazy":
            return 2
        if name == "heavy":
            import untaped_optional_dependency  # type: ignore[import-not-found]  # noqa: F401

        raise AttributeError(name)

    module.__getattr__ = __getattr__  # type: ignore[method-assign]
    sys.modules["untaped_other"], sys.modules["untaped_other.api"] = package, module
    yield module
    del sys.modules["untaped_other"], sys.modules["untaped_other.api"]


def _record(name: str, *, guarded: bool = False) -> ImportRecord:
    return ImportRecord(
        module="untaped_other.api", name=name, file="acme/x.py", line=1, guarded=guarded
    )


def test_a_name_only_its_module_getattr_gives_is_there(api: types.ModuleType) -> None:
    verdict = look_up([_record("kept"), _record("lazy")], _OWNERS)
    assert (verdict.missing, verdict.cascade, verdict.uses) == ([], None, [])


def test_a_name_neither_there_nor_guarded_is_missing(api: types.ModuleType) -> None:
    verdict = look_up([_record("gone"), _record("also", guarded=True)], _OWNERS)
    assert verdict.missing == [Missing(_record("gone"), _OTHER)]


def test_a_name_whose_lookup_raises_disables_the_plugin_with_its_owner(
    api: types.ModuleType,
) -> None:
    verdict = look_up([_record("heavy")], _OWNERS)
    assert verdict.cascade == Cascade(
        _record("heavy"),
        _OTHER,
        error="ModuleNotFoundError: No module named 'untaped_optional_dependency'",
    )
    assert look_up([_record("heavy", guarded=True)], _OWNERS).cascade is None


def test_a_guarded_import_from_a_disabled_plugin_changes_nothing() -> None:
    disabled = ["untaped-other"]
    assert look_up([_record("kept", guarded=True)], _OWNERS, disabled).cascade is None
    assert look_up([_record("kept")], _OWNERS, disabled).cascade == Cascade(_record("kept"), _OTHER)


def test_the_longest_package_owns_a_module() -> None:
    nested = Owner("untaped-acme", "acme")
    owners = {**_OWNERS, "untaped.plugins.acme": nested}
    assert owner_of("untaped.plugins.acme.api", owners) is nested
    assert owner_of("untaped.sdk", owners) is _OWNERS["untaped"]
    assert owner_of("requests", owners) is None

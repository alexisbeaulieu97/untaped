"""The SDK's groups: each lists its names, loads each from its home on first use, and stays cheap.

A plugin spells a group's name from the group (``from untaped.sdk.storage
import Cache``). Until the removals (12.0 delivery step 6) the root still
exports the same objects for the plugins that have not moved, so each group
name is the root's object, never a copy.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from untaped.sdk._lazy import lazy_names

#: The groups a plugin imports from, besides the root (primitives, section 8).
GROUPS = (
    "untaped.sdk.git",
    "untaped.sdk.health",
    "untaped.sdk.network",
    "untaped.sdk.projects",
    "untaped.sdk.prompt",
    "untaped.sdk.storage",
)


@pytest.mark.parametrize("path", GROUPS)
def test_every_listed_name_resolves(path: str) -> None:
    group = importlib.import_module(path)
    for name in group.__all__:
        assert getattr(group, name) is not None, f"{path}.{name}"
    assert set(group.__all__) <= set(dir(group))


@pytest.mark.parametrize("path", GROUPS)
def test_a_group_name_is_the_roots_object(path: str) -> None:
    import untaped.sdk as root

    group = importlib.import_module(path)
    for name in group.__all__:
        assert name in root.__all__, f"{path}.{name}"
        assert getattr(group, name) is getattr(root, name), f"{path}.{name}"


def test_an_unknown_name_is_an_attribute_error() -> None:
    import untaped.sdk.storage as storage

    with pytest.raises(AttributeError, match="has no attribute 'Nope'"):
        _ = storage.Nope  # type: ignore[attr-defined]


def test_a_table_that_disagrees_with_all_is_refused() -> None:
    namespace: dict[str, object] = {"__name__": "untaped.sdk.example", "__all__": ["A", "B"]}
    with pytest.raises(ImportError, match="disagree"):
        lazy_names(namespace, {"one": ("A",)})
    with pytest.raises(ImportError, match="disagree"):
        lazy_names(namespace, {"one": ("A", "B"), "two": ("B",)})


def test_a_resolved_name_is_kept_in_the_group() -> None:
    from untaped.sdk.words import plural

    namespace: dict[str, object] = {"__name__": "untaped.sdk.example", "__all__": ["plural"]}
    getattr_, dir_ = lazy_names(namespace, {"untaped.sdk.words": ("plural",)})
    assert "plural" in dir_()
    assert getattr_("plural") is plural
    assert namespace["plural"] is plural


#: The groups that are packages (``git`` and ``projects`` are one module each).
PACKAGES = (
    "untaped.sdk.health",
    "untaped.sdk.network",
    "untaped.sdk.prompt",
    "untaped.sdk.storage",
)


@pytest.mark.parametrize("path", PACKAGES)
def test_importing_a_group_loads_none_of_its_members(path: str) -> None:
    code = (
        "import sys, importlib\n"
        f"importlib.import_module({path!r})\n"
        f"print(sorted(m for m in sys.modules if m.startswith({path + '.'!r})))\n"
    )
    shown = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout
    assert shown.strip() == "[]"

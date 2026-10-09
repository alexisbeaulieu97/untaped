"""``docs/versioning.md`` names exactly the experimental things the marks say.

The bullet list under "## Experimental" is kept by hand, because it also names
record kinds, file formats and environment variables no mark knows. This
checks it rather than generating it: every experimental capability, group or
command is a bullet, every bullet that starts with a command path is marked, and
every experimental setting is named in backticks somewhere in the section (a
capability's own settings are covered by its bullet).
"""

from __future__ import annotations

import inspect
import re
from types import ModuleType

from repo.support import REPO_ROOT
from untaped import bootstrap, sdk, testing
from untaped.capabilities.registry import ProviderCandidate
from untaped.stability import Experimental, function_mark, marks

_BULLET = re.compile(r"^- `([^`]+)`:", re.MULTILINE)


def experimental_section(text: str) -> str:
    """The body of the ``## Experimental`` section of ``docs/versioning.md``."""
    match = re.search(r"^## Experimental\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match is not None, "docs/versioning.md has no '## Experimental' section"
    return match.group(1)


def bullet_paths(text: str) -> set[str]:
    """The command paths the bullets of ``## Experimental`` start with (``awx test``)."""
    return set(_BULLET.findall(experimental_section(text)))


def unnamed_settings(text: str, keys: set[str]) -> list[str]:
    """The settings of ``keys`` that ``## Experimental`` does not name in backticks."""
    section = experimental_section(text)
    return sorted(key for key in keys if f"`{key}`" not in section)


def versioning_problems(text: str, marked: set[str]) -> list[str]:
    """What is missing from, and what is stray on, the ``## Experimental`` bullets."""
    listed = bullet_paths(text)
    return [
        *(f"{path}: experimental but has no bullet" for path in sorted(marked - listed)),
        *(
            f"{path}: has a bullet but is not marked experimental"
            for path in sorted(listed - marked)
        ),
    ]


def experimental_objects(module: ModuleType) -> set[str]:
    """The names in ``module.__all__`` that carry ``@experimental``, and ``Class.attr`` ones.

    A public attribute of an exported class counts when it is marked itself
    (``UiContext.run``); an unmarked class with marked members is not listed.
    """
    found: set[str] = set()
    for name in module.__all__:
        obj = getattr(module, name)
        if isinstance(function_mark(obj), Experimental):
            found.add(name)
        if inspect.isclass(obj):
            found.update(
                f"{name}.{attr}"
                for attr, member in vars(obj).items()
                if not attr.startswith("_") and isinstance(function_mark(member), Experimental)
            )
    return found


def unlisted_objects(text: str, names: set[str], *, prefix: str = "") -> list[str]:
    """The ``names`` that ``## Experimental`` does not put in backticks.

    ``prefix`` (``untaped.testing.``) is also accepted in front of a name, for
    objects that live outside ``untaped.sdk``.
    """
    section = experimental_section(text)
    return sorted(
        name
        for name in names
        if f"`{name}`" not in section and not (prefix and f"`{prefix}{name}`" in section)
    )


def _bullets(*paths: str) -> str:
    return (
        "## Experimental\n\n" + "".join(f"- `{path}`: things.\n" for path in paths) + "\n## Next\n"
    )


def test_a_missing_bullet_and_a_stray_bullet_each_fail() -> None:
    assert versioning_problems(_bullets("awx test"), {"awx test", "dotfiles"}) == [
        "dotfiles: experimental but has no bullet"
    ]
    assert versioning_problems(_bullets("awx test", "gone"), {"awx test"}) == [
        "gone: has a bullet but is not marked experimental"
    ]
    assert versioning_problems(_bullets("awx test"), {"awx test"}) == []


def test_only_the_experimental_section_counts() -> None:
    text = "## Before\n\n- `other`: not here.\n\n" + _bullets("awx test")

    assert bullet_paths(text) == {"awx test"}


def test_every_experimental_mark_of_the_repo_is_on_the_versioning_page(
    first_party_candidates: tuple[ProviderCandidate, ...], fresh_composition: None
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    marked = {
        mark.where
        for mark in marks(root, bootstrap.composition(), resolve=True)
        if mark.target != "setting" and isinstance(mark.stability, Experimental)
    }
    text = (REPO_ROOT / "docs" / "versioning.md").read_text(encoding="utf-8")

    assert marked >= {"awx test", "workspace", "dotfiles"}
    assert versioning_problems(text, marked) == []


def test_every_experimental_setting_of_the_repo_is_on_the_versioning_page(
    first_party_candidates: tuple[ProviderCandidate, ...], fresh_composition: None
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    keys = {
        mark.where
        for mark in marks(root, bootstrap.composition())
        if mark.target == "setting" and isinstance(mark.stability, Experimental)
    }
    text = (REPO_ROOT / "docs" / "versioning.md").read_text(encoding="utf-8")

    assert keys >= {"awx.test_timeout_seconds", "awx.test_parallel"}
    assert unnamed_settings(text, keys) == []


def test_a_setting_the_page_does_not_name_fails() -> None:
    text = _bullets("awx test").replace("things.", "the `awx.test_parallel` setting.")

    assert unnamed_settings(text, {"awx.test_parallel", "awx.other"}) == ["awx.other"]


def test_every_experimental_sdk_object_is_on_the_versioning_page() -> None:
    text = (REPO_ROOT / "docs" / "versioning.md").read_text(encoding="utf-8")
    marked = experimental_objects(sdk)

    assert marked >= {"Screen", "Cmd", "Quit", "Footer", "UiContext.run"}
    assert unlisted_objects(text, marked) == []


def test_every_experimental_testing_object_is_on_the_versioning_page() -> None:
    text = (REPO_ROOT / "docs" / "versioning.md").read_text(encoding="utf-8")
    marked = experimental_objects(testing)

    assert marked >= {"drive_screen", "ScreenKeys", "ScreenRun"}
    assert unlisted_objects(text, marked, prefix="untaped.testing.") == []


def test_a_marked_object_the_page_does_not_name_fails() -> None:
    page = _bullets("awx test").replace("things.", "the `Screen` and `UiContext.run` API.")

    assert unlisted_objects(page, {"Screen", "UiContext.run", "Cmd"}) == ["Cmd"]
    assert unlisted_objects(page, {"drive_screen"}, prefix="untaped.testing.") == ["drive_screen"]
    assert (
        unlisted_objects(
            page.replace("`Screen`", "`untaped.testing.drive_screen`"),
            {"drive_screen"},
            prefix="untaped.testing.",
        )
        == []
    )


def test_experimental_objects_are_found_by_their_marks() -> None:
    class Holder:
        @staticmethod
        def plain() -> None: ...

        @sdk.experimental
        def marked(self) -> None: ...

    module = ModuleType("fake")
    module.__all__ = ["Holder", "Free"]  # type: ignore[attr-defined]
    module.Holder = Holder  # type: ignore[attr-defined]
    module.Free = sdk.experimental(type("Free", (), {}))  # type: ignore[attr-defined]

    assert experimental_objects(module) == {"Free", "Holder.marked"}

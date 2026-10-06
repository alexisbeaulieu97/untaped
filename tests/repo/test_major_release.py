"""A major release ships without the previous major's deprecated spellings.

Fires when the workspace version is ``X.0.0`` (or an ``X.0.0`` pre-release)
and ``CHANGELOG.md`` has no ``## Unreleased`` heading, which is the state of
a major release PR. Between releases main carries the last released version,
so the version alone would fire there. ``retired_keys`` never count: they are
what a major leaves behind for ``config migrate``.
"""

from __future__ import annotations

import functools
import re
import warnings
from collections.abc import Iterator, Mapping
from typing import Annotated, Any, ClassVar

import pytest
import release  # scripts/release.py (``pythonpath = ["scripts"]``)
from cyclopts import App, Parameter
from packaging.version import Version
from pydantic import BaseModel

from repo.support import REPO_ROOT
from untaped import bootstrap, sdk
from untaped.capabilities.registry import ProviderCandidate
from untaped.settings import profile_section_models
from untaped.stability import deprecated_alias, deprecated_aliases


def is_major_release(version: str, changelog: str) -> bool:
    """Whether ``version`` with this ``changelog`` is a major release being prepared."""
    parsed = Version(version)
    unreleased = re.search(r"^## Unreleased\b", changelog, re.MULTILINE) is not None
    return parsed.minor == 0 and parsed.micro == 0 and not unreleased


def settings_leftovers(sections: Mapping[str, type[BaseModel]]) -> list[str]:
    """Each section declaring ``renamed_keys`` or ``deprecated_settings``."""
    return [
        f"{section}: {declaration} {sorted(getattr(model, declaration))}"
        for section, model in sections.items()
        for declaration in ("renamed_keys", "deprecated_settings")
        if getattr(model, declaration, None)
    ]


def alias_leftovers(app: App, path: tuple[str, ...] = ()) -> Iterator[str]:
    """Every ``deprecated_alias`` registered on ``app`` or a command below it."""
    where = " ".join(path) or "untaped"
    for old, new in sorted(deprecated_aliases(app).items()):
        yield f"{where}: deprecated alias {old} -> {new}"
    for name in app:
        if not name.startswith("-"):
            yield from alias_leftovers(app[name], (*path, name))


def option_leftovers(app: App, path: tuple[str, ...] = ()) -> Iterator[str]:
    """Every hidden option whose help starts with ``Deprecated:`` on a command below ``app``."""
    where = " ".join(path) or "untaped"
    if app.default_command is not None:
        for argument in app.assemble_argument_collection():
            help_text = argument.parameter.help or ""
            if not argument.show and help_text.startswith("Deprecated:"):
                yield f"{where}: deprecated option {' '.join(argument.names)}"
    for name in app:
        if not name.startswith("-"):
            yield from option_leftovers(app[name], (*path, name))


def sdk_leftovers(namespace: Mapping[str, object]) -> list[str]:
    """Each export, or attribute of an exported class, marked ``@warnings.deprecated``."""
    found = [name for name, value in namespace.items() if _deprecated(value)]
    for name, value in namespace.items():
        if isinstance(value, type):
            found.extend(
                f"{name}.{attr}" for attr, member in vars(value).items() if _deprecated(member)
            )
    return found


def _deprecated(value: object) -> bool:
    if isinstance(value, property):
        value = value.fget
    elif isinstance(value, functools.cached_property):
        value = value.func
    elif isinstance(value, staticmethod | classmethod):
        value = value.__func__
    return getattr(value, "__deprecated__", None) is not None


def test_a_major_release_drops_deprecated_spellings(
    first_party_candidates: tuple[ProviderCandidate, ...], fresh_composition: None
) -> None:
    version = release.release_version(REPO_ROOT)
    if not is_major_release(version, (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")):
        pytest.skip(f"{version} is not a major release being prepared")
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    sections = profile_section_models()
    exports = {name: getattr(sdk, name) for name in sdk.__all__}

    leftovers = [
        *settings_leftovers(sections),
        *alias_leftovers(root),
        *option_leftovers(root),
        *sdk_leftovers(exports),
    ]

    assert not leftovers, f"remove before releasing {version}:\n" + "\n".join(leftovers)


@pytest.mark.parametrize(
    ("version", "changelog", "expected"),
    [
        ("11.0.0", "# Changelog\n\n## 11.0.0\n", True),
        ("11.0.0rc1", "# Changelog\n\n## 11.0.0\n", True),
        ("11.0.0", "# Changelog\n\n## Unreleased\n\n## 10.0.0\n", False),
        ("11.1.0", "# Changelog\n\n## 11.1.0\n", False),
        ("11.0.1", "# Changelog\n\n## 11.0.1\n", False),
    ],
)
def test_is_major_release(version: str, changelog: str, expected: bool) -> None:
    assert is_major_release(version, changelog) is expected


class _Renamed(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old": "new"}
    retired_keys: ClassVar[dict[str, str]] = {"older": "new"}
    new: int = 0


class _Deprecated(BaseModel):
    deprecated_settings: ClassVar[dict[str, str]] = {"legacy": "use new"}
    legacy: bool = False


class _Retired(BaseModel):
    retired_keys: ClassVar[dict[str, str]] = {"old": "new"}
    new: int = 0


def test_settings_leftovers_name_each_section_but_not_retired_keys() -> None:
    sections = {"a": _Renamed, "b": _Deprecated, "c": _Retired}

    assert settings_leftovers(sections) == [
        "a: renamed_keys ['old']",
        "b: deprecated_settings ['legacy']",
    ]


def test_alias_leftovers_name_the_command() -> None:
    root = App(name="untaped")
    group = App(name="jira")
    group.command(App(name="whoami"))
    root.command(group)
    deprecated_alias(root, "--old", "--new")
    deprecated_alias(group, "me", "whoami")

    assert list(alias_leftovers(root)) == [
        "untaped: deprecated alias --old -> --new",
        "jira: deprecated alias me -> whoami",
    ]


def test_option_leftovers_name_hidden_deprecated_options_only() -> None:
    root = App(name="untaped")
    group = App(name="awx")

    @group.command(name="run")
    def run(
        *,
        old: Annotated[
            bool, Parameter(name="--old", negative="", show=False, help="Deprecated: use new.")
        ] = False,
        hidden: Annotated[bool, Parameter(name="--hidden", negative="", show=False)] = False,
        shown: Annotated[
            bool, Parameter(name="--shown", negative="", help="Deprecated: still visible.")
        ] = False,
    ) -> None: ...

    root.command(group)

    assert list(option_leftovers(root)) == ["awx run: deprecated option --old"]


def test_option_leftovers_see_the_first_party_deprecated_dry_run(
    first_party_candidates: tuple[ProviderCandidate, ...], fresh_composition: None
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    assert "awx test run: deprecated option --dry-run" in list(option_leftovers(root))


@warnings.deprecated("use g")
def _old_function() -> None: ...


class _Client:
    @property
    @warnings.deprecated("use b")
    def a(self) -> int:
        return 1

    @functools.cached_property
    @warnings.deprecated("use d")
    def c(self) -> int:
        return 1

    @staticmethod
    @warnings.deprecated("use f")
    def e() -> int:
        return 1

    @classmethod
    @warnings.deprecated("use h")
    def g(cls) -> int:
        return 1

    def current(self) -> int:
        return 1


def test_sdk_leftovers_look_through_properties_and_method_wrappers() -> None:
    namespace: dict[str, Any] = {"f": _old_function, "Client": _Client, "VALUE": 3}

    assert sdk_leftovers(namespace) == ["f", "Client.a", "Client.c", "Client.e", "Client.g"]


def test_the_deprecated_shell_aliases_setting_blocks_a_major_release(
    first_party_candidates: tuple[ProviderCandidate, ...], fresh_composition: None
) -> None:
    bootstrap.build_root_app(candidates=first_party_candidates)

    assert "shell: deprecated_settings ['aliases']" in settings_leftovers(profile_section_models())

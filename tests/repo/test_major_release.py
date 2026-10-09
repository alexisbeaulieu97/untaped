"""A major release ships without the previous major's deprecated spellings.

Fires when the workspace version is ``X.0.0`` (or an ``X.0.0`` pre-release),
``CHANGELOG.md`` has a section for that version and ``changelog.d/`` holds no
fragments, which is the state of a major release PR (and of main just after
the release, where the test passes anyway). Between releases main carries the
last released version, so the version alone would fire there. ``retired_keys`` never count: they are
what a major leaves behind for ``config migrate``.
"""

from __future__ import annotations

import functools
import re
import warnings
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from typing import Annotated, Any, ClassVar

import pytest
import release  # scripts/release.py (``pythonpath = ["scripts"]``)
from cyclopts import App, Parameter
from packaging.version import Version
from pydantic import BaseModel

import changelog  # scripts/changelog.py
from repo.support import REPO_ROOT
from test_plugins.plugin_harness import make_spec
from untaped import bootstrap, sdk
from untaped.cli import create_app
from untaped.plugins.registry import CompositionResult, ProviderCandidate
from untaped.settings import profile_section_models
from untaped.stability import (
    Deprecated,
    deprecated,
    deprecated_alias,
    deprecated_aliases,
    experimental,
    marks,
)
from untaped.testing import provider_candidate


def is_major_release(version: str, text: str, fragments: Sequence[str]) -> bool:
    """Whether ``version`` with this changelog ``text`` and fragments is a major release in prep."""
    parsed = Version(version)
    built = re.search(rf"^## {re.escape(version)}$", text, re.MULTILINE) is not None
    return parsed.minor == 0 and parsed.micro == 0 and built and not fragments


def settings_leftovers(sections: Mapping[str, type[BaseModel]]) -> list[str]:
    """Each section declaring ``renamed_keys``."""
    return [
        f"{section}: renamed_keys {sorted(model.renamed_keys)}"
        for section, model in sections.items()
        if getattr(model, "renamed_keys", None)
    ]


def mark_leftovers(root: App, result: CompositionResult) -> list[str]:
    """Every plugin, group, command or setting marked ``deprecated``."""
    return [
        (
            f"{mark.where}: deprecated setting"
            if mark.target == "setting"
            else f"untaped {mark.where}: deprecated {mark.target}"
        )
        for mark in marks(root, result, resolve=True)
        if isinstance(mark.stability, Deprecated)
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
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not is_major_release(version, text, changelog.fragment_paths(REPO_ROOT)):
        pytest.skip(f"{version} is not a major release being prepared")
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    sections = profile_section_models()
    exports = {name: getattr(sdk, name) for name in sdk.__all__}

    leftovers = [
        *settings_leftovers(sections),
        *mark_leftovers(root, bootstrap.composition()),
        *alias_leftovers(root),
        *option_leftovers(root),
        *sdk_leftovers(exports),
    ]

    assert not leftovers, f"remove before releasing {version}:\n" + "\n".join(leftovers)


@pytest.mark.parametrize(
    ("version", "text", "fragments", "expected"),
    [
        ("11.0.0", "# Changelog\n\n## 11.0.0\n", [], True),
        ("11.0.0rc1", "# Changelog\n\n## 11.0.0rc1\n", [], True),
        # A pre-release section is retitled by the final build, so the final version needs its own.
        ("11.0.0", "# Changelog\n\n## 11.0.0rc1\n", [], False),
        # Fragments left in changelog.d/ mean the release has not been built (main before it).
        ("11.0.0", "# Changelog\n\n## 10.0.0\n", ["changelog.d/x.added.md"], False),
        ("11.0.0", "# Changelog\n\n## 10.0.0\n", [], False),
        ("11.1.0", "# Changelog\n\n## 11.1.0\n", [], False),
        ("11.0.1", "# Changelog\n\n## 11.0.1\n", [], False),
    ],
)
def test_is_major_release(version: str, text: str, fragments: list[str], expected: bool) -> None:
    assert is_major_release(version, text, fragments) is expected


class _Renamed(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old": "new"}
    retired_keys: ClassVar[dict[str, str]] = {"older": "new"}
    new: int = 0


class _Retired(BaseModel):
    retired_keys: ClassVar[dict[str, str]] = {"old": "new"}
    new: int = 0


def test_settings_leftovers_name_each_section_but_not_retired_keys() -> None:
    sections = {"a": _Renamed, "c": _Retired}

    assert settings_leftovers(sections) == ["a: renamed_keys ['old']"]


def test_mark_leftovers_report_a_deprecated_plugin_group_and_command() -> None:
    def factory() -> App:
        app = create_app(name="svc", help="Service.")
        app.command(create_app(name="sunset", help="Sunset.", stability=deprecated()))

        @app.command(name="old")
        @deprecated()
        def old() -> None: ...

        @app.command(name="trial")
        @experimental
        def trial() -> None: ...

        return app

    spec = replace(make_spec(name="svc", factory=factory), stability=deprecated())
    root = bootstrap.build_root_app(candidates=[provider_candidate(spec)])

    found = mark_leftovers(root, bootstrap.composition())
    assert [line for line in found if line.startswith("untaped svc")] == [
        "untaped svc: deprecated plugin",
        "untaped svc sunset: deprecated command",
        "untaped svc old: deprecated command",
    ]


class _Marked(BaseModel):
    old: Annotated[bool, deprecated(replacement="new")] = False
    trial: Annotated[int, experimental] = 1
    new: bool = False


def test_mark_leftovers_report_a_deprecated_setting_but_not_an_experimental_one() -> None:
    spec = make_spec(name="svc", factory=lambda: create_app(name="svc", help="S."), profile=_Marked)
    root = bootstrap.build_root_app(candidates=[provider_candidate(spec)])

    found = mark_leftovers(root, bootstrap.composition())

    assert [line for line in found if line.startswith("svc.")] == ["svc.old: deprecated setting"]


def test_the_deprecated_alias_command_blocks_a_major_release() -> None:
    root = bootstrap.build_root_app(candidates=[])

    assert "untaped alias: deprecated group" in mark_leftovers(root, bootstrap.composition())


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
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    assert "shell.aliases: deprecated setting" in mark_leftovers(root, bootstrap.composition())

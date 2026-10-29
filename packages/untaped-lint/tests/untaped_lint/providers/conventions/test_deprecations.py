"""Deprecation lint: a mark on an overload, and a marked parameter nothing warns about."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions import DECLARED
from untaped_lint.providers.conventions.deprecations import deprecation_violations


def _violations(tmp_path: Path, source: str) -> list[str]:
    source_dir = tmp_path / "acme"
    source_dir.mkdir()
    (source_dir / "api.py").write_text(dedent(source).lstrip(), encoding="utf-8")
    return deprecation_violations(source_dir, list(source_files(source_dir)))


def test_both_rules_are_declared() -> None:
    assert {"mark-on-overload", "unwarned-parameter"} <= {rule.id for rule in DECLARED}


def test_a_mark_on_an_overload_is_a_violation(tmp_path: Path) -> None:
    source = """
        import typing
        import warnings
        from typing import overload
        from untaped.sdk import deprecated, experimental

        @overload
        @deprecated(removed_in=3)
        def f(x: int) -> int: ...
        @typing.overload
        @experimental
        def f(x: str) -> str: ...
        @overload
        @warnings.deprecated("f(bytes) is deprecated")
        def f(x: bytes) -> bytes: ...
        def f(x): return x
    """
    assert _violations(tmp_path, source) == [
        "acme/api.py:8::mark-on-overload::f: mark the implementation, not an overload",
        "acme/api.py:11::mark-on-overload::f: mark the implementation, not an overload",
    ]


def test_a_marked_parameter_without_the_decorator_is_a_violation(tmp_path: Path) -> None:
    source = """
        from typing import Annotated
        from untaped.sdk import deprecated, deprecated_parameters

        def emit(rows, *, kind: Annotated[str | None, deprecated(removed_in=3)] = None): ...
        async def fetch(x: "Annotated[int, deprecated(removed_in=3)]" = 0): ...
        @deprecated_parameters
        def fine(rows, *, kind: Annotated[str | None, deprecated(removed_in=3)] = None): ...
        def other(rows, *, kind: Annotated[str, "deprecated"] = ""): ...
        def optional(*, old: Annotated[bool, deprecated(removed_in=3)] | None = None): ...
        def element(x: list[Annotated[int, deprecated(removed_in=3)]]): ...
    """
    assert _violations(tmp_path, source) == [
        "acme/api.py:4::unwarned-parameter::emit(kind=) is marked deprecated but nothing warns "
        "when it is passed; add @deprecated_parameters",
        "acme/api.py:5::unwarned-parameter::fetch(x=) is marked deprecated but nothing warns "
        "when it is passed; add @deprecated_parameters",
        "acme/api.py:9::unwarned-parameter::optional(old=) is marked deprecated but nothing "
        "warns when it is passed; add @deprecated_parameters",
    ]


def test_a_command_flag_is_the_root_s_to_warn_about(tmp_path: Path) -> None:
    source = """
        from typing import Annotated
        from cyclopts import Parameter
        from untaped.sdk import deprecated

        def run(*, dry_run: Annotated[bool, Parameter(), deprecated(removed_in=3)] = False): ...
    """
    assert _violations(tmp_path, source) == []


def test_an_allow_marker_waives_one(tmp_path: Path) -> None:
    source = """
        from typing import Annotated
        from untaped.sdk import deprecated

        def emit(k: Annotated[int, deprecated()] = 0): ...  # untaped: allow unwarned-parameter
    """
    assert _violations(tmp_path, source) == []

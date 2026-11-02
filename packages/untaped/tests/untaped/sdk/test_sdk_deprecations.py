"""Deprecated SDK callables and parameters: the marks, the sentence and the warning."""

from __future__ import annotations

import asyncio
import inspect
import sys
import types
import warnings
from typing import TYPE_CHECKING, Annotated

import pytest

from untaped.core.plugin_check.stability import deprecation_text_violations
from untaped.runtime.run_output import Diagnostics
from untaped.sdk._parameter_marks import (
    callable_owner,
    deprecated_parameters,
    deprecation_sentence,
    parameter_marks,
    public_name,
    read_parameter_marks,
    warns_parameters,
)
from untaped.sdk._replacement import sdk_replacement_text
from untaped.sdk.marks import UntapedDeprecationWarning, deprecated, function_mark

if TYPE_CHECKING:
    from untaped.sdk import Record  # never imported at run time


# --- the mark and the sentence -------------------------------------------------------------


class Client:
    @classmethod
    @deprecated(removed_in=3)
    def build(cls) -> None: ...

    @staticmethod
    @deprecated(removed_in=3)
    def make() -> None: ...

    @property
    @deprecated(removed_in=3)
    def name(self) -> str:
        return ""


class Tolerant:
    @deprecated(removed_in=3)  # type: ignore[arg-type]
    @classmethod
    def build(cls) -> None: ...


def test_a_mark_under_a_descriptor_and_one_above_it_land_on_the_function() -> None:
    for owner, name in [(Client, "build"), (Client, "make"), (Tolerant, "build")]:
        member = vars(owner)[name]
        assert function_mark(member.__func__) == deprecated(removed_in=3)
    getter = vars(Client)["name"].fget
    assert function_mark(getter) == deprecated(removed_in=3)


def test_an_object_replacement_reads_as_its_qualname() -> None:
    assert sdk_replacement_text(deprecated(replacement=Diagnostics.failure)) == (
        "`Diagnostics.failure`"
    )
    assert sdk_replacement_text(deprecated(replacement="a log line")) == "a log line"
    assert sdk_replacement_text(deprecated()) is None


def test_core_s_callable_names_the_bare_version_and_a_plugin_s_its_distribution() -> None:
    def api() -> None: ...

    def core() -> None: ...

    core.__module__ = "untaped.sdk.probe"
    api.__module__ = "untaped_acme_tools.api"
    mark = deprecated(removed_in=3, replacement="new_api")
    assert callable_owner(core) is None
    assert callable_owner(api) == "untaped-acme-tools"
    assert deprecation_sentence(api, mark) == (
        "test_core_s_callable_names_the_bare_version_and_a_plugin_s_its_distribution.<locals>.api"
        " is deprecated and will be removed in untaped-acme-tools 3.0; use new_api"
    )


# --- the warning a literal raises ----------------------------------------------------------


def bright() -> None: ...


@deprecated(removed_in=3, replacement=bright)
@warnings.deprecated(
    "dim is deprecated and will be removed in 3.0; use `bright`",
    category=UntapedDeprecationWarning,
)
def dim() -> None: ...


@deprecated(removed_in=4)
@warnings.deprecated("Lamp is deprecated", category=UntapedDeprecationWarning)
class Lamp: ...


def _fields(warning: object) -> tuple[object, ...]:
    assert isinstance(warning, UntapedDeprecationWarning)
    return (warning.id, warning.removed_in, warning.distribution, warning.replacement)


def test_a_literal_s_warning_carries_its_callable_s_fields() -> None:
    (called,) = _caught(dim)
    (built,) = _caught(Lamp)
    assert _fields(called.message) == (f"sdk {__name__}.dim", 3, None, "bright")
    assert _fields(built.message) == (f"sdk {__name__}.Lamp", 4, None, None)


def test_a_literal_with_no_mark_over_it_carries_no_id() -> None:
    assert _fields(UntapedDeprecationWarning("nothing marks this")) == (None, None, None, None)


def _module(name: str, **names: object) -> types.ModuleType:
    module = types.ModuleType(name)
    vars(module).update(names)
    return module


def test_a_callable_is_named_by_the_imported_module_that_exports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Bulb:
        def dim(self) -> None: ...

    def glow() -> None: ...

    for each in (Bulb, Bulb.dim, glow):
        each.__module__ = "untaped_lamp._bulbs"
        each.__qualname__ = each.__name__
    Bulb.dim.__qualname__ = "Bulb.dim"
    defining = _module("untaped_lamp._bulbs", Bulb=Bulb, glow=glow)
    api = _module("untaped_lamp.api", Bulb=Bulb, glow=glow, __all__=["Bulb"])
    monkeypatch.setitem(sys.modules, "untaped_lamp._bulbs", defining)
    assert public_name(glow) == "untaped_lamp._bulbs.glow"  # api not imported: the defining one
    monkeypatch.setitem(sys.modules, "untaped_lamp.api", api)
    monkeypatch.setitem(sys.modules, "untaped_lamp", _module("untaped_lamp", __all__=[]))
    assert public_name(Bulb.dim) == "untaped_lamp.api.Bulb.dim"
    assert public_name(glow) == "untaped_lamp._bulbs.glow"  # not in api's __all__
    assert public_name(Diagnostics.failure) == "untaped.sdk.Diagnostics.failure"


# --- deprecated_parameters -----------------------------------------------------------------


@deprecated_parameters
def emit(
    rows: list[Record],
    *,
    kind: Annotated[
        str | None, deprecated(removed_in=12, replacement="kind= on the Record class")
    ] = None,
) -> int:
    return len(rows)


@deprecated_parameters
async def fetch(x: int, old: Annotated[bool, deprecated(removed_in=12)] = False) -> int:
    return x


class Builder:
    @classmethod
    @deprecated_parameters
    def build(cls, *, legacy: Annotated[bool, deprecated(removed_in=12)] = False) -> str:
        return cls.__name__


def _caught(call: object) -> list[warnings.WarningMessage]:
    with warnings.catch_warnings(record=True) as log:
        warnings.simplefilter("always")
        call()  # type: ignore[operator]
    return log


def test_a_marked_parameter_warns_when_passed_even_at_its_default() -> None:
    (passed,) = _caught(lambda: emit([], kind="x"))
    (default,) = _caught(lambda: emit([], kind=None))
    assert (
        str(passed.message)
        == str(default.message)
        == ("emit(kind=) is deprecated and will be removed in 12.0; use kind= on the Record class")
    )
    assert _caught(lambda: emit([])) == []


def test_the_warning_carries_its_fields_and_points_at_the_caller() -> None:
    with warnings.catch_warnings(record=True) as log:
        warnings.simplefilter("always")
        frame = inspect.currentframe()
        before = frame.f_lineno if frame is not None else 0
        emit([], kind="x")
    (shown,) = log
    assert (shown.filename, shown.lineno) == (__file__, before + 1)
    warning = shown.message
    assert isinstance(warning, UntapedDeprecationWarning)
    assert (warning.id, warning.removed_in, warning.distribution, warning.replacement) == (
        f"sdk {__name__}.emit(kind=)",
        12,
        None,
        "kind= on the Record class",
    )


def test_an_async_function_stays_one_and_warns() -> None:
    assert inspect.iscoroutinefunction(fetch)
    (shown,) = _caught(lambda: asyncio.run(fetch(1, old=True)))
    assert str(shown.message) == "fetch(old=) is deprecated and will be removed in 12.0"
    assert _caught(lambda: asyncio.run(fetch(1))) == []


def test_a_classmethod_warns_and_keeps_its_signature() -> None:
    (shown,) = _caught(lambda: Builder.build(legacy=True))
    assert str(shown.message).startswith("Builder.build(legacy=) is deprecated")
    assert list(inspect.signature(Builder.build).parameters) == ["legacy"]
    assert warns_parameters(vars(Builder)["build"])


def test_hints_imported_for_type_checking_never_raise() -> None:
    assert set(parameter_marks(emit)) == {"kind"}
    assert warns_parameters(emit)


@deprecated_parameters
def optional(*, old: Annotated[bool, deprecated(removed_in=12)] | None = None) -> bool | None:
    return old


def test_a_mark_inside_a_union_warns() -> None:
    (shown,) = _caught(lambda: optional(old=True))
    assert str(shown.message) == "optional(old=) is deprecated and will be removed in 12.0"


def test_a_call_that_does_not_bind_raises_python_s_own_error() -> None:
    with pytest.raises(TypeError, match=r"^emit\(\) got an unexpected keyword argument 'nope'"):
        emit([], nope=1)  # type: ignore[call-arg]


def test_the_decorator_above_a_descriptor_says_where_it_goes() -> None:
    with pytest.raises(TypeError, match="put @deprecated_parameters under @classmethod"):
        deprecated_parameters(classmethod(lambda cls: None))  # type: ignore[arg-type]


# --- lazily evaluated annotations (a module without the future import) ----------------------


def test_a_hint_only_type_checkers_import_never_raises_at_a_call() -> None:
    import core_test_support.lazy_annotations_sample as lazy

    (shown,) = _caught(lambda: lazy.hinted(x=1))
    assert str(shown.message).startswith("hinted(x=) is deprecated")
    assert _caught(lambda: lazy.hinted(y=None)) == []


def test_a_marked_var_keyword_warns_and_an_element_mark_is_not_the_parameter_s() -> None:
    import core_test_support.lazy_annotations_sample as lazy

    (shown,) = _caught(lambda: lazy.extra(a=1))
    assert str(shown.message).startswith("extra(kwargs=) is deprecated")
    assert _caught(lambda: lazy.element([1])) == []


def test_what_a_lazy_module_can_t_resolve_is_reported() -> None:
    import core_test_support.lazy_annotations_sample as lazy

    found = deprecation_text_violations(lazy)
    assert found == [
        "core_test_support.lazy_annotations_sample.Settings.__init__::unwarned-parameter::"
        "flag= is marked "
        "deprecated but nothing warns when it is passed; write the __init__ yourself, under "
        "@deprecated_parameters",
        "core_test_support.lazy_annotations_sample.unresolved::unwarned-parameter::"
        "x=: its deprecated(...) mark "
        "can't be evaluated where the function is defined, so it never warns "
        "(replacement=helper is not defined); name only module-level objects in it",
    ]


def test_other_metadata_that_fails_to_evaluate_is_not_a_mark() -> None:
    def documented(x: Annotated[int, Doc("deprecated soon")] = 0) -> None: ...  # type: ignore[name-defined]  # noqa: F821

    assert read_parameter_marks(documented) == ({}, {})

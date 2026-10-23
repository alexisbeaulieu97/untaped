"""The ``sdk`` walker, one shape per test, over a sample module."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from untaped.core.surface import SDK_MODULES, Line, SurfaceError
from untaped.core.surface._sdk import collect_exports, module_lines

_NAME = "untaped_sample"


@contextmanager
def _sample(names: Callable[[list[str]], list[str]]) -> Iterator[None]:
    """The sample module imported as ``untaped_sample``, exporting ``names(__all__)``."""
    spec = importlib.util.spec_from_file_location(_NAME, Path(__file__).with_name("sdk_sample.py"))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module
    try:
        spec.loader.exec_module(module)
        module.__all__ = names(module.__all__)
        yield
    finally:
        sys.modules.pop(_NAME, None)


@pytest.fixture(scope="module")
def lines() -> Iterator[set[str]]:
    with _sample(lambda names: [name for name in names if not name.startswith("watch")]):
        exports = collect_exports({_NAME: None})
        yield {str(line) for line in module_lines(_NAME, None, exports)}


def _of(lines: set[str], name: str) -> list[str]:
    prefix = f"sdk {_NAME}.{name}"
    return sorted(
        line
        for line in lines
        if line.startswith((f"{prefix}(", f"{prefix}.", f"{prefix} ")) or line == prefix
    )


def test_constants_and_aliases_are_names(lines: set[str]) -> None:
    assert _of(lines, "VERSION") == [f"sdk {_NAME}.VERSION"]
    assert _of(lines, "NO_LIMIT") == [f"sdk {_NAME}.NO_LIMIT"]
    assert _of(lines, "Name") == [f"sdk {_NAME}.Name"]


def test_a_literal_alias_has_a_choice_line_per_value(lines: set[str]) -> None:
    assert _of(lines, "Format") == [
        f"sdk {_NAME}.Format choice=json",
        f"sdk {_NAME}.Format choice=table",
    ]


def test_enum_members_carry_their_values(lines: set[str]) -> None:
    assert _of(lines, "Color") == [f"sdk {_NAME}.Color.BLUE = 2", f"sdk {_NAME}.Color.RED = red"]


def test_a_function_lists_required_parameters_and_its_callback(lines: set[str]) -> None:
    assert _of(lines, "fill") == [
        f"sdk {_NAME}.fill(limit=) default=NO_LIMIT",
        f"sdk {_NAME}.fill(source, items) callback(source, 2)",
    ]


def test_an_optional_protocol_callback_carries_its_arity(lines: set[str]) -> None:
    assert _of(lines, "fill_later") == [
        f"sdk {_NAME}.fill_later(each@2=) default=None callback(2)",
        f"sdk {_NAME}.fill_later(items)",
    ]


def test_a_paramspec_callback_positional_only_and_var_parameters(lines: set[str]) -> None:
    assert _of(lines, "gather") == [
        f"sdk {_NAME}.gather(**kwargs)",
        f"sdk {_NAME}.gather(*args)",
        f"sdk {_NAME}.gather(method, /) callback(method, P)",
    ]


def test_a_callable_inside_a_container_counts_and_enum_defaults_by_name(lines: set[str]) -> None:
    assert _of(lines, "listing") == [
        f"sdk {_NAME}.listing(color=) default=Color.RED",
        f"sdk {_NAME}.listing(rows) callback(rows, *)",
    ]


def test_required_keywords_follow_a_star(lines: set[str]) -> None:
    assert _of(lines, "place") == [
        f"sdk {_NAME}.place(at@2=) default=0",
        f"sdk {_NAME}.place(flags=) default=None",
        f"sdk {_NAME}.place(where, *, near)",
    ]


def test_a_bare_callable_and_an_optional_positional_only_parameter(lines: set[str]) -> None:
    assert _of(lines, "hook") == [
        f"sdk {_NAME}.hook(name, run, /) callback(run, *)",
        f"sdk {_NAME}.hook(size@3/=) default=0",
    ]


def test_marks(lines: set[str]) -> None:
    assert _of(lines, "preview") == [
        f"sdk {_NAME}.preview()  [experimental]",
        f"sdk {_NAME}.preview(count@1=) default=3  [experimental]",
    ]
    assert _of(lines, "legacy") == [f"sdk {_NAME}.legacy()  [deprecated]"]
    assert f"sdk {_NAME}.Widget.grow()  [experimental]" in lines


def test_the_mro_stops_at_the_first_foreign_base(lines: set[str]) -> None:
    assert _of(lines, "Fill") == [
        f"sdk {_NAME}.Fill(message)",
        f"sdk {_NAME}.Fill.category class",
        f"sdk {_NAME}.Fill.hint class",
    ]


def test_a_model_lists_fields_class_attributes_methods_and_subclass_keywords(
    lines: set[str],
) -> None:
    assert _of(lines, "Record") == [
        f"sdk {_NAME}.Record(*, name)",
        f"sdk {_NAME}.Record(count=) default=0",
        f"sdk {_NAME}.Record(tags=) default=[]",
        f'sdk {_NAME}.Record.__init_subclass__(kind=) default=""',
        f"sdk {_NAME}.Record.count",
        f"sdk {_NAME}.Record.name",
        f"sdk {_NAME}.Record.show()",
        f"sdk {_NAME}.Record.show(wide=) default=false",
        f"sdk {_NAME}.Record.table_columns class",
        f"sdk {_NAME}.Record.tags",
        f"sdk {_NAME}.Record.title",
    ]


def test_a_dataclass_with_factories_and_a_positional_only_method(lines: set[str]) -> None:
    assert _of(lines, "Point") == [
        f"sdk {_NAME}.Point(labels@3=) default=[]",
        f"sdk {_NAME}.Point(made@4=) default=<computed>",
        f"sdk {_NAME}.Point(x)",
        f"sdk {_NAME}.Point(y@2=) default=0",
        f"sdk {_NAME}.Point.labels",
        f"sdk {_NAME}.Point.made",
        f"sdk {_NAME}.Point.moved(dx, /)",
        f"sdk {_NAME}.Point.moved(dy@2=) default=0",
        f"sdk {_NAME}.Point.x",
        f"sdk {_NAME}.Point.y",
    ]


def test_a_home_path_default_and_a_plain_class(lines: set[str]) -> None:
    assert f"sdk {_NAME}.Settings(path@1=) default=~/.untaped" in lines
    assert _of(lines, "Widget") == [
        f"sdk {_NAME}.Widget(on_change@2=) default=None callback(1)",
        f"sdk {_NAME}.Widget(size)",
        f"sdk {_NAME}.Widget.default()",
        f"sdk {_NAME}.Widget.grow()  [experimental]",
        f"sdk {_NAME}.Widget.size",
        f"sdk {_NAME}.Widget.sized(size)",
    ]


def test_a_protocol_promises_its_members_not_a_constructor(lines: set[str]) -> None:
    assert _of(lines, "Teller") == [f"sdk {_NAME}.Teller", f"sdk {_NAME}.Teller.tell(text)"]


def test_no_line_names_pydantic_or_stdlib_members(lines: set[str]) -> None:
    for member in ("model_dump", "model_validate", "with_traceback", "add_note", "args"):
        assert not [line for line in lines if f".{member}" in line]


@pytest.mark.parametrize("name", ["watch", "watch_bare"])
def test_an_unresolvable_callback_annotation_stops_generation(name: str) -> None:
    with (
        _sample(lambda _: [name]),
        pytest.raises(SurfaceError, match=rf"{name}\(callback\): its annotation"),
    ):
        module_lines(_NAME, None, collect_exports({_NAME: None}))


def test_a_module_without_all_stops_generation() -> None:
    with pytest.raises(SurfaceError, match="has no __all__"):
        collect_exports({"untaped.core.surface._guard": None})


def test_a_plugin_mark_reaches_every_line_without_its_own() -> None:
    lines = module_lines(
        "untaped.sdk.testing.git",
        "experimental",
        collect_exports({"untaped.sdk.testing.git": "experimental"}),
    )
    assert lines
    assert {line.mark for line in lines} == {"experimental"}
    assert Line("sdk untaped.sdk.testing.git.git_remote", "experimental") not in lines


def test_an_implementer_lists_what_plugins_implement_with_full_signatures() -> None:
    exports = collect_exports(dict.fromkeys(SDK_MODULES))
    lines = {line.text for line in module_lines("untaped.sdk.contracts", None, exports)}
    assert "sdk untaped.sdk.contracts.Contract implement=" in lines
    assert "sdk untaped.sdk.contracts.Contract.ready() -> NotReady?" in lines
    testing = {line.text for line in module_lines("untaped.sdk.testing", None, exports)}
    assert (
        "sdk untaped.sdk.testing.PromptBackend.secret(message: string, *, confirmation: boolean) "
        "-> string"
    ) in testing


def test_a_plugin_class_lists_its_own_members_and_its_exported_bases() -> None:
    with _sample(lambda _: ["Refused"]):
        exports = collect_exports({**dict.fromkeys(SDK_MODULES), _NAME: None})
        lines = {line.text for line in module_lines(_NAME, None, exports, plugin=True)}
    assert lines == {
        f"sdk {_NAME}.Refused base=untaped.sdk.UntapedError",
        f"sdk {_NAME}.Refused(message, *, reason)",
        f"sdk {_NAME}.Refused.category class",
        f"sdk {_NAME}.Refused.why()",
    }


def test_a_marked_parameter_is_dated_on_its_own_line(lines: set[str]) -> None:
    assert _of(lines, "marked") == [
        f"sdk {_NAME}.marked(old=) default=None  [deprecated, removed in 12.0; use x]",
        f"sdk {_NAME}.marked(x)",
    ]
    assert f"sdk {_NAME}.Boxed(old@1=) default=false  [deprecated, removed in 12.0]" in _of(
        lines, "Boxed"
    )

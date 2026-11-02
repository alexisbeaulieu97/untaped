"""The two-major window over small line sets: :func:`broken` and the ``Line`` helpers it reads."""

from __future__ import annotations

import pytest

from untaped.core.surface import DeprecatedMark, Line, LineMark, broken


def _due(major: int = 13) -> DeprecatedMark:
    return DeprecatedMark(major, "untaped.sdk.new")


@pytest.mark.parametrize(
    ("text", "facing"),
    [
        ("sdk untaped.sdk.old(x)", True),
        ("contract acme.items.list() -> Item[]", True),
        ("convention entry-point untaped.plugins", True),
        ("record acme.Item", True),  # a kind's own name: users write it into their files
        ("record acme.Item.name string", False),
        ("cli untaped acme list", False),
        ("cfg acme.mode string", False),
        ("state acme.items.name string", False),
    ],
)
def test_which_lines_plugins_build_on(text: str, facing: bool) -> None:
    assert Line(text).plugin_facing is facing


@pytest.mark.parametrize(
    ("text", "key", "parameter"),
    [
        ("sdk m.f(a, *, b) callback(b, 1)", "sdk m.f()", None),
        ("sdk m.f(n=) default=1", "sdk m.f(n=)", ("m.f", "n")),
        ("sdk m.f(n@2=) default=1", "sdk m.f(n=)", ("m.f", "n")),
        ("sdk m.f(n@1/=) default=1", "sdk m.f(n=)", ("m.f", "n")),
        ("sdk m.f(*args)", "sdk m.f(*args)", ("m.f", "*args")),
        (
            "contract o.c.m(limit@2=) integer default=10",
            "contract o.c.m(limit=)",
            ("o.c.m", "limit"),
        ),
        ("contract o.c.m(name: string) -> Item?", "contract o.c.m()", None),
        ("sdk m.C base=untaped.sdk.Record", "sdk m.C base=", None),
        ("sdk m.C extra=forbid", "sdk m.C extra=", None),
        ("sdk m.Kind choice=a", "sdk m.Kind choice=", None),
        ("sdk m.Color.RED = red", "sdk m.Color.RED =", None),
        ("sdk m.C.size class", "sdk m.C.size", None),
        ("contract o.Item.name string", "contract o.Item.name", None),
        ("sdk m.C.size", "sdk m.C.size", None),
    ],
)
def test_a_line_s_name_apart_from_its_shape(
    text: str, key: str, parameter: tuple[str, str] | None
) -> None:
    assert (Line(text).key, Line(text).parameter) == (key, parameter)


# --- the window ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "ok"),
    [
        (Line("sdk untaped.sdk.old(x)", DeprecatedMark(13)), False),
        (Line("sdk untaped.sdk.old(x)", DeprecatedMark(14)), True),
        (Line("contract acme.items.list() -> Item[]", DeprecatedMark(13)), False),
        (Line("record acme.Item", DeprecatedMark(13)), False),
        (Line("record acme.Item.name string", DeprecatedMark(13)), True),  # fields: one major
        (Line("cli untaped acme list", DeprecatedMark(13)), True),
    ],
    ids=str,
)
def test_a_name_plugins_use_is_deprecated_for_two_majors(line: Line, ok: bool) -> None:
    found = broken([Line(line.text)], [line], major=12)
    assert (not found) is ok
    if not ok:
        assert found == [
            f"{line}: plugins built on 12.x must keep working on 13.x; deprecate with "
            "removed_in=14 and add a new name"
        ]


def test_a_date_already_shown_is_the_shortening_rule_s_business() -> None:
    shown = Line("sdk untaped.sdk.old(x)", DeprecatedMark(13))
    assert broken([shown], [shown], major=12) == []


def test_no_recorded_line_still_needs_the_window() -> None:
    assert broken([], [Line("sdk m.new(x)", DeprecatedMark(13))], major=12)


# --- go only when due ---------------------------------------------------------------------


def test_an_undeprecated_name_never_goes_even_at_a_major() -> None:
    (message,) = broken([Line("sdk m.f(x)")], [], major=13)
    assert message.startswith("sdk m.f(x): a name plugins use goes only at the major its ")
    assert message.endswith("deprecate it with removed_in=15 and add a new name")


def test_a_due_name_goes() -> None:
    assert broken([Line("sdk m.f(x)", _due(13))], [], major=13) == []


def test_a_name_dated_later_stays() -> None:
    assert broken([Line("sdk m.f(x)", _due(14))], [], major=13)


def test_a_changed_default_is_a_new_name() -> None:
    recorded = [Line("sdk m.f(x)"), Line("sdk m.f(n=) default=1")]
    current = [Line("sdk m.f(x)"), Line("sdk m.f(n=) default=2")]
    (message,) = broken(recorded, current, major=13)
    assert message.startswith("sdk m.f(n=) default=1: a name plugins use goes only")


def test_a_required_parameter_made_optional_keeps_its_promise() -> None:
    recorded = [Line("sdk m.f(x, y)")]
    current = [Line("sdk m.f(x)"), Line("sdk m.f(y@2=) default=None")]
    assert broken(recorded, current, major=12) == []


def test_other_areas_keep_their_own_rule() -> None:
    assert broken([Line("cli untaped acme list")], [], major=13) == []


# --- parameters ---------------------------------------------------------------------------


def test_a_due_parameter_goes_only_with_its_callable() -> None:
    owner = Line("sdk m.f(x)")
    gone = Line("sdk m.f(n=) default=1", _due(13))
    (message,) = broken([owner, gone], [owner], major=13)
    assert message == (
        f"{gone}: m.f stays, so a plugin still passing n gets a TypeError; a parameter goes "
        "only with its callable (deprecate the callable for a new one)"
    )
    assert broken([Line("sdk m.f(x)", _due(13)), gone], [], major=13) == []


@pytest.mark.parametrize("extra", ["forbid", "allow"])
def test_a_due_field_goes_from_a_model_that_says_so(extra: str) -> None:
    model = [Line("sdk m.Item(*, name)"), Line(f"sdk m.Item extra={extra}")]
    gone = [Line("sdk m.Item(old=) default=0", _due(13)), Line("sdk m.Item.old", _due(13))]
    assert broken([*model, *gone], model, major=13) == []


def test_a_due_field_stays_on_a_model_that_says_nothing() -> None:
    model = [Line("sdk m.Item(*, name)")]  # a dataclass: a passed field is a TypeError
    gone = [Line("sdk m.Item(old=) default=0", _due(13)), Line("sdk m.Item.old", _due(13))]
    assert len(broken([*model, *gone], model, major=13)) == 1


# --- experimental names --------------------------------------------------------------------


def test_an_experimental_name_respelled_in_place_fails() -> None:
    recorded = [Line("sdk m.f(n=) default=1", "experimental")]
    current = [Line("sdk m.f(n=) default=2", "experimental")]
    (message,) = broken(recorded, current, major=12)
    assert message.startswith("sdk m.f(n=) default=1  [experimental]: an experimental name")


_X: LineMark = "experimental"


@pytest.mark.parametrize(
    ("recorded", "current"),
    [
        ([Line("sdk m.f(n=) default=1", _X)], []),  # a plugin meets a missing name
        ([Line("sdk m.f(n=) default=1", _X)], [Line("sdk m.g(n=) default=2", _X)]),
        (
            [Line("sdk m.f(x, y)", _X)],
            [Line("sdk m.f(x)", _X), Line("sdk m.f(y@2=) default=None", _X)],
        ),
    ],
    ids=["removed", "new-name", "loosened"],
)
def test_an_experimental_name_may_go_or_loosen(recorded: list[Line], current: list[Line]) -> None:
    assert broken(recorded, current, major=12) == []


# --- the switches --------------------------------------------------------------------------


def test_before_the_window_only_new_deprecations_are_checked() -> None:
    recorded = [Line("sdk m.f(x)"), Line("sdk m.g()", "experimental")]
    current = [Line("sdk m.g(y)", "experimental"), Line("sdk m.h()", DeprecatedMark(13))]
    found = broken(recorded, current, major=12, removals=False)
    assert found == [
        "sdk m.h()  [deprecated, removed in 13.0]: plugins built on 12.x must keep working "
        "on 13.x; deprecate with removed_in=14 and add a new name"
    ]


def test_a_waived_line_may_go_early() -> None:
    recorded = [Line("sdk m.leaky(x)")]
    assert broken(recorded, [], major=12, waived={"sdk m.leaky(x)"}) == []

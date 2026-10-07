"""``field_for`` over the real settings: every leaf maps, and the declared bounds are enforced.

The descriptors come from the settings models of the root app and of every
first-party capability, so a setting added with a type nobody mapped (or a
constraint ``field_for`` cannot read) fails here instead of in a screen.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from untaped.bootstrap import SHELL_SPEC
from untaped.capabilities.registry import CapabilitySpec
from untaped.config_schema import FieldDescriptor, find_descriptor, walk_settings
from untaped.screen.components.choices import Cycle
from untaped.screen.components.fields import field_for
from untaped.screen.components.inputs import NumberInput
from untaped.screen.core import Key
from untaped.settings import Settings


def _models(specs: tuple[CapabilitySpec, ...]) -> list[tuple[str, type[BaseModel]]]:
    return [
        ("root", Settings),
        (SHELL_SPEC.config_section, SHELL_SPEC.profile_model),
        *((spec.config_section, spec.profile_model) for spec in specs),
    ]


def _descriptor(specs: tuple[CapabilitySpec, ...], section: str, key: str) -> FieldDescriptor:
    """The descriptor of ``key`` in ``section``'s model (``root`` is the core ``Settings``)."""
    for name, model in _models(specs):
        if name == section:
            descriptor = find_descriptor(walk_settings(model), key)
            if descriptor is not None:
                return descriptor
    raise AssertionError(f"no setting {section}.{key}")


def _text(component: Any, text: str) -> Any:
    """``component`` with its text replaced by ``text``, typed through its own keys."""
    for _ in range(len(component.text)):
        component = component.update(Key("backspace"))[0]
    for char in text:
        component = component.update(Key(char))[0]
    return component


def test_every_leaf_of_every_first_party_settings_model_has_a_component(
    first_party_specs: tuple[CapabilitySpec, ...],
) -> None:
    mapped = 0
    for _, model in _models(first_party_specs):
        for descriptor in walk_settings(model):
            assert field_for(descriptor) is not None, descriptor.key
            mapped += 1
    assert mapped > 40  # the walk really covered the capabilities


def test_ansible_git_fetch_parallel_enforces_one_to_thirty_two(
    first_party_specs: tuple[CapabilitySpec, ...],
) -> None:
    built = field_for(_descriptor(first_party_specs, "ansible", "git_fetch_parallel"))

    assert isinstance(built, NumberInput)
    assert (built.minimum, built.maximum) == (1, 32)
    assert built.value == 8
    for rejected in ("0", "33"):
        assert _text(built, rejected).validate() != "", rejected
    for accepted in ("1", "32"):
        assert _text(built, accepted).validate() == "", accepted


def test_awx_page_size_must_be_above_zero(first_party_specs: tuple[CapabilitySpec, ...]) -> None:
    built = field_for(_descriptor(first_party_specs, "awx", "page_size"))

    assert isinstance(built, NumberInput)
    assert _text(built, "0").validate() == "Must be at least 1."
    assert _text(built, "1").validate() == ""
    assert _text(built, "").validate() != ""  # required: it has a default, not an unset state


def test_http_timeout_seconds_is_a_float_above_zero(
    first_party_specs: tuple[CapabilitySpec, ...],
) -> None:
    built = field_for(_descriptor(first_party_specs, "root", "http.timeout_seconds"))

    assert isinstance(built, NumberInput)
    assert built.integer is False
    assert built.text == "30.0"
    assert _text(built, "0").validate() == "Must be greater than 0."
    assert _text(built, "0.0").validate() == "Must be greater than 0."
    assert _text(built, "0.5").validate() == ""
    assert _text(built, "0.5").value == 0.5


def test_workspace_parallel_may_be_left_empty_for_unset(
    first_party_specs: tuple[CapabilitySpec, ...],
) -> None:
    built = field_for(_descriptor(first_party_specs, "workspace", "parallel"))

    assert isinstance(built, NumberInput)
    assert built.text == ""
    assert built.value is None
    assert built.validate() == ""  # an optional number: empty is unset, and fine
    assert _text(built, "0").validate() != ""
    assert _text(built, "4").value == 4
    assert _text(_text(built, "4"), "").value is None


def test_ui_hide_empty_columns_is_a_three_state_cycle_starting_unset(
    first_party_specs: tuple[CapabilitySpec, ...],
) -> None:
    built = field_for(_descriptor(first_party_specs, "root", "ui.hide_empty_columns"))

    assert isinstance(built, Cycle)
    assert built.value is None
    on = built.update(Key("right"))[0]
    off = on.update(Key("right"))[0]
    assert (on.value, off.value) == (True, False)
    assert off.update(Key("right"))[0].value is None  # wraps back to unset
    assert built.update(Key("left"))[0].value is False

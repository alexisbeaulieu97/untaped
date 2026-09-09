"""Unit tests for post-write apply convergence verification."""

from __future__ import annotations

from untaped.capabilities.awx.application.apply_verifier import ApplyVerifier
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC


def test_verifier_accepts_reflected_structures_and_secret_paths() -> None:
    verifier = ApplyVerifier()
    desired = {
        "name": "deploy",
        "numbers": [2, 1],
        "extra_vars": {"wanted": True},
        "survey_spec": {
            "spec": [
                {
                    "variable": "pw",
                    "question_name": "Password",
                    "default": "actual-secret",
                }
            ]
        },
    }
    observed = {
        "name": "deploy",
        "numbers": [1, 2],
        "extra_vars": '{"wanted": true, "server_added": "ok"}',
        "survey_spec": {
            "spec": [
                {
                    "question_name": "Password",
                    "variable": "pw",
                    "required": False,
                    "min": None,
                    "max": None,
                    "new_question": False,
                    "default": "$encrypted$",
                }
            ]
        },
    }

    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        desired,
        observed,
        fields=("name", "numbers", "extra_vars", "survey_spec"),
    ) == ("numbers", "extra_vars")


def test_verifier_reports_only_fields_not_reflected() -> None:
    verifier = ApplyVerifier()
    desired = {
        "survey_spec": {
            "spec": [
                {
                    "variable": "pw",
                    "question_name": "Password",
                }
            ]
        },
        "verbosity": 3,
    }
    observed = {
        "survey_spec": {
            "spec": [
                {
                    "variable": "other",
                    "question_name": "Other",
                    "required": False,
                }
            ]
        },
        "verbosity": 3,
    }

    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        desired,
        observed,
        fields=("survey_spec", "verbosity"),
    ) == ("survey_spec",)


def test_verifier_parses_desired_structured_string() -> None:
    verifier = ApplyVerifier()

    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        {"extra_vars": "wanted: true\n"},
        {"extra_vars": '{"wanted": true, "server_added": "ok"}'},
        fields=("extra_vars",),
    ) == ("extra_vars",)


def test_verifier_parses_yaml_strings_on_both_sides() -> None:
    verifier = ApplyVerifier()

    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        {"extra_vars": "nested:\n  enabled: true\n"},
        {"extra_vars": "nested:\n  enabled: true\n  server_added: ok\n"},
        fields=("extra_vars",),
    ) == ("extra_vars",)


def test_verifier_keeps_scalar_comparison_strict() -> None:
    verifier = ApplyVerifier()

    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        {"enabled": True, "choice": "x"},
        {"enabled": "true", "choice": ["x"]},
        fields=("enabled", "choice"),
    ) == ("enabled", "choice")


def test_verifier_treats_lists_as_exact_ordered_replacements() -> None:
    verifier = ApplyVerifier()

    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        {"values": [{"name": "b"}, {"name": "a"}], "empty": []},
        {"values": [{"name": "a"}, {"name": "b"}], "empty": []},
        fields=("values", "empty"),
    ) == ("values",)
    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        {"values": [{"name": "a"}], "empty": []},
        {"values": [{"name": "a"}, {"name": "b"}], "empty": [1]},
        fields=("values", "empty"),
    ) == ("values", "empty")


def test_verifier_treats_dict_desired_as_replacement() -> None:
    verifier = ApplyVerifier()

    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        {"settings": {"wanted": {"enabled": True}}},
        {"settings": {"wanted": {"enabled": True, "defaulted": False}, "extra": "ok"}},
        fields=("settings",),
    ) == ("settings",)


def test_verifier_currently_requires_none_keys_to_be_present() -> None:
    """Documents current strict behavior; revisit if live AWX omits nulls."""

    verifier = ApplyVerifier()

    assert (
        verifier.unreflected_fields(
            JOB_TEMPLATE_SPEC,
            {"timeout": None},
            {"timeout": None},
            fields=("timeout",),
        )
        == ()
    )
    assert verifier.unreflected_fields(
        JOB_TEMPLATE_SPEC,
        {"timeout": None},
        {},
        fields=("timeout",),
    ) == ("timeout",)

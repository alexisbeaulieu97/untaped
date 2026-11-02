"""Composition checks what each plugin requires, from its metadata, before importing it.

Requirements on ``untaped`` and on other plugins are compared by version; any
other requirement by presence. A plugin requiring a disabled plugin is
disabled too. Its commands then exit 4, saying why, instead of "unknown command".
"""

from __future__ import annotations

import pytest

import untaped.runtime.registry as registry
from core_test_support.commands.support import make_plugin
from core_test_support.runtime.plugin_harness import make_candidate
from untaped.runtime import bootstrap
from untaped.runtime.registry import CompositionResult, PluginCandidate, compose
from untaped.sdk.testing import CliInvoker


def _base() -> PluginCandidate:
    """``untaped-base`` 2.0.0, a plugin others require."""
    return make_candidate(make_plugin("base"), "untaped-base", distribution_version="2.0.0")


def _acme(*requires: str, calls: list[str] | None = None) -> PluginCandidate:
    """``untaped-acme`` 1.0.0, requiring ``requires``."""
    return make_candidate(
        make_plugin("acme"),
        "untaped-acme",
        distribution_version="1.0.0",
        requires_dist=requires,
        calls=calls,
    )


def _composed(result: CompositionResult) -> list[str]:
    return [plugin.name for plugin in result.plugins]


@pytest.mark.parametrize(
    "requirement",
    [
        "untaped-base>=1",
        "untaped-base>=2.0.0,!=1.5",
        "untaped-base>=9; extra == 'more'",
        "untaped-base>=9; python_version < '3'",
        "untaped-base @ https://example.com/untaped_base-9.0-py3-none-any.whl",
        "pydantic>=99",  # a library: only its presence is read
    ],
    ids=["floor", "not-equal", "extra", "marker", "url", "library-version"],
)
def test_a_met_requirement_composes(requirement: str) -> None:
    result = compose([_base(), _acme(requirement)])
    assert _composed(result) == ["untaped", "acme", "base"]
    assert result.quarantine == ()


@pytest.mark.parametrize(
    ("requirement", "facts", "fix"),
    [
        (
            "untaped-base>=3",
            "untaped-acme 1.0.0 requires untaped-base>=3, but untaped-base 2.0.0 is installed",
            "upgrade untaped-base, or remove untaped-acme",
        ),
        (
            "untaped-base>=1,<2",
            "untaped-acme 1.0.0 requires untaped-base>=1,<2, but untaped-base 2.0.0 is installed",
            "upgrade untaped-acme, or remove it",
        ),
        (
            "untaped-base==1.0.0",
            "untaped-acme 1.0.0 requires untaped-base==1.0.0, but untaped-base 2.0.0 is installed",
            "upgrade untaped-acme to match untaped-base",
        ),
        (
            "untaped-base==3.0.0",
            "untaped-acme 1.0.0 requires untaped-base==3.0.0, but untaped-base 2.0.0 is installed",
            "upgrade untaped-base to match untaped-acme",
        ),
        (
            "untaped-base>=1,!=2.0.0",
            "untaped-acme 1.0.0 requires untaped-base>=1,!=2.0.0, but untaped-base 2.0.0 is "
            "installed",
            "install another release of untaped-base, or remove untaped-acme",
        ),
        (
            "untaped-nowhere>=1",
            "untaped-acme 1.0.0 requires untaped-nowhere>=1, which is not installed",
            "install untaped-nowhere, or remove untaped-acme",
        ),
        (
            "untaped-nowhere-lib",
            "untaped-acme 1.0.0 requires untaped-nowhere-lib, which is not installed",
            "install untaped-nowhere-lib, or remove untaped-acme",
        ),
    ],
    ids=["below-floor", "upper-limit", "pin-older", "pin-newer", "excluded", "plugin", "library"],
)
def test_an_unmet_requirement_quarantines_without_importing(
    requirement: str, facts: str, fix: str
) -> None:
    calls: list[str] = []
    result = compose([_base(), _acme(requirement, calls=calls)])
    assert _composed(result) == ["untaped", "base"]
    [record] = result.quarantine
    assert (record.name, record.reason, record.detail, record.fix) == (
        "acme",
        "unmet-requirement",
        f"{facts}; {fix}",
        fix,
    )
    assert calls == []  # read from metadata, never imported


def test_untaped_s_own_version_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.importlib_metadata, "version", lambda name: "12.2.0")
    [record] = compose([_acme("untaped>=12.3")]).quarantine
    assert record.detail == (
        "untaped-acme 1.0.0 requires untaped>=12.3, but untaped 12.2.0 is installed; "
        "upgrade untaped, or remove untaped-acme"
    )


@pytest.mark.parametrize(
    "requirement",
    ["untaped-base>=1; python_version ~= '3'", "untaped-base>=1; os_name ~= '1.0'"],
    ids=["python-version", "os-name"],
)
def test_a_marker_that_cannot_be_compared_is_bad_metadata(requirement: str) -> None:
    [record] = compose([_base(), _acme(requirement)]).quarantine
    assert record.reason == "bad-metadata"
    assert "malformed Requires-Dist" in record.detail


def test_a_plugin_requiring_a_disabled_plugin_is_disabled_two_levels_deep() -> None:
    lower = make_candidate(
        make_plugin("lower"),
        "untaped-lower",
        distribution_version="1.0",
        requires_dist=("untaped-gone>=1",),
    )
    middle = make_candidate(
        make_plugin("middle"),
        "untaped-middle",
        distribution_version="1.0",
        requires_dist=("untaped-lower>=1",),
    )
    upper = make_candidate(
        make_plugin("upper"),
        "untaped-upper",
        distribution_version="1.0",
        requires_dist=("untaped-middle>=1",),
    )
    result = compose([upper, middle, lower, _base()])
    assert _composed(result) == ["untaped", "base"]
    details = {record.name: record.detail for record in result.quarantine}
    assert details["middle"] == (
        "untaped-middle 1.0 requires untaped-lower, which is disabled [unmet-requirement]; "
        "fix untaped-lower first (install untaped-gone, or remove untaped-lower)"
    )
    assert details["upper"].startswith(
        "untaped-upper 1.0 requires untaped-middle, which is disabled [unmet-requirement]; "
        "fix untaped-middle first (fix untaped-lower first"
    )


def test_a_plugin_requiring_a_disabled_plugin_waits_for_it_however_it_was_refused() -> None:
    """The cascade follows a refusal found on import or later, not only from metadata."""
    broken = make_candidate(
        make_plugin("lower"),
        "untaped-lower",
        distribution_version="1.0",
        error=RuntimeError("lower is broken"),
    )
    raising = make_candidate(
        make_plugin("upper"),
        "untaped-upper",
        distribution_version="1.0",
        requires_dist=("untaped-lower>=1",),
        error=ImportError("cannot import name 'Thing' from 'untaped_lower.api'"),
    )
    sound = make_candidate(
        make_plugin("side"),
        "untaped-side",
        distribution_version="1.0",
        requires_dist=("untaped-lower>=1",),
    )
    result = compose([broken, raising, sound])
    records = {record.name: record for record in result.quarantine}
    assert records["lower"].reason == "import-failed"
    assert "RuntimeError: lower is broken" in records["lower"].detail
    for name in ("upper", "side"):
        assert records[name].reason == "unmet-requirement"
        assert records[name].detail.startswith(
            f"untaped-{name} 1.0 requires untaped-lower, which is disabled [import-failed]"
        )


def test_an_import_error_from_a_library_is_import_failed() -> None:
    missing = ModuleNotFoundError("No module named 'zzz_missing'", name="zzz_missing")
    acme = make_candidate(make_plugin("acme"), "untaped-acme", error=missing)
    [record] = compose([acme]).quarantine
    assert record.reason == "import-failed"
    assert record.detail.endswith("raised ModuleNotFoundError: No module named 'zzz_missing'")


def test_a_target_naming_a_missing_attribute_is_a_malformed_entry_point() -> None:
    gone = PluginCandidate(distribution="untaped-acme", name="acme", target="json:NoSuchThing")
    [record] = compose([gone]).quarantine
    assert record.reason == "malformed-entry-point"


def test_presence_is_read_once_per_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def present(name: str) -> bool:
        asked.append(name)
        return True

    monkeypatch.setattr(registry, "distribution_present", present)
    lib = "some-library>=1"
    compose([_acme(lib), make_candidate(make_plugin("bee"), "untaped-bee", requires_dist=(lib,))])
    assert asked == ["some-library"]


def test_running_a_disabled_plugin_s_command_exits_4_with_why() -> None:
    root = bootstrap.build_root_app(candidates=[_acme("untaped-nowhere-lib>=1")])
    result = CliInvoker().invoke(root.meta, ["acme", "anything", "--flag"])
    assert result.exit_code == 4, result.output
    assert (
        "error: acme is disabled [unmet-requirement]; "
        "install untaped-nowhere-lib, or remove untaped-acme"
    ) in result.stderr
    help_text = CliInvoker().invoke(root.meta, ["--help"]).stdout
    assert "acme" not in help_text  # a stand-in, kept out of help


def test_a_disabled_plugin_named_after_a_root_command_leaves_it_alone() -> None:
    taken = make_candidate(make_plugin("doctor"), "untaped-doctor", requires_dist=("untaped>=999",))
    root = bootstrap.build_root_app(candidates=[taken])
    assert root["doctor"].show  # core's command, not a stand-in

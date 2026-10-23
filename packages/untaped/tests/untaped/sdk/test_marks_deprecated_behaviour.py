"""Deprecated behaviours: the declaration, its owner, its three entry points and its line."""

from __future__ import annotations

import inspect
import json
import warnings
from collections.abc import Sequence
from dataclasses import replace

import pytest
from cyclopts import App

from core_test_support.plugins.plugin_harness import make_spec
from untaped.conventions.stability import stability_violations
from untaped.core.surface import DeprecatedMark, Line, plugin_lines
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginSpec
from untaped.sdk.diagnostic import DeprecationNotice
from untaped.sdk.errors import ConfigError
from untaped.sdk.marks import (
    DeprecatedBehaviour,
    UntapedDeprecationWarning,
    deprecated,
    deprecated_behaviour,
    forget_notices,
    marks,
)
from untaped.sdk.network.auth import PLAINTEXT_TOKEN
from untaped.sdk.testing import invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")

ROW_KIND = deprecated_behaviour("row-kind", removed_in=3, replacement="kind= on the Record class")
UNLISTED = deprecated_behaviour("unlisted", removed_in=3)


def _spec(
    *behaviours: DeprecatedBehaviour, stability: object = None, name: str = "svc"
) -> PluginSpec:
    base = make_spec(name, stability=stability)  # type: ignore[arg-type]
    return PluginSpec(
        name=base.name,
        app_factory=base.app_factory,
        settings=base.settings,
        stability=base.stability,
        deprecations=lambda: behaviours,
    )


def _compose(*specs: PluginSpec) -> App:
    return bootstrap.build_root_app(candidates=[plugin_candidate(spec) for spec in specs])


# --- the declaration -----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Plain", "plain_token", "-x", "x-", "", "a--b"])
def test_a_behaviour_s_name_is_kebab_case(name: str) -> None:
    with pytest.raises(TypeError, match="kebab-case"):
        deprecated_behaviour(name, removed_in=3)


def test_a_behaviour_names_its_major() -> None:
    with pytest.raises(TypeError, match="removed_in=12, not"):
        deprecated_behaviour("x", removed_in="3")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="names the major"):
        DeprecatedBehaviour("x", deprecated(removed_in=None))


def test_a_behaviour_s_replacement_is_text() -> None:
    with pytest.raises(TypeError, match="text or None"):
        DeprecatedBehaviour("x", deprecated(removed_in=3, replacement=print))


@pytest.mark.parametrize("value", [(), None])
def test_a_spec_s_deprecations_is_a_function(value: object) -> None:
    with pytest.raises(ConfigError, match="deprecations must be a function"):
        PluginSpec(name="svc", deprecations=value)  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="shell deprecations must be a function"):
        replace(bootstrap.SHELL_SPEC, deprecations=value)  # type: ignore[arg-type]


# --- the sentence and its owner ------------------------------------------------------------


def test_core_s_behaviour_names_the_bare_version() -> None:
    _compose()
    assert PLAINTEXT_TOKEN.id == "behaviour untaped.plaintext-token"
    subject = "support for a plain-text awx.token in config.yml"
    assert PLAINTEXT_TOKEN.sentence(subject) == (
        f"{subject} is deprecated and will be removed in 12.0; use `untaped auth migrate`"
    )
    assert PLAINTEXT_TOKEN.sentence(subject, replacement="untaped auth set awx").endswith(
        "; use `untaped auth set awx`"
    )
    assert PLAINTEXT_TOKEN.sentence(subject, replacement=None) == (
        f"{subject} is deprecated and will be removed in 12.0"
    )


def test_a_plugin_s_behaviour_names_its_distribution() -> None:
    _compose(_spec(ROW_KIND))
    assert ROW_KIND.id == "behaviour svc.row-kind"
    assert ROW_KIND.sentence("a row without a declared kind") == (
        "a row without a declared kind is deprecated and will be removed in untaped-svc 3.0; "
        "use kind= on the Record class"
    )


def test_a_behaviour_nobody_lists_reads_as_core_s() -> None:
    _compose(_spec(ROW_KIND))
    assert UNLISTED.id == "behaviour untaped.unlisted"
    assert UNLISTED.sentence("x") == "x is deprecated and will be removed in 3.0"


def test_a_core_behaviour_stays_core_s_and_no_plugin_may_list_it() -> None:
    spec = _spec(PLAINTEXT_TOKEN, name="hijack")
    _compose(spec)
    assert PLAINTEXT_TOKEN.id == "behaviour untaped.plaintext-token"
    with pytest.raises(ConfigError, match="'plaintext-token', which untaped owns"):
        plugin_lines(spec)


def test_a_listing_that_fails_never_fails_the_warning() -> None:
    def broken() -> Sequence[DeprecatedBehaviour]:
        raise RuntimeError("boom")

    spec = PluginSpec(name="bad", deprecations=broken)
    _compose(spec, _spec(ROW_KIND))
    assert ROW_KIND.id == "behaviour svc.row-kind"


# --- notify and warn -----------------------------------------------------------------------


def _notifier(behaviour: DeprecatedBehaviour, *, hint: str | None = None) -> App:
    app = App(name="w")

    @app.default
    def run() -> None:
        behaviour.notify("a row without a declared kind", key="row", hint=hint)
        behaviour.notify("the same row again", key="row")

    return app


def test_notify_warns_once_per_key_as_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _compose(_spec(ROW_KIND))
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    forget_notices()
    result = invoke_cli(_notifier(ROW_KIND, hint="run `untaped svc fix`"), [])
    (line,) = [json.loads(line) for line in result.stderr.splitlines()]
    assert line == {
        "level": "warning",
        "kind": "untaped.deprecation",
        "id": "behaviour svc.row-kind",
        "removed_in": 3,
        "distribution": "untaped-svc",
        "replacement": "kind= on the Record class",
        "hint": "run `untaped svc fix`",
        "message": "a row without a declared kind is deprecated and will be removed in "
        "untaped-svc 3.0; use kind= on the Record class",
    }
    assert isinstance(result.diagnostics[0], DeprecationNotice)


def _api() -> None:
    """A Python API whose behaviour is deprecated: the warning points at its caller."""
    ROW_KIND.warn("a row without a declared kind", replacement="untaped svc set")


def test_each_behaviour_keeps_its_own_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _compose(_spec(ROW_KIND))
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    forget_notices()
    app = App(name="w")

    @app.default
    def run() -> None:
        ROW_KIND.notify("a row", key="same")
        UNLISTED.notify("a thing", key="same")

    result = invoke_cli(app, [])
    ids = [json.loads(line)["id"] for line in result.stderr.splitlines()]
    assert ids == ["behaviour svc.row-kind", "behaviour untaped.unlisted"]


def test_warn_raises_an_untaped_deprecation_warning_at_the_api_s_caller() -> None:
    _compose(_spec(ROW_KIND))
    with warnings.catch_warnings(record=True) as log:
        warnings.simplefilter("always")
        frame = inspect.currentframe()
        before = frame.f_lineno if frame is not None else 0
        _api()
    (shown,) = log
    assert (shown.filename, shown.lineno) == (__file__, before + 1)
    warning = shown.message
    assert isinstance(warning, UntapedDeprecationWarning)
    assert shown.filename == __file__
    assert (warning.id, warning.removed_in, warning.distribution, warning.replacement) == (
        "behaviour svc.row-kind",
        3,
        "untaped-svc",
        "untaped svc set",
    )
    assert str(warning).endswith("; use `untaped svc set`")


# --- the surface line and the marks --------------------------------------------------------


def test_a_plugin_s_behaviour_is_a_surface_line() -> None:
    lines = plugin_lines(_spec(ROW_KIND))
    assert Line("behaviour svc.row-kind", DeprecatedMark(3, "kind= on the Record class")) in lines
    assert not any(line.identity.startswith("behaviour untaped.") for line in lines)


@pytest.mark.parametrize(
    ("listed", "message"),
    [
        ((ROW_KIND, ROW_KIND), "lists 'row-kind' twice"),
        (("row-kind",), "not a deprecated_behaviour"),
    ],
)
def test_a_bad_listing_is_refused_where_it_is_read(
    listed: tuple[object, ...], message: str
) -> None:
    spec = PluginSpec(name="svc", deprecations=lambda: listed)  # type: ignore[arg-type, return-value]
    with pytest.raises(ConfigError, match=message):
        plugin_lines(spec)


def test_marks_report_behaviours() -> None:
    root = _compose(_spec(ROW_KIND))
    found = {
        mark.where: mark.replacement
        for mark in marks(root, bootstrap.composition())
        if mark.target == "behaviour"
    }
    assert found == {
        "untaped.connected-client-by-name": "base_url= and bearer= values",
        "untaped.plaintext-token": "`untaped auth migrate`",
        "untaped.unmarked-service": (
            "a NeedsSetup-marked CredentialKey setting and a declared Credential"
        ),
        "svc.row-kind": "kind= on the Record class",
    }


def test_a_behaviour_under_a_deprecated_plugin_is_nested() -> None:
    spec = _spec(ROW_KIND, stability=deprecated(removed_in=3))
    root = _compose(spec)
    found = stability_violations(root, bootstrap.composition(), ["svc"], spec=spec)
    assert "svc.row-kind::nested-mark::deprecated under deprecated svc" in found


def test_core_s_behaviours_are_checked_with_core(monkeypatch: pytest.MonkeyPatch) -> None:
    gone = deprecated_behaviour("gone", removed_in=12, replacement="untaped auth nowhere")
    shell = replace(bootstrap.SHELL_SPEC, deprecations=lambda: (gone,))
    monkeypatch.setattr(bootstrap, "SHELL_SPEC", shell)
    root = _compose()
    found = stability_violations(root, bootstrap.composition(), [], sections=["shell"])
    assert found == [
        "untaped.gone::bad-replacement::`untaped auth nowhere` does not resolve to a command"
    ]


def test_a_behaviour_s_replacement_must_resolve() -> None:
    gone = deprecated_behaviour("gone", removed_in=3, replacement="untaped svc nowhere")
    spec = _spec(gone)
    root = _compose(spec)
    found = stability_violations(root, bootstrap.composition(), ["svc"], spec=spec)
    assert found == [
        "svc.gone::bad-replacement::`untaped svc nowhere` does not resolve to a command"
    ]

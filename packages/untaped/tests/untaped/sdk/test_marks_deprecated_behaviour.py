"""Deprecated behaviours: the declaration, its owner, its three entry points and its line."""

from __future__ import annotations

import inspect
import json
import warnings
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel

import untaped.sdk.marks as mark_module
from core_test_support.commands.support import make_plugin
from core_test_support.runtime.run import run_owning
from untaped.core.plugin_check.stability import stability_violations
from untaped.core.surface import DeprecatedMark, Line, plugin_lines
from untaped.runtime import bootstrap
from untaped.sdk import Command, Plugin
from untaped.sdk._run import run_scope
from untaped.sdk.marks import (
    DeprecatedBehaviour,
    UntapedDeprecationWarning,
    deprecated,
    forget_notices,
    marks,
)
from untaped.sdk.testing import install, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")

UNLISTED = DeprecatedBehaviour("unlisted", deprecated(removed_in=3))
CORE = DeprecatedBehaviour("old-login", deprecated(removed_in=12, replacement="untaped auth login"))


@pytest.fixture
def _core_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core lists :data:`CORE` as its own behaviour."""
    monkeypatch.setattr(mark_module, "_core_behaviours", lambda: (CORE,))


def _svc(stability: object = None, **behaviours: DeprecatedBehaviour) -> type[Plugin]:
    """Plugin ``svc`` holding ``behaviours`` (``row_kind`` without any)."""
    held = behaviours or {
        "row_kind": DeprecatedBehaviour(removed_in=3, replacement="kind= on the Record class")
    }
    return make_plugin(
        "svc",
        stability=stability,  # type: ignore[arg-type]
        commands=(Warn,),
        **held,
    )


def _row_kind(svc: type[Plugin]) -> DeprecatedBehaviour:
    behaviour = svc.row_kind  # type: ignore[attr-defined]
    assert isinstance(behaviour, DeprecatedBehaviour)
    return behaviour


def _compose(*plugins: type[Plugin]) -> App:
    return bootstrap.build_root_app(candidates=[plugin_candidate(each) for each in plugins])


# --- the declaration -----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Plain", "plain_token", "-x", "x-", "", "a--b"])
def test_a_behaviour_s_name_is_kebab_case(name: str) -> None:
    with pytest.raises(TypeError, match="kebab-case"):
        DeprecatedBehaviour(name, deprecated(removed_in=3))


def test_a_behaviour_names_its_major() -> None:
    with pytest.raises(TypeError, match="removed_in=13, not"):
        DeprecatedBehaviour("x", deprecated(removed_in="3"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="names the major"):
        DeprecatedBehaviour("x", deprecated(removed_in=None))


def test_a_behaviour_s_replacement_is_text() -> None:
    with pytest.raises(TypeError, match="text or None"):
        DeprecatedBehaviour("x", deprecated(removed_in=3, replacement=print))


class _Settings(BaseModel):
    pass


def test_a_plugin_class_names_its_behaviour(tmp_path: Path) -> None:
    class Demo(Plugin):
        """Demo."""

        settings = _Settings
        bare_ids = DeprecatedBehaviour(removed_in=13, replacement="--id NAME")

    behaviour = Demo.bare_ids
    assert (behaviour.name, behaviour.spelled, behaviour.module) == (
        "bare-ids",
        False,
        __name__,
    )
    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")):
        assert behaviour.id == "behaviour demo.bare-ids"
        assert behaviour.sentence("a plain token").startswith(
            "a plain token is deprecated and will be removed in"
        )
    assert repr(behaviour).endswith("removed_in=13)")


def test_a_behaviour_takes_its_name_and_mark_together() -> None:
    with pytest.raises(TypeError, match="its name and mark together"):
        DeprecatedBehaviour("x")
    with pytest.raises(TypeError, match="mark already says"):
        DeprecatedBehaviour("x", deprecated(removed_in=3), removed_in=4)
    with pytest.raises(TypeError, match="names the major"):
        DeprecatedBehaviour(replacement="y")


# --- the sentence and its owner ------------------------------------------------------------


@pytest.mark.usefixtures("_core_behaviour")
def test_core_s_behaviour_names_the_bare_version() -> None:
    _compose()
    assert CORE.id == "behaviour untaped.old-login"
    subject = "logging in the old way"
    assert CORE.sentence(subject) == (
        f"{subject} is deprecated and will be removed in 12.0; use `untaped auth login`"
    )
    assert CORE.sentence(subject, replacement="untaped auth login awx").endswith(
        "; use `untaped auth login awx`"
    )
    assert CORE.sentence(subject, replacement=None) == (
        f"{subject} is deprecated and will be removed in 12.0"
    )


def test_a_plugin_s_behaviour_names_its_distribution() -> None:
    svc = _svc()
    with install(svc).running():
        row_kind = _row_kind(svc)
        assert row_kind.id == "behaviour svc.row-kind"
        assert row_kind.sentence("a row without a declared kind") == (
            "a row without a declared kind is deprecated and will be removed in untaped-svc 3.0; "
            "use kind= on the Record class"
        )


def test_a_behaviour_nobody_lists_reads_as_core_s() -> None:
    with install(_svc()).running():
        assert UNLISTED.id == "behaviour untaped.unlisted"
        assert UNLISTED.sentence("x") == "x is deprecated and will be removed in 3.0"


# --- notify and warn -----------------------------------------------------------------------


#: What ``svc warn`` notifies: (behaviour, subject, key, hint).
NOTICES: list[tuple[DeprecatedBehaviour, str, str, str | None]] = []


class Warn(Command):
    """Notify each of :data:`NOTICES`."""

    def run(self) -> None:
        for behaviour, subject, key, hint in NOTICES:
            behaviour.notify(subject, key=key, hint=hint)


def _warned(svc: type[Plugin], *notices: tuple[DeprecatedBehaviour, str, str, str | None]) -> str:
    """``svc warn``'s JSON diagnostics after it notifies ``notices``."""
    NOTICES[:] = notices
    forget_notices()
    result = invoke_cli(_compose(svc).meta, ["svc", "warn"])
    assert result.exit_code == 0, result.output
    return result.stderr


def test_notify_warns_once_per_key_as_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    svc = _svc()
    row_kind = _row_kind(svc)
    stderr = _warned(
        svc,
        (row_kind, "a row without a declared kind", "row", "run `untaped svc fix`"),
        (row_kind, "the same row again", "row", None),
    )
    (line,) = [json.loads(line) for line in stderr.splitlines()]
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


def _api(row_kind: DeprecatedBehaviour) -> None:
    """A Python API whose behaviour is deprecated: the warning points at its caller."""
    row_kind.warn("a row without a declared kind", replacement="untaped svc set")


def test_each_behaviour_keeps_its_own_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    svc = _svc()
    stderr = _warned(
        svc, (_row_kind(svc), "a row", "same", None), (UNLISTED, "a thing", "same", None)
    )
    ids = [json.loads(line)["id"] for line in stderr.splitlines()]
    assert ids == ["behaviour svc.row-kind", "behaviour untaped.unlisted"]


def test_warn_raises_an_untaped_deprecation_warning_at_the_api_s_caller() -> None:
    svc = _svc()
    with install(svc).running(), warnings.catch_warnings(record=True) as log:
        warnings.simplefilter("always")
        frame = inspect.currentframe()
        before = frame.f_lineno if frame is not None else 0
        _api(_row_kind(svc))
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
    lines = plugin_lines(plugin_candidate(_svc()))
    assert Line("behaviour svc.row-kind", DeprecatedMark(3, "kind= on the Record class")) in lines
    assert not any(line.identity.startswith("behaviour untaped.") for line in lines)


@pytest.mark.usefixtures("_core_behaviour")
def test_marks_report_behaviours() -> None:
    root = _compose(_svc())
    found = {
        mark.where: mark.replacement
        for mark in marks(root, bootstrap.composition())
        if mark.target == "behaviour"
    }
    assert found == {
        "untaped.old-login": "`untaped auth login`",
        "svc.row-kind": "kind= on the Record class",
    }


def _svc_violations(svc: type[Plugin]) -> list[str]:
    root = _compose(svc)
    result = bootstrap.composition()
    [composed] = [plugin for plugin in result.plugins if plugin.name == "svc"]
    return stability_violations(root, result, ["svc"], plugin=composed)


def test_a_behaviour_under_a_deprecated_plugin_is_nested() -> None:
    found = _svc_violations(_svc(stability=deprecated(removed_in=3)))
    assert "svc.row-kind::nested-mark::deprecated under deprecated svc" in found


def test_core_s_behaviours_are_checked_with_core(monkeypatch: pytest.MonkeyPatch) -> None:
    gone = DeprecatedBehaviour(
        "gone", deprecated(removed_in=12, replacement="untaped auth nowhere")
    )
    monkeypatch.setattr(mark_module, "_core_behaviours", lambda: (gone,))
    root = _compose()
    found = stability_violations(root, bootstrap.composition(), [], sections=["untaped"])
    assert found == [
        "untaped.gone::bad-replacement::`untaped auth nowhere` does not resolve to a command"
    ]


def test_a_behaviour_s_replacement_must_resolve() -> None:
    gone = DeprecatedBehaviour(removed_in=3, replacement="untaped svc nowhere")
    found = _svc_violations(_svc(gone=gone))
    assert found == [
        "svc.gone::bad-replacement::`untaped svc nowhere` does not resolve to a command"
    ]

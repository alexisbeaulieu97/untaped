"""The removal version: the marker, the sentences, the record and the warnings hook."""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from typing import ClassVar

import pytest
from cyclopts import App
from pydantic import BaseModel

from test_plugins.plugin_harness import make_spec
from untaped import bootstrap
from untaped.cache import Cache
from untaped.cli import create_app
from untaped.deprecated_keys import Renamed, Retired, mapping_errors
from untaped.errors import ConfigError
from untaped.messages import deprecated_ignored_message, deprecated_line, deprecated_message
from untaped.output import sink
from untaped.output.diagnostic import DeprecationNotice
from untaped.stability import (
    UntapedDeprecationWarning,
    check_removed_in,
    deprecated,
    deprecated_alias,
    removal_text,
)
from untaped.testing import CliInvoker, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


# --- the marker ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 12, None])
def test_removed_in_is_a_major(value: int | None) -> None:
    assert check_removed_in(value) == value
    assert deprecated(removed_in=value).removed_in == value


@pytest.mark.parametrize("value", ["12.0", "12", True, 0, -1, 12.0])
def test_removed_in_refuses_anything_but_a_major(value: object) -> None:
    with pytest.raises(TypeError, match="removed_in=12, not"):
        deprecated(removed_in=value)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="removed_in=12, not"):
        Renamed("new", removed_in=value)  # type: ignore[arg-type]


def test_renamed_takes_removed_in_by_keyword() -> None:
    with pytest.raises(TypeError):
        Renamed("new", 12)  # type: ignore[misc]


def test_a_cache_dates_only_a_rename() -> None:
    day = timedelta(days=1)
    with pytest.raises(ConfigError, match="removed_in dates renamed_from, which is empty"):
        Cache("acme", "index", label="index", max_age=day, removed_in=12)
    dated = Cache("acme", "index", label="index", max_age=day, renamed_from=("old",), removed_in=12)
    assert dated.removed_in == 12


def test_a_renamed_key_belongs_in_renamed_keys() -> None:
    class Section(BaseModel):
        renamed_keys: ClassVar[Mapping[str, object]] = {"older": Renamed("new", removed_in=12)}
        retired_keys: ClassVar[Mapping[str, object]] = {"old": Renamed("new", removed_in=12)}

        new: int = 0

    assert any(
        "a Renamed key is still read, so it belongs in renamed_keys" in error
        for error in mapping_errors(Section)
    )


def test_a_dated_rename_and_a_retired_key_validate() -> None:
    class Section(BaseModel):
        renamed_keys: ClassVar[Mapping[str, object]] = {"older": Renamed("new", removed_in=12)}
        retired_keys: ClassVar[Mapping[str, object]] = {"oldest": Retired(note="gone")}

        new: int = 0

    assert mapping_errors(Section) == []


# --- the sentences -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("replacement", "removal", "expected"),
    [
        (None, None, "`x` is deprecated and will be removed in a future major release"),
        ("`y`", None, "`x` is deprecated and will be removed in a future major release; use `y`"),
        (None, "12.0", "`x` is deprecated and will be removed in 12.0"),
        (
            "`y`",
            "untaped-awx 12.0",
            "`x` is deprecated and will be removed in untaped-awx 12.0; use `y`",
        ),
    ],
)
def test_the_deprecation_sentence(
    replacement: str | None, removal: str | None, expected: str
) -> None:
    assert deprecated_message("`x`", replacement, removal) == expected


def test_the_ignored_sentence_names_its_removal() -> None:
    assert deprecated_ignored_message("a.old", "a.new", "a.new", "12.0") == (
        "a.old is deprecated and ignored because a.new is also set; it is removed in 12.0"
    )
    assert deprecated_ignored_message("OLD", "new in BLOB", "NEW", None) == (
        "OLD is deprecated and ignored because new in BLOB is also set; use NEW; "
        "it is removed in a future major release"
    )


@pytest.mark.parametrize(
    ("replacement", "removal", "expected"),
    [
        (None, None, "Deprecated: removed in a future major release."),
        (
            "untaped b",
            "untaped-acme 3.0",
            "Deprecated: removed in untaped-acme 3.0; use untaped b.",
        ),
    ],
)
def test_the_help_line(replacement: str | None, removal: str | None, expected: str) -> None:
    assert deprecated_line(replacement, removal) == expected


@pytest.mark.parametrize(
    ("removed_in", "owner", "text"),
    [
        (12, None, "12.0"),
        (12, "untaped-awx", "untaped-awx 12.0"),
        (2, "untaped-acme", "untaped-acme 2.0"),
        (None, "untaped-acme", None),
    ],
)
def test_removal_text_names_whose_major(
    removed_in: int | None, owner: str | None, text: str | None
) -> None:
    assert removal_text(removed_in, owner) == text


# --- what a plugin's user sees -------------------------------------------------------------


def _svc() -> App:
    app = create_app(name="svc", help="Service.")

    @app.command(name="set")
    def set_command(*, follow: bool = False) -> None:
        """Set it."""

    @app.command(name="put")
    @deprecated(removed_in=3, replacement=set_command)
    def put_command() -> None:
        """Put it."""

    deprecated_alias(app, "assign", "set", removed_in=4)
    deprecated_alias(set_command_app(app), "-F", "--follow", removed_in=4)
    return app


def set_command_app(app: App) -> App:
    return app["set"]


def _root() -> App:
    app = _svc()
    spec = replace(make_spec(name="svc", factory=lambda: app))
    return bootstrap.build_root_app(candidates=[plugin_candidate(spec)])


def _json_notices(argv: list[str]) -> list[dict[str, object]]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("UNTAPED_DIAGNOSTICS", "json")
        result = CliInvoker().invoke(_root().meta, argv)
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stderr.splitlines()]
    return [line for line in lines if line["kind"] == "untaped.deprecation"]


def test_a_deprecated_command_names_its_plugin_s_major() -> None:
    (notice,) = _json_notices(["svc", "put"])
    assert notice == {
        "level": "warning",
        "kind": "untaped.deprecation",
        "id": "cli untaped svc put",
        "removed_in": 3,
        "distribution": "untaped-svc",
        "replacement": "untaped svc set",
        "hint": None,
        "message": "`untaped svc put` is deprecated and will be removed in untaped-svc 3.0; "
        "use `untaped svc set`",
    }


def test_the_help_line_carries_the_version() -> None:
    result = CliInvoker().invoke(_root().meta, ["svc", "put", "--help"])
    assert result.stdout.rstrip().endswith(
        "Deprecated: removed in untaped-svc 3.0; use untaped svc set."
    )


@pytest.mark.parametrize(
    ("argv", "identity", "message"),
    [
        (
            ["svc", "assign"],
            "cli untaped svc assign -> untaped svc set",
            "`untaped svc assign` is deprecated and will be removed in untaped-svc 4.0; "
            "use `untaped svc set`",
        ),
        (
            ["svc", "set", "-F"],
            "cli untaped svc set -F -> --follow",
            "`untaped svc set -F` is deprecated and will be removed in untaped-svc 4.0; "
            "use `untaped svc set --follow`",
        ),
    ],
)
def test_an_alias_names_both_full_paths(argv: list[str], identity: str, message: str) -> None:
    (notice,) = _json_notices(argv)
    assert (notice["id"], notice["removed_in"], notice["message"]) == (identity, 4, message)


# --- the warnings hook ---------------------------------------------------------------------


def _warn_from_a_command(warning: Warning) -> App:
    app = App(name="w")

    @app.default
    def run() -> None:
        warnings.warn(warning, stacklevel=1)

    return app


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python shows warnings as it does outside pytest's recording, every time."""
    monkeypatch.setattr(warnings, "_showwarnmsg_impl", sink._SHOW_UNRECORDED)
    monkeypatch.setattr(warnings, "filters", [])
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    warnings.simplefilter("always")


@pytest.mark.usefixtures("shown")
def test_an_untaped_deprecation_warning_arrives_as_its_record() -> None:
    warning = UntapedDeprecationWarning(
        "`old()` is deprecated",
        id="sdk untaped_acme.api.old()",
        removed_in=3,
        distribution="untaped-acme",
        replacement="new()",
    )
    result = invoke_cli(_warn_from_a_command(warning), [])

    (line,) = [json.loads(line) for line in result.stderr.splitlines()]
    assert line == {
        "level": "warning",
        "kind": "untaped.deprecation",
        "id": "sdk untaped_acme.api.old()",
        "removed_in": 3,
        "distribution": "untaped-acme",
        "replacement": "new()",
        "hint": None,
        "message": "`old()` is deprecated",
    }
    assert isinstance(result.diagnostics[0], DeprecationNotice)


@pytest.mark.usefixtures("shown")
def test_another_python_warning_is_a_warning_line_and_the_hook_ends_with_its_scope() -> None:
    before = warnings.showwarning
    result = invoke_cli(_warn_from_a_command(DeprecationWarning("plain")), [])

    (line,) = [json.loads(line) for line in result.stderr.splitlines()]
    assert line["kind"] == "untaped.message"
    assert line["level"] == "warning"
    assert str(line["message"]).startswith("DeprecationWarning: plain (")
    assert warnings.showwarning is before

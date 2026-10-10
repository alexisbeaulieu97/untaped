"""Experimental and deprecated marks: the markers, panels, help lines and the warning."""

from __future__ import annotations

import gc
import sys
import types
import warnings
from dataclasses import replace

import pytest
from cyclopts import App

from test_plugins.plugin_harness import make_spec
from untaped import bootstrap
from untaped.cli import create_app
from untaped.errors import ConfigError
from untaped.messages import EXPERIMENTAL_LINE
from untaped.plugins.registry import PluginCandidate, PluginSpec
from untaped.stability import (
    Deprecated,
    Experimental,
    apply_marks,
    check_stability,
    deprecated,
    experimental,
    function_mark,
    mark_of,
    replacement_path,
    replacement_text,
)
from untaped.testing import CliInvoker, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")

_NEXT_MAJOR = "is deprecated and will be removed in the next major release"


def _service(*, stability: Experimental | Deprecated | None = None) -> App:
    """A ``svc`` group: a stable command, a deprecated one naming it, an experimental one."""
    app = create_app(name="svc", help="Service.", stability=stability)

    @app.command(name="set")
    def set_command() -> None:
        """Set it."""

    @app.command(name="put")
    @deprecated(replacement=set_command)
    def put_command() -> None:
        """Put it."""

    @app.command(name="try")
    @experimental
    def try_command() -> None:
        """Try it."""

    return app


def _root(
    app: App, *, lazy: bool = False, stability: Experimental | Deprecated | None = None
) -> App:
    spec = make_spec(name=app.name[0], factory=lambda: app)
    spec = replace(spec, stability=stability, help="Service." if lazy else None)
    return bootstrap.build_root_app(candidates=[plugin_candidate(spec)])


def _help(root: App, *argv: str) -> str:
    result = CliInvoker().invoke(root.meta, [*argv, "--help"])
    assert result.exit_code == 0, result.output
    return result.stdout


def _panels(text: str) -> list[str]:
    return [line.strip("╭─ ").split(" ─")[0] for line in text.splitlines() if line.startswith("╭")]


# --- the markers -------------------------------------------------------------


def test_experimental_marks_a_function_and_returns_it_unchanged() -> None:
    def command() -> None: ...

    assert experimental(command) is command
    assert function_mark(command) is experimental


def test_a_bare_deprecated_decorator_fails_at_import() -> None:
    def command() -> None: ...

    with pytest.raises(TypeError):
        deprecated(command)  # type: ignore[call-arg, misc]


@pytest.mark.parametrize("replacement", [42, "", "  ", ["x"]])
def test_deprecated_rejects_a_replacement_that_is_not_an_object_or_text(
    replacement: object,
) -> None:
    with pytest.raises(TypeError, match="replacement"):
        deprecated(replacement=replacement)  # type: ignore[arg-type]


def test_deprecated_marks_a_function_and_returns_it_unchanged() -> None:
    def command() -> None: ...

    marked = deprecated(replacement="a shell alias")(command)

    assert marked is command
    assert function_mark(command) == Deprecated("a shell alias")


def test_the_markers_are_not_python_deprecation_markers() -> None:
    assert not isinstance(experimental, warnings.deprecated)
    assert not isinstance(deprecated(), warnings.deprecated)
    assert not issubclass(Deprecated, warnings.deprecated)
    assert not issubclass(Experimental, warnings.deprecated)


@pytest.mark.parametrize("wrong", [deprecated, warnings.deprecated("x"), "experimental", 1])
def test_a_wrong_stability_value_is_refused_by_create_app_and_the_spec(wrong: object) -> None:
    with pytest.raises(TypeError, match="stability must be experimental or deprecated"):
        create_app(name="x", stability=wrong)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        check_stability(wrong, where="here")
    with pytest.raises(ConfigError, match="stability must be experimental or deprecated"):
        replace(make_spec(name="x"), stability=wrong)  # type: ignore[arg-type]


def test_an_uncalled_deprecated_says_to_call_it() -> None:
    with pytest.raises(TypeError, match=r"call it: deprecated\(replacement=\.\.\.\)"):
        check_stability(deprecated, where="here")


def test_a_plugin_building_a_bad_spec_stability_is_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("bad_stability_plugin")

    def build(name: str) -> PluginSpec:
        return replace(make_spec(name="bad"), stability=deprecated)  # type: ignore[arg-type]

    module.__getattr__ = build  # type: ignore[method-assign]  # SPEC is built on first access
    monkeypatch.setitem(sys.modules, module.__name__, module)
    candidate = PluginCandidate(
        distribution="test-plugin", name="bad", target="bad_stability_plugin:SPEC"
    )

    composition = bootstrap.compose_root(candidates=[candidate])

    assert composition.plugins == ()
    assert [record.name for record in composition.quarantine] == ["bad"]
    assert "stability must be experimental or deprecated" in composition.quarantine[0].detail


def test_the_app_side_table_drops_an_entry_when_its_app_is_collected() -> None:
    from untaped import stability

    app = create_app(name="gone", stability=experimental)
    key = id(app)
    assert key in stability._APP_MARKS

    del app
    gc.collect()

    assert key not in stability._APP_MARKS


def test_mark_of_reads_the_app_mark_then_the_command_function() -> None:
    marked = create_app(name="marked", stability=experimental)
    assert mark_of(marked) is experimental
    assert mark_of(App(name="plain")) is None


# --- panels and help lines ----------------------------------------------------


def test_marked_commands_are_listed_in_their_stability_panels() -> None:
    app = _service()
    root = _root(app)

    text = _help(root, "svc")

    assert _panels(text) == ["Commands", "Experimental", "Parameters"]
    assert "Put it." not in text  # the Deprecated panel is hidden without --deprecated


def test_the_deprecated_panel_shows_with_the_flag_and_sorts_before_parameters() -> None:
    app = _service()
    root = _root(app)

    for argv in (["--deprecated", "svc", "--help"], ["svc", "--deprecated", "--help"]):
        text = CliInvoker().invoke(root.meta, argv).stdout
        assert _panels(text) == ["Commands", "Experimental", "Deprecated", "Parameters"], argv
        assert "Put it." in text


def test_each_marked_command_help_ends_with_its_line() -> None:
    app = _service()
    root = _root(app)

    assert _help(root, "svc", "try").rstrip().endswith(EXPERIMENTAL_LINE)
    assert (
        _help(root, "svc", "put")
        .rstrip()
        .endswith("Deprecated: removed in the next major release; use untaped svc set.")
    )
    assert EXPERIMENTAL_LINE not in _help(root, "svc", "set")


def test_applying_marks_twice_writes_each_line_once() -> None:
    app = _service()
    apply_marks(app, path=("svc",))
    apply_marks(app, path=("svc",))

    assert app["try"].help_epilogue == EXPERIMENTAL_LINE
    assert (app["put"].help_epilogue or "").count("Deprecated:") == 1


def test_an_author_epilogue_inside_a_marked_subtree_still_ends_with_the_line() -> None:
    group = create_app(name="grp", help="Group.", stability=experimental)
    group.help_epilogue = "See the manual."
    inner = App(name="inner", help="Inner.", help_epilogue="Inner note.")
    group.command(inner)

    @inner.default
    def _run() -> None:
        """Run."""

    apply_marks(group, path=("grp",))
    apply_marks(group, path=("grp",))

    assert group.help_epilogue == f"See the manual.\n\n{EXPERIMENTAL_LINE}"
    assert inner.help_epilogue == f"Inner note.\n\n{EXPERIMENTAL_LINE}"


def test_an_experimental_group_marks_every_help_below_it() -> None:
    group = create_app(name="grp", help="Group.", stability=experimental)

    @group.command(name="leaf")
    def leaf() -> None:
        """Leaf."""

    root = _root(group)

    assert _help(root, "grp", "leaf").rstrip().endswith(EXPERIMENTAL_LINE)
    assert _help(root, "grp").rstrip().endswith(EXPERIMENTAL_LINE)


def test_apply_marks_leaves_an_unresolved_lazy_plugin_unresolved() -> None:
    app = _service(stability=None)
    root = _root(app, lazy=True, stability=experimental)
    lazy = root._get_item("svc", recurse_meta=True)
    assert not lazy.is_resolved

    apply_marks(root)
    text = CliInvoker().invoke(root.meta, ["--help"]).stdout

    assert not lazy.is_resolved
    assert "Experimental" in _panels(text)
    assert "svc" in text.split("Experimental")[1]


@pytest.mark.parametrize("stability", [experimental, deprecated(replacement="something else")])
def test_a_marked_plugin_renders_the_same_lazy_and_eager(
    stability: Experimental | Deprecated,
) -> None:
    for argv in (
        ["--help"],
        ["--deprecated", "--help"],
        ["svc", "--help"],
        ["svc", "set", "--help"],
    ):
        lazy_app = _service()
        eager_app = _service()
        lazy = CliInvoker().invoke(_root(lazy_app, lazy=True, stability=stability).meta, argv)
        eager = CliInvoker().invoke(
            _root(eager_app, lazy=False, stability=stability).meta,
            argv,
        )
        assert (lazy.exit_code, lazy.stdout) == (eager.exit_code, eager.stdout), argv


def test_a_marked_plugin_lists_in_its_panel_and_its_help_ends_with_the_line() -> None:
    app = _service()
    root = _root(app, stability=experimental)

    listing = CliInvoker().invoke(root.meta, ["--help"]).stdout

    assert _panels(listing) == ["Commands", "Experimental", "Parameters"]
    assert _help(root, "svc", "set").rstrip().endswith(EXPERIMENTAL_LINE)


# --- replacements -------------------------------------------------------------


def test_replacement_path_finds_a_command_function_or_app_by_identity() -> None:
    app = _service()
    root = _root(app)
    set_command = app["set"].default_command

    assert replacement_path(root, set_command) == ("untaped", "svc", "set")
    assert replacement_path(root, app) == ("untaped", "svc")
    assert replacement_path(root, lambda: None) is None


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("untaped github sync", "`untaped github sync`"),
        ("awx.test_timeout_seconds", "`awx.test_timeout_seconds`"),
        ("a shell alias or function", "a shell alias or function"),
    ],
)
def test_text_replacements_are_backticked_when_they_name_a_command_or_a_key(
    text: str, shown: str
) -> None:
    assert replacement_text(deprecated(replacement=text), None) == shown


def test_no_replacement_gives_no_text() -> None:
    assert replacement_text(deprecated(), None) is None


# --- the warning ---------------------------------------------------------------


def test_a_deprecated_command_warns_once_naming_its_replacement() -> None:
    app = _service()
    root = _root(app)

    result = CliInvoker().invoke(root.meta, ["svc", "put"])

    assert result.exit_code == 0, result.output
    assert result.stderr.count("warning:") == 1
    assert f"warning: `untaped svc put` {_NEXT_MAJOR}; use `untaped svc set`\n" in result.stderr


def test_the_warning_follows_a_renamed_replacement() -> None:
    app = create_app(name="svc", help="Service.")

    @app.command(name="assign")  # the replacement was renamed from "set"
    def renamed() -> None:
        """Assign it."""

    @app.command(name="put")
    @deprecated(replacement=renamed)
    def put_command() -> None:
        """Put it."""

    result = CliInvoker().invoke(_root(app).meta, ["svc", "put"])

    assert f"`untaped svc put` {_NEXT_MAJOR}; use `untaped svc assign`" in result.stderr


def test_without_a_replacement_the_sentence_ends_at_the_next_major_release() -> None:
    app = create_app(name="svc", help="Service.")

    @app.command(name="old")
    @deprecated()
    def old() -> None:
        """Old."""

    root = _root(app)

    assert (
        f"`untaped svc old` {_NEXT_MAJOR}\n"
        in CliInvoker().invoke(root.meta, ["svc", "old"]).stderr
    )
    assert (
        _help(root, "--deprecated", "svc", "old")
        .rstrip()
        .endswith("Deprecated: removed in the next major release.")
    )


def test_a_replacement_missing_at_run_time_falls_back_to_the_plain_sentence() -> None:
    app = create_app(name="svc", help="Service.")

    def never_mounted() -> None: ...

    @app.command(name="old")
    @deprecated(replacement=never_mounted)
    def old() -> None:
        """Old."""

    result = CliInvoker().invoke(_root(app).meta, ["svc", "old"])

    assert result.exit_code == 0, result.output
    assert f"`untaped svc old` {_NEXT_MAJOR}\n" in result.stderr


def test_the_innermost_deprecated_node_on_the_chain_wins() -> None:
    app = create_app(name="svc", help="Service.", stability=deprecated(replacement="elsewhere"))

    @app.command(name="inner")
    @deprecated(replacement="untaped svc other")
    def inner() -> None:
        """Inner."""

    root = _root(app)

    stderr = CliInvoker().invoke(root.meta, ["svc", "inner"]).stderr
    assert f"`untaped svc inner` {_NEXT_MAJOR}; use `untaped svc other`" in stderr
    assert stderr.count("warning:") == 1


def test_help_and_version_print_no_deprecation_warning() -> None:
    app = _service()
    root = _root(app)

    for argv in (["svc", "put", "--help"], ["svc", "put", "-h"], ["--version"]):
        assert "deprecated" not in CliInvoker().invoke(root.meta, argv).stderr, argv


def test_a_command_that_is_not_deprecated_prints_no_warning() -> None:
    app = _service()

    assert CliInvoker().invoke(_root(app).meta, ["svc", "set"]).stderr == ""


# --- invoke_cli ---------------------------------------------------------------


def test_invoke_cli_gives_a_directly_invoked_app_its_marks() -> None:
    app = _service()

    text = CliInvoker().invoke(app, ["put", "--help"]).stdout

    assert text.rstrip().endswith(
        "Deprecated: removed in the next major release; use untaped svc set."
    )


def test_invoke_cli_on_the_root_never_resolves_a_lazy_plugin() -> None:
    app = _service()
    root = _root(app, lazy=True, stability=experimental)
    lazy = root._get_item("svc", recurse_meta=True)

    CliInvoker().invoke(root.meta, ["--help"])
    CliInvoker().invoke(root, ["--help"])

    assert not lazy.is_resolved

"""Every deprecation names its removal, and an SDK callable's two marks agree."""

from __future__ import annotations

from core_test_support.plugin_check.support import Install
from untaped.core.plugin_check.rules import core_plugin_violations
from untaped.runtime.registry import PluginCandidate

_API = '''
    """Acme's Python API."""

    from __future__ import annotations

    import warnings
    from typing import Annotated

    from acme_lib import borrowed
    from untaped.sdk import deprecated, deprecated_parameters

    __all__ = [
        "Box",
        "Client",
        "Crate",
        "borrowed",
        "clean",
        "drifted",
        "old",
        "optional",
        "param",
        "typed",
        "undated",
        "warned",
    ]


    @deprecated(removed_in=3, replacement="clean")
    @warnings.deprecated("clean is deprecated and will be removed in untaped-acme 3.0; use clean")
    def clean() -> None:
        """Gone at 3.0."""


    @deprecated(removed_in=3)
    @warnings.deprecated("drifted is deprecated and will be removed in 3.0")
    def drifted() -> None:
        """Its literal forgot the distribution."""


    @warnings.deprecated("old is deprecated")
    def old() -> None:
        """Python's mark alone."""


    @deprecated(removed_in=3)
    def typed() -> None:
        """untaped's mark alone."""


    @deprecated()
    @warnings.deprecated("undated is deprecated and will be removed in a future major release")
    def undated() -> None:
        """No date."""


    def param(x: Annotated[int, deprecated(removed_in=3)] = 0) -> None:
        """A marked parameter nothing warns about."""


    @deprecated_parameters
    def warned(x: Annotated[int, deprecated(removed_in=3)] = 0) -> None:
        """A marked parameter that warns."""


    def optional(*, x: Annotated[int, deprecated(removed_in=3)] | None = None) -> None:
        """A mark inside a union, nothing warning about it."""


    class Box:
        """Its constructor warns."""

        @deprecated_parameters
        def __init__(self, old: Annotated[bool, deprecated(removed_in=3)] = False) -> None:
            self.old = old


    class Crate:
        """Its constructor never warns."""

        def __init__(self, old: Annotated[bool, deprecated(removed_in=3)] = False) -> None:
            self.old = old


    class Client:
        """A client."""

        @classmethod
        @deprecated(removed_in=3)
        def build(cls) -> None:
            """untaped's mark alone, under the descriptor."""
'''

_PLUGIN = {
    "untaped_acme/__init__.py": '''
        """Acme."""

        from collections.abc import Mapping
        from datetime import timedelta
        from typing import Annotated, ClassVar

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import Cache, PluginSpec, create_app, deprecated, deprecated_alias

        def _app():
            app = create_app(name="acme", help="Acme.")

            @app.command(name="put")
            @deprecated()
            def put() -> None:
                """Put it."""

            @app.command(name="set")
            def set_() -> None:
                """Set it."""

            deprecated_alias(app, "store", "set")
            return app

        class AcmeSettings(BaseModel):
            model_config = ConfigDict(frozen=True)
            renamed_keys: ClassVar[Mapping[str, object]] = {"old_wait": "wait"}

            wait: int = 5
            old_mode: Annotated[bool, deprecated()] = False

        INDEX = Cache(
            "acme", "index", label="index", max_age=timedelta(hours=1), renamed_from=("stale",)
        )

        SPEC = PluginSpec(name="acme", app_factory=_app, settings=AcmeSettings, caches=(INDEX,))
        ''',
    "acme_lib/__init__.py": '''
        """Another library's deprecation, re-exported by acme's API."""

        import warnings

        @warnings.deprecated("borrowed is deprecated")
        def borrowed() -> None:
            """Not acme's to date."""
        ''',
    "untaped_acme/errors.py": '"""Errors."""\n',
    "untaped_acme/api.py": _API,
}


_UNREADABLE = '''

    class Runner:
        """Its mark can't be evaluated."""

        @deprecated_parameters
        def run(self, *, old: Annotated[bool, deprecated(removed_in=3, replacement=hlp)] = 0):
            """Its mark names a name the module doesn't have."""
'''


def _rows(install: Install, plugin: dict[str, str] = _PLUGIN) -> list[str]:
    install(plugin)
    candidate = PluginCandidate(
        distribution="untaped-acme", name="acme", target="untaped_acme:SPEC"
    )
    rules = ("undated-deprecation", "untyped-deprecation", "deprecation-text", "unwarned-parameter")
    found = core_plugin_violations("acme", candidates=[candidate])
    return [line for line in found if any(f"::{rule}::" in line for rule in rules)]


def test_each_rule_reads_a_plugin_s_api_and_its_marks(install: Install) -> None:
    undated = "every deprecation is a promise with a date: add removed_in=<major> to its mark"
    unwarned = (
        "is marked deprecated but nothing warns when it is passed; add @deprecated_parameters"
    )
    assert _rows(install) == [
        f"cfg acme.old_mode boolean default=false::undated-deprecation::{undated}",
        f"cfg acme.old_wait -> acme.wait::undated-deprecation::{undated}",
        f"cfg acme.stale -> acme.caches.index.max_age_seconds::undated-deprecation::{undated}",
        f"cli untaped acme put::undated-deprecation::{undated}",
        f"cli untaped acme store -> untaped acme set::undated-deprecation::{undated}",
        "untaped_acme.api.Client.build::untyped-deprecation::untaped's mark alone is invisible "
        'to type checkers; add @warnings.deprecated("Client.build is deprecated and will be '
        'removed in untaped-acme 3.0") under it',
        f"untaped_acme.api.Crate.__init__::unwarned-parameter::old= {unwarned} to the function",
        "untaped_acme.api.drifted::deprecation-text::its warnings.deprecated text is not its "
        'mark\'s; use "drifted is deprecated and will be removed in untaped-acme 3.0"',
        "untaped_acme.api.old::undated-deprecation::carries warnings.deprecated and no untaped "
        "mark; add @deprecated(removed_in=<major>, replacement=...) above it so the surface "
        "dates it",
        f"untaped_acme.api.optional::unwarned-parameter::x= {unwarned} to the function",
        f"untaped_acme.api.param::unwarned-parameter::x= {unwarned} to the function",
        "untaped_acme.api.typed::untyped-deprecation::untaped's mark alone is invisible to type "
        'checkers; add @warnings.deprecated("typed is deprecated and will be removed in '
        'untaped-acme 3.0") under it',
        f"untaped_acme.api.undated::undated-deprecation::{undated}",
    ]


def test_a_mark_that_can_t_be_evaluated_is_reported_not_dropped(install: Install) -> None:
    api = _API.replace("__all__ = [\n", '__all__ = [\n        "Runner",\n') + _UNREADABLE
    rows = _rows(install, {**_PLUGIN, "untaped_acme/api.py": api})
    assert rows[0].startswith(
        "acme::undated-deprecation::its marks can't be read from its surface "
        "(untaped_acme.api.Runner.run(old): its annotation"
    )
    assert (
        "untaped_acme.api.Runner.run::unwarned-parameter::old=: its deprecated(...) mark can't "
        "be evaluated where the function is defined, so it never warns "
        "(deprecated(removed_in=3, replacement=hlp): NameError: name 'hlp' is not defined); "
        "name only module-level objects in it"
    ) in rows

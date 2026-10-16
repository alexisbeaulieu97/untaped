"""Every deprecation names its removal, and an SDK callable's two marks agree."""

from __future__ import annotations

from test_conventions.support import Install
from untaped.conventions import core_plugin_violations
from untaped.plugins.registry import PluginCandidate

_API = '''
    """Acme's Python API."""

    import warnings
    from typing import Annotated

    from untaped.sdk import deprecated, deprecated_parameters

    __all__ = ["Client", "clean", "drifted", "old", "param", "typed", "undated", "warned"]


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

        from untaped.sdk import PluginSpec, create_app, deprecated

        def _app():
            app = create_app(name="acme", help="Acme.")

            @app.command(name="put")
            @deprecated()
            def put() -> None:
                """Put it."""

            return app

        SPEC = PluginSpec(name="acme", app_factory=_app)
        ''',
    "untaped_acme/errors.py": '"""Errors."""\n',
    "untaped_acme/api.py": _API,
}


def _rows(install: Install) -> list[str]:
    install(_PLUGIN)
    candidate = PluginCandidate(
        distribution="untaped-acme", name="acme", target="untaped_acme:SPEC"
    )
    rules = ("undated-deprecation", "untyped-deprecation", "deprecation-text", "unwarned-parameter")
    found = core_plugin_violations("acme", candidates=[candidate])
    return [line for line in found if any(f"::{rule}::" in line for rule in rules)]


def test_each_rule_reads_a_plugin_s_api_and_its_marks(install: Install) -> None:
    undated = "every deprecation is a promise with a date: add removed_in=<major> to its mark"
    assert _rows(install) == [
        f"cli untaped acme put::undated-deprecation::{undated}",
        "untaped_acme.api.Client.build::untyped-deprecation::untaped's mark alone is invisible "
        'to type checkers; add @warnings.deprecated("Client.build is deprecated and will be '
        'removed in untaped-acme 3.0") under it',
        "untaped_acme.api.drifted::deprecation-text::its warnings.deprecated text is not its "
        'mark\'s; use "drifted is deprecated and will be removed in untaped-acme 3.0"',
        "untaped_acme.api.old::undated-deprecation::carries warnings.deprecated and no untaped "
        "mark; add @deprecated(removed_in=<major>, replacement=...) above it so the surface "
        "dates it",
        "untaped_acme.api.param::unwarned-parameter::x= is marked deprecated but nothing warns "
        "when it is passed; add @deprecated_parameters to the function",
        "untaped_acme.api.typed::untyped-deprecation::untaped's mark alone is invisible to type "
        'checkers; add @warnings.deprecated("typed is deprecated and will be removed in '
        'untaped-acme 3.0") under it',
        f"untaped_acme.api.undated::undated-deprecation::{undated}",
    ]

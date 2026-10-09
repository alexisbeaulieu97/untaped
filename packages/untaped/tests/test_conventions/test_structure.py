"""Structure rules on a tmp plugin package, one rule broken at a time."""

from __future__ import annotations

import importlib
from textwrap import dedent

import pytest
from cyclopts import App

from test_conventions.support import Install
from untaped.conventions.source import source_files
from untaped.conventions.structure import structure_violations
from untaped.plugins.registry import PluginSpec

_ERRORS = dedent('''
    """Acme errors."""

    from untaped.sdk import UntapedError


    class AcmeError(UntapedError):
        """An acme failure."""

        system = "acme"
    ''')
_SETTINGS = dedent('''
    """Acme settings."""

    from pydantic import BaseModel, ConfigDict


    class AcmeSettings(BaseModel):
        """Acme profile settings."""

        model_config = ConfigDict(frozen=True)


    class AcmeState(BaseModel):
        """Acme state."""

        model_config = ConfigDict(frozen=True)
    ''')
_CLEAN = {
    "acme/__init__.py": '"""Acme plugin."""\n',
    "acme/errors.py": _ERRORS,
    "acme/settings.py": _SETTINGS,
    "acme/cli.py": '''
        """Acme commands."""

        from untaped.sdk import get_config_section


        def settings() -> object:
            return get_config_section("acme")
        ''',
}


def _violations(install: Install, files: dict[str, str]) -> list[str]:
    """Structure violations of the package ``acme`` written from ``files``."""
    site = install(files)
    settings = importlib.import_module("acme.settings")
    spec = PluginSpec(
        name="acme",
        app_factory=App,
        settings=settings.AcmeSettings,
        state=settings.AcmeState,
    )
    source_dir = site / "acme"
    files_found = list(source_files(source_dir))
    return structure_violations(
        spec, "acme", source_dir, files_found, tests_dir=site / "acme_tests"
    )


def test_a_clean_package_has_no_violations(install: Install) -> None:
    assert _violations(install, _CLEAN) == []


def test_a_package_without_errors_module_is_flagged(install: Install) -> None:
    files = {name: body for name, body in _CLEAN.items() if name != "acme/errors.py"}
    assert _violations(install, files) == ["acme/errors.py::errors-module"]


@pytest.mark.parametrize(
    ("errors", "violation"),
    [
        (
            _ERRORS + '\n\nclass ParseError(ValueError):\n    """Bad input."""\n',
            "acme.errors.ParseError::exception-base",
        ),
        (
            _ERRORS + '\n\nclass Boom(AcmeError):\n    """Boom."""\n',
            "acme.errors.Boom::exception-name",
        ),
        (
            _ERRORS.replace('system = "acme"', "pass"),
            "acme.errors.AcmeError::error-system",
        ),
    ],
    ids=["exception-base", "exception-name", "error-system"],
)
def test_an_exception_rule_is_flagged(install: Install, errors: str, violation: str) -> None:
    assert _violations(install, {**_CLEAN, "acme/errors.py": errors}) == [violation]


def test_a_protocol_outside_the_ports_is_flagged(install: Install) -> None:
    files = {
        **_CLEAN,
        "acme/domain/__init__.py": "",
        "acme/domain/store.py": '''
            from typing import Protocol


            class Store(Protocol):
                """A store."""
            ''',
    }
    assert _violations(install, files) == ["acme.domain.store.Store::protocol-location"]


def test_a_port_and_an_adapter_sharing_a_name_are_flagged(install: Install) -> None:
    files = {
        **_CLEAN,
        "acme/application/__init__.py": "",
        "acme/application/ports.py": '''
            from typing import Protocol


            class Store(Protocol):
                """A store."""
            ''',
        "acme/infrastructure/__init__.py": "",
        "acme/infrastructure/store.py": '''
            class Store:
                """A file store."""
            ''',
    }
    assert _violations(install, files) == ["acme.application.ports.Store::port-adapter-clash"]


def test_reading_another_plugin_section_is_flagged(install: Install) -> None:
    cli = _CLEAN["acme/cli.py"].replace('"acme"', '"github"')
    assert _violations(install, {**_CLEAN, "acme/cli.py": cli}) == [
        "acme/cli.py::foreign-section::github"
    ]


def test_mutable_settings_models_are_flagged(install: Install) -> None:
    mutable = _SETTINGS.replace("model_config = ConfigDict(frozen=True)", "pass")
    assert _violations(install, {**_CLEAN, "acme/settings.py": mutable}) == [
        "acme.settings.AcmeSettings::settings-not-frozen::profile",
        "acme.settings.AcmeState::settings-not-frozen::state",
    ]


def test_tests_importing_private_names_are_flagged(install: Install) -> None:
    tests = {
        "acme_tests/test_acme.py": """
            import untaped._internal.tool
            from acme._parse import parse
            from untaped.sdk import _hidden
            from acme.cli import _helper  # untaped: allow private-test-import
            from other._private import thing
            from acme import __version__
            """,
    }
    assert _violations(install, {**_CLEAN, **tests}) == [
        "acme_tests/test_acme.py::private-test-import::untaped._internal.tool",
        "acme_tests/test_acme.py::private-test-import::acme._parse:_parse",
        "acme_tests/test_acme.py::private-test-import::untaped.sdk:_hidden",
    ]

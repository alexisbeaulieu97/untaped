"""Structure rules that import a tmp plugin package, one rule broken at a time.

The structure rules that only read its files are lint's, tested in ``untaped-lint``.
"""

from __future__ import annotations

import importlib
from textwrap import dedent

import pytest
from cyclopts import App

from core_test_support.conventions.support import Install
from untaped.conventions.structure import structure_class_violations, unimportable
from untaped.runtime.registry import PluginSpec

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
    """Structure class violations of the package ``acme`` written from ``files``."""
    site = install(files)
    settings = importlib.import_module("acme.settings")
    spec = PluginSpec(
        name="acme",
        app_factory=App,
        settings=settings.AcmeSettings,
        state=settings.AcmeState,
    )
    return structure_class_violations(spec, "acme", site / "acme")


def test_a_clean_package_has_no_violations(install: Install) -> None:
    assert _violations(install, _CLEAN) == []


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


def test_mutable_settings_models_are_flagged(install: Install) -> None:
    mutable = _SETTINGS.replace("model_config = ConfigDict(frozen=True)", "pass")
    assert _violations(install, {**_CLEAN, "acme/settings.py": mutable}) == [
        "acme.settings.AcmeSettings::settings-not-frozen::settings",
        "acme.settings.AcmeState::settings-not-frozen::state",
    ]


def test_an_own_file_python_cannot_read_is_the_one_violation(install: Install) -> None:
    site = install({"acme/__init__.py": '"""Acme."""\n', "acme/broken.py": "def (:\n"})
    assert unimportable("acme", site / "acme") == (
        "acme/broken.py::unreadable-file::is not valid Python (line 1)"
    )
    (site / "acme" / "broken.py").write_bytes(b"\xff\xfe not utf-8")
    assert unimportable("acme", site / "acme") == "acme/broken.py::unreadable-file::is not UTF-8"


def test_a_dependency_python_cannot_read_is_raised(install: Install) -> None:
    site = install({"acme/__init__.py": "import dep\n", "dep/__init__.py": "def (:\n"})
    with pytest.raises(SyntaxError):
        unimportable("acme", site / "acme")

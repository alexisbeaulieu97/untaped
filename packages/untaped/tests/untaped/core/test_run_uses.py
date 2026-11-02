"""What a plugin is seen calling while a command runs: core's ``run_uses`` rows and doctor's.

The plugins are real installs (``core_test_support.runtime.plugin_uses_site``):
``untaped-shelf`` hands out a ``Bulb`` whose ``dim`` is deprecated, and
``untaped-acme``'s commands call it on the object they get back, which no
import shows.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from core_test_support.runtime.plugin_uses_site import Site, installed_site
from untaped.core import run_uses
from untaped.runtime import bootstrap, run_output
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.testing import CliInvoker

_SHELF = {
    "untaped_shelf/__init__.py": "",
    "untaped_shelf/plugin.py": '''
        """The ``shelf`` plugin class."""

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import Plugin


        class ShelfSettings(BaseModel):
            model_config = ConfigDict(frozen=True)


        class Shelf(Plugin):
            """Bulbs."""

            settings = ShelfSettings
        ''',
    "untaped_shelf/api.py": '''
        """What a plugin may import from shelf."""

        import warnings

        from untaped.sdk import UntapedDeprecationWarning, deprecated


        class Bulb:
            def bright(self) -> str:
                return "bright"

            @deprecated(removed_in=5, replacement=bright)
            @warnings.deprecated(
                "Bulb.dim is deprecated and will be removed in untaped-shelf 5.0; use "
                "`Bulb.bright`",
                category=UntapedDeprecationWarning,
            )
            def dim(self) -> str:
                return "dim"


        def bulb() -> Bulb:
            return Bulb()


        __all__ = ["Bulb", "bulb"]
        ''',
}

_ACME = {
    "untaped_acme/__init__.py": "",
    "untaped_acme/plugin.py": '''
        """The ``acme`` plugin class, whose commands dim a bulb they get back."""

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import Command, Plugin
        from untaped.sdk.errors import UntapedError
        from untaped_shelf.api import bulb


        class Report(Command):
            """Report."""

            def run(self) -> None:
                bulb().dim()


        class Broken(Command):
            """Fail after dimming."""

            def run(self) -> None:
                bulb().dim()
                raise UntapedError("broken on purpose")


        class AcmeSettings(BaseModel):
            model_config = ConfigDict(frozen=True)


        class Acme(Plugin):
            """Reports."""

            settings = AcmeSettings
            commands = (Report, Broken)
        ''',
}


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    """Real installs, a home of the test's own, and warnings shown as outside pytest."""
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(warnings, "_showwarnmsg_impl", run_output._SHOW_UNRECORDED)
    monkeypatch.setattr(warnings._get_context(), "log", None)  # context-aware recording
    monkeypatch.setattr(warnings, "filters", [])
    warnings.simplefilter("always")
    run_uses.forget()
    with installed_site(tmp_path, monkeypatch) as installed:
        yield installed
    run_uses.forget()


def _install(site: Site, *, pinned: bool = False) -> list[PluginCandidate]:
    shelf = site.install("untaped-shelf", _SHELF, at="1.0" if pinned else "3.0")
    requires = ("untaped-shelf==1.0",) if pinned else ("untaped-shelf>=3.0",)
    return [site.install("untaped-acme", _ACME, requires=requires), shelf]


def _invoke(found: list[PluginCandidate], *argv: str) -> Any:
    return CliInvoker().invoke(bootstrap.build_root_app(candidates=found).meta, list(argv))


def _state(site: Site) -> Path:
    return site.root / "home" / "plugins" / "untaped" / "state.yml"


def _rows(site: Site) -> list[dict[str, Any]]:
    path = _state(site)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = (yaml.safe_load(path.read_text()) or {}).get("run_uses", [])
    return rows


def test_a_call_on_a_returned_object_is_recorded_once_with_its_command(site: Site) -> None:
    found = _install(site)
    assert _invoke(found, "acme", "report").exit_code == 0
    assert _invoke(found, "acme", "report").exit_code == 0
    [row] = _rows(site)
    assert {key: value for key, value in row.items() if key != "key"} == {
        "plugin": "acme",
        "distribution": "untaped-acme",
        "version": "1.0",
        "id": "sdk untaped_shelf.api.Bulb.dim",
        "owner": "untaped-shelf",
        "removed_in": 5,
        "replacement": "Bulb.bright",
        "command": "untaped acme report",
    }
    run_uses.forget()  # another process: it finds the row already there
    assert _invoke(found, "acme", "report").exit_code == 0
    assert len(_rows(site)) == 1


def test_a_failed_command_records_what_it_called(site: Site) -> None:
    found = _install(site)
    assert _invoke(found, "acme", "broken").exit_code != 0
    assert [row["command"] for row in _rows(site)] == ["untaped acme broken"]


def test_a_plugin_released_with_what_it_uses_is_not_recorded(site: Site) -> None:
    found = _install(site, pinned=True)
    assert _invoke(found, "acme", "report").exit_code == 0
    assert _rows(site) == []


def test_a_home_it_cannot_write_changes_nothing_about_the_command(site: Site) -> None:
    found = _install(site)
    _state(site).mkdir(parents=True)  # a folder where the file goes: every write fails
    result = _invoke(found, "acme", "report")
    assert result.exit_code == 0, result.output
    assert "state" not in result.stderr


_HOLD = """
import sys, time
from filelock import FileLock
with FileLock(sys.argv[1]):
    print("held", flush=True)
    time.sleep(30)
"""


def test_a_lock_another_process_holds_neither_fails_nor_delays_the_command(site: Site) -> None:
    found = _install(site)
    state = _state(site)
    state.parent.mkdir(parents=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD, f"{state}.lock"], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        result = _invoke(found, "acme", "report")
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait()
    assert result.exit_code == 0, result.output
    assert elapsed < 5  # a wait for the lock would be ten seconds
    assert _rows(site) == []


def test_a_write_drops_the_rows_of_versions_no_longer_installed(site: Site) -> None:
    found = _install(site)
    stale = {
        "key": "untaped-acme 0.9 acme sdk untaped_shelf.api.Bulb.dim | untaped acme report",
        "plugin": "acme",
        "distribution": "untaped-acme",
        "version": "0.9",
        "id": "sdk untaped_shelf.api.Bulb.dim",
        "owner": "untaped-shelf",
        "removed_in": 5,
        "replacement": "Bulb.bright",
        "command": "untaped acme report",
    }
    _state(site).parent.mkdir(parents=True)
    _state(site).write_text(yaml.safe_dump({"run_uses": [stale]}))
    assert _invoke(found, "acme", "report").exit_code == 0
    assert [row["version"] for row in _rows(site)] == ["1.0"]


def test_doctor_names_the_command_a_removal_would_break(site: Site) -> None:
    found = _install(site)
    assert _invoke(found, "acme", "report").exit_code == 0
    result = _invoke(found, "doctor", "--format", "json")
    rows = [row for row in json.loads(result.stdout) if row["check"] == "plugin-uses"]
    assert [(row["plugin"], row["status"], row["detail"]) for row in rows] == [
        (
            "acme",
            "warn",
            "untaped-acme 1.0 calls untaped_shelf.api.Bulb.dim (in `untaped acme report`), "
            "removed in untaped-shelf 5.0: upgrading untaped-shelf to 5.0 makes "
            "`untaped acme report` fail unless a newer untaped-acme stops using it",
        )
    ]

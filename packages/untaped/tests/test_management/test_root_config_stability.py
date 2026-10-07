"""``config list|get`` and ``doctor`` follow the stability marks of settings and capabilities."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar

import pytest
from pydantic import BaseModel

from test_management.support import compose, make_spec, write_config
from untaped import bootstrap
from untaped.config.models import SettingEntry, Source
from untaped.management.config import _emit_split_tables, build_root_config_app
from untaped.management.doctor import build_root_doctor_app
from untaped.settings import profile_section_models, section_stabilities
from untaped.stability import (
    deprecated,
    enable_show_deprecated,
    experimental,
    reset_show_deprecated,
    setting_mark,
    stability_name,
)
from untaped.testing import CliInvoker, CliResult

pytestmark = pytest.mark.usefixtures("_isolated_config")


def stability_of(
    key: str, *, sections: Mapping[str, type[BaseModel]], section_stability: Mapping[str, Any]
) -> str:
    return stability_name(setting_mark(key, sections=sections, section_stability=section_stability))


class Trial(BaseModel):
    """Section ``trial``: stable, experimental and deprecated settings."""

    steady: int = 1
    probe: Annotated[int, experimental] = 2
    old_flag: Annotated[bool, deprecated(replacement="trial.steady")] = False
    gone: Annotated[bool, deprecated()] = False


class Beta(BaseModel):
    """Section ``beta`` of an experimental capability."""

    size: int = 1
    old: Annotated[bool, deprecated(replacement="beta.size")] = False


class Sunset(BaseModel):
    """Section ``sunset`` of a deprecated capability."""

    host: str = "h"
    port: int = 80


class Moved(BaseModel):
    """Section ``moved``: an experimental setting that was renamed."""

    renamed_keys: ClassVar[dict[str, str]] = {"old_wait": "wait"}

    wait: Annotated[int, experimental] = 5


def _compose() -> Any:
    return compose(
        make_spec("trial", profile_model=Trial),
        make_spec("beta", profile_model=Beta, stability=experimental),
        make_spec(
            "sunset", profile_model=Sunset, stability=deprecated(replacement="a newer service")
        ),
        make_spec("moved", profile_model=Moved),
    )


def _config(*args: str) -> CliResult:
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=_compose())
    return CliInvoker().invoke(app, list(args))


def _rows(*args: str) -> dict[str, dict[str, Any]]:
    result = _config("list", "--format", "json", *args)
    assert result.exit_code == 0, result.output
    return {row["key"]: row for row in json.loads(result.stdout)}


def _tables(*args: str) -> dict[str, str]:
    """The output of ``config list`` by table: ``""`` for the stable one, else its heading."""
    result = _config("list", *args)
    assert result.exit_code == 0, result.output
    tables: dict[str, str] = {"": ""}
    heading = ""
    for line in result.stdout.splitlines():
        if line in ("Experimental", "Deprecated"):
            heading = line
            tables[heading] = ""
        else:
            tables[heading] += f"{line}\n"
    return tables


@pytest.fixture
def _show_deprecated() -> Iterator[None]:
    token = enable_show_deprecated()
    yield
    reset_show_deprecated(token)


def test_each_setting_is_listed_in_the_table_of_its_stability() -> None:
    tables = _tables()

    assert "trial.steady" in tables[""]
    assert "trial.probe" not in tables[""]
    assert "trial.probe" in tables["Experimental"]
    assert "beta.size" in tables["Experimental"]
    assert "moved.wait" in tables["Experimental"]


def test_a_deprecated_setting_at_its_default_is_not_listed() -> None:
    tables = _tables()

    assert "Deprecated" not in tables
    assert not any(name in text for text in tables.values() for name in ("old_flag", "gone"))
    assert not any("sunset." in text or "beta.old" in text for text in tables.values())


def test_a_deprecated_setting_set_in_a_file_is_listed_with_its_replacement(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    trial:\n      old_flag: true\n      gone: true\n",
    )

    tables = _tables()

    assert "trial.old_flag" not in tables[""] + tables["Experimental"]
    assert "use `trial.steady`" in tables["Deprecated"]
    assert "trial.gone" in tables["Deprecated"]
    assert " note " in tables["Deprecated"].splitlines()[1]


def test_a_deprecated_setting_set_in_the_environment_is_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_TRIAL__OLD_FLAG", "true")

    assert "trial.old_flag" in _tables()["Deprecated"]


def test_show_deprecated_lists_every_deprecated_setting(_show_deprecated: None) -> None:
    deprecated_table = _tables()["Deprecated"]

    for key in ("trial.old_flag", "trial.gone", "beta.old", "sunset.host", "sunset.port"):
        assert key in deprecated_table
    assert "trial.probe" not in deprecated_table


def test_a_deprecated_capability_marks_its_settings_deprecated() -> None:
    rows = _rows()

    assert {rows[key]["stability"] for key in ("sunset.host", "sunset.port")} == {"deprecated"}
    assert rows["sunset.host"]["note"] == "use a newer service"


def test_an_experimental_capability_marks_its_settings_experimental() -> None:
    assert _rows()["beta.size"]["stability"] == "experimental"


def test_a_field_mark_wins_over_the_capability_mark() -> None:
    rows = _rows()

    assert rows["beta.old"]["stability"] == "deprecated"
    assert rows["beta.old"]["note"] == "use `beta.size`"


def test_structured_output_is_one_flat_list_with_every_setting() -> None:
    rows = _rows()

    stabilities = {key: row["stability"] for key, row in rows.items() if key.startswith("trial.")}
    assert stabilities == {
        "trial.steady": "stable",
        "trial.probe": "experimental",
        "trial.old_flag": "deprecated",
        "trial.gone": "deprecated",
    }
    assert rows["trial.gone"]["note"] is None
    assert rows["http.timeout_seconds"]["stability"] == "stable"


def test_config_get_carries_the_stability() -> None:
    result = _config("get", "trial.probe", "--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["stability"] == "experimental"


def test_an_experimental_setting_read_through_an_old_spelling_is_listed_as_deprecated(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    moved:\n      old_wait: 9\n")

    tables = _tables()

    assert "moved.wait" not in tables["Experimental"]
    assert "from deprecated moved.old_wait" in tables["Deprecated"]
    assert _rows()["moved.wait"]["stability"] == "experimental"


def test_all_profiles_splits_the_same_way(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    trial:\n      steady: 3\n      probe: 4\n"
        "      old_flag: true\n",
    )

    tables = _tables("--all-profiles")

    assert "trial.steady" in tables[""]
    assert "trial.probe" in tables["Experimental"]
    assert "trial.old_flag" in tables["Deprecated"]


def test_columns_apply_to_every_table(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    trial:\n      old_flag: true\n")

    tables = _tables("--columns", "key,value")

    for text in tables.values():
        header = text.splitlines()[1]
        assert " key " in header
        assert " source " not in header
        assert " note " not in header


def test_a_column_can_be_added_to_every_table() -> None:
    tables = _tables("--columns", "+stability")

    assert " stability " in tables[""].splitlines()[1]
    assert " stability " in tables["Experimental"].splitlines()[1]


def test_listing_the_columns_prints_them_once() -> None:
    result = _config("list", "--columns", "?")

    assert result.exit_code == 0, result.output
    assert result.stderr.count("stability") == 1


def test_an_empty_stable_table_is_left_out(capsys: pytest.CaptureFixture[str]) -> None:
    entry = SettingEntry(
        key="beta.size",
        value=1,
        default=1,
        source=Source(kind="default"),
        stability="experimental",
    )

    _emit_split_tables([entry], columns=None)

    out = capsys.readouterr().out
    assert out.startswith("Experimental\n")
    assert "beta.size" in out


def test_nothing_to_list_prints_the_empty_stable_table(capsys: pytest.CaptureFixture[str]) -> None:
    _emit_split_tables([], columns=None)

    assert "Experimental" not in capsys.readouterr().out


def test_the_pure_lookup_prefers_the_field_over_the_section() -> None:
    sections = {"beta": Beta, "trial": Trial}
    inherited = {"beta": experimental}

    assert stability_of("beta.size", sections=sections, section_stability=inherited) == (
        "experimental"
    )
    assert stability_of("beta.old", sections=sections, section_stability=inherited) == "deprecated"
    assert stability_of("trial.steady", sections=sections, section_stability={}) == "stable"
    assert stability_of("trial.probe", sections=sections, section_stability={}) == "experimental"
    assert stability_of("nope.key", sections=sections, section_stability={}) == "stable"
    assert setting_mark("trial.old_flag", sections=sections, section_stability={}) == deprecated(
        replacement="trial.steady"
    )


def test_the_registry_holds_the_composed_sections_and_their_marks() -> None:
    _compose()

    def setting_stability(key: str) -> str:
        return stability_of(
            key, sections=profile_section_models(), section_stability=section_stabilities()
        )

    assert setting_stability("sunset.port") == "deprecated"
    assert setting_stability("beta.size") == "experimental"
    assert setting_stability("trial.probe") == "experimental"
    assert setting_stability("trial.steady") == "stable"
    assert setting_stability("shell.aliases") == "deprecated"


def _doctor_detail() -> str:
    app = build_root_doctor_app(
        shell=bootstrap.SHELL_SPEC, builtin_for=lambda _name: None, result=_compose()
    )
    result = CliInvoker().invoke(app, ["--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    return next(
        row["detail"] for row in json.loads(result.stdout) if row["check"] == "deprecated-keys"
    )


def test_doctor_lists_every_key_set_in_a_deprecated_capabilitys_section(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    sunset:\n      host: h2\n      port: 81\n"
        "    beta:\n      size: 3\n",
    )

    detail = _doctor_detail()

    assert "sunset.host (profile default, deprecated): use a newer service" in detail
    assert "sunset.port (profile default, deprecated): use a newer service" in detail
    assert "beta." not in detail


def test_reading_a_deprecated_capabilitys_settings_warns_nothing(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    sunset:\n      host: h2\n")

    result = _config("get", "sunset.host")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "h2"
    assert result.stderr == ""

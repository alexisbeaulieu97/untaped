"""installed_code: a version that changes when the named code does, and not otherwise."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.sdk import ConfigError, installed_code
from untaped.sdk.storage import installed_code as module


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    """Each version is computed once per process; these tests compute it afresh."""
    module._modules_version.cache_clear()
    module._install_version.cache_clear()
    yield
    module._modules_version.cache_clear()
    module._install_version.cache_clear()


def _again(code: module.InstalledCode) -> str:
    module._modules_version.cache_clear()
    module._install_version.cache_clear()
    return code()


@pytest.fixture
def parsers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    package = tmp_path / "site" / "acme_parsers"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "rows.py").write_text("ROWS = 1\n")
    (tmp_path / "site" / "acme_other.py").write_text("OTHER = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    return package


def test_a_package_changes_when_its_files_do(parsers: Path) -> None:
    code = installed_code("acme_parsers")
    before = code()
    assert _again(code) == before
    (parsers.parent / "acme_other.py").write_text("OTHER = 2\n")
    assert _again(code) == before
    (parsers / "rows.py").write_text("ROWS = 2\n")
    assert _again(code) != before
    (parsers / "rows.py").write_text("ROWS = 1\n")
    assert _again(code) == before
    (parsers / "more.py").write_text("")
    assert _again(code) != before


def test_bytecode_and_hidden_folders_do_not_count(parsers: Path) -> None:
    code = installed_code("acme_parsers")
    before = code()
    (parsers / "__pycache__").mkdir()
    (parsers / "__pycache__" / "rows.cpython-314.pyc").write_bytes(b"\0")
    (parsers / ".cache").mkdir()
    (parsers / ".cache" / "x").write_text("x")
    assert _again(code) == before


def test_a_module_counts_as_its_name(parsers: Path) -> None:
    import acme_other  # type: ignore[import-not-found]

    assert installed_code(acme_other)() == installed_code("acme_other")()
    assert installed_code("acme_other")() != installed_code("acme_parsers")()
    assert installed_code("acme_parsers", "acme_other")() != installed_code("acme_parsers")()


def test_naming_a_module_never_imports_it(parsers: Path) -> None:
    import sys

    installed_code("acme_parsers.rows")()
    assert "acme_parsers" not in sys.modules


def test_a_module_without_files_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="'acme_absent' has no source files"):
        installed_code("acme_absent")()


@pytest.mark.parametrize("bad", ["", 3])
def test_only_a_module_or_its_name_is_taken(bad: object) -> None:
    with pytest.raises(TypeError, match="is not a module or a module's name"):
        installed_code(bad)  # type: ignore[arg-type]


def test_it_reads_as_written() -> None:
    assert repr(installed_code()) == "installed_code()"
    assert repr(installed_code("acme_parsers", "acme_other")) == (
        "installed_code('acme_parsers', 'acme_other')"
    )
    assert installed_code("acme_parsers") == installed_code("acme_parsers")


def _plugin_distribution(site: Path, source: Path, *, version: str = "1.0") -> Path:
    info = site / f"untaped_acme-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: untaped-acme\nVersion: {version}\n")
    (info / "entry_points.txt").write_text("[untaped.plugins]\nacme = untaped_acme:Acme\n")
    (info / "RECORD").write_text("untaped_acme.pth,,\n")
    (info / "direct_url.json").write_text(
        f'{{"url": "{source.as_uri()}", "dir_info": {{"editable": true}}}}'
    )
    return info


@pytest.fixture
def acme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source = tmp_path / "untaped-acme"
    (source / "src" / "untaped_acme").mkdir(parents=True)
    (source / "src" / "untaped_acme" / "__init__.py").write_text("")
    (source / "tests").mkdir()
    site = tmp_path / "site-packages"
    info = _plugin_distribution(site, source)
    monkeypatch.syspath_prepend(str(site))
    return info, source / "src" / "untaped_acme"


def test_the_install_follows_every_plugin_distribution(acme: tuple[Path, Path]) -> None:
    info, package = acme
    code = installed_code()
    before = code()
    assert _again(code) == before
    (info / "RECORD").write_text("untaped_acme.pth,sha256=new,10\n")
    reinstalled = _again(code)
    assert reinstalled != before
    plugin = package / "__init__.py"
    stat = plugin.stat()
    os.utime(plugin, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert _again(code) != reinstalled


def test_an_editable_install_counts_its_src_folder_only(acme: tuple[Path, Path]) -> None:
    _, package = acme
    code = installed_code()
    before = code()
    (package.parent.parent / "tests" / "test_acme.py").write_text("")
    assert _again(code) == before
    (package / "commands.py").write_text("")
    assert _again(code) != before


def test_an_upgrade_changes_the_install(acme: tuple[Path, Path]) -> None:
    info, _ = acme
    code = installed_code()
    before = code()
    text = (info / "METADATA").read_text().replace("Version: 1.0", "Version: 1.1")
    (info / "METADATA").write_text(text)
    assert _again(code) != before


def test_a_plain_install_is_its_version_and_record(acme: tuple[Path, Path]) -> None:
    info, package = acme
    (info / "direct_url.json").write_text('{"url": "https://example.com/acme.whl"}')
    code = installed_code()
    before = code()
    (package / "commands.py").write_text("")
    assert _again(code) == before

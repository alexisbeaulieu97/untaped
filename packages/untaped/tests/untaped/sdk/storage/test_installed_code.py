"""installed_code: a version that changes when the named code does, and not otherwise."""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
import types
import zipfile
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
def parsers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    package = tmp_path / "site" / "acme_parsers"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "rows.py").write_text("ROWS = 1\n")
    (tmp_path / "site" / "acme_other.py").write_text("OTHER = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    yield package
    for name in ("acme_other", "acme_parsers", "acme_parsers.rows"):
        sys.modules.pop(name, None)  # imported by a test: the next one has other files


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
    installed_code("acme_parsers.rows")()
    assert "acme_parsers" not in sys.modules


def test_a_module_without_files_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="'acme_absent' has no source files"):
        installed_code("acme_absent")()


def test_a_namespace_package_is_found_without_importing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for portion in ("one", "two"):
        folder = tmp_path / portion / "acme_ns"
        folder.mkdir(parents=True)
        (folder / f"{portion}.py").write_text("")
        monkeypatch.syspath_prepend(str(tmp_path / portion))
    code = installed_code("acme_ns")
    before = code()
    assert "acme_ns" not in sys.modules
    (tmp_path / "two" / "acme_ns" / "two.py").write_text("TWO = 2\n")
    assert _again(code) != before


def test_a_submodule_is_looked_for_where_its_loaded_parent_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "stale" / "acme_hooked"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text("")
    (stale / "sub.py").write_text("STALE = 1\n")
    real = tmp_path / "real" / "acme_hooked"
    real.mkdir(parents=True)
    (real / "sub.py").write_text("REAL = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path / "stale"))
    parent = types.ModuleType("acme_hooked")
    parent.__path__ = [str(real)]
    monkeypatch.setitem(sys.modules, "acme_hooked", parent)
    code = installed_code("acme_hooked.sub")
    before = code()
    (stale / "sub.py").write_text("STALE = 2\n")
    assert _again(code) == before
    (real / "sub.py").write_text("REAL = 2\n")
    assert _again(code) != before


def test_a_module_in_an_archive_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "acme.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("acme_zipped.py", "X = 1\n")
    monkeypatch.syspath_prepend(str(archive))
    with pytest.raises(ConfigError, match="no source files on disk"):
        installed_code("acme_zipped")()


def test_a_file_gone_while_walking_is_left_out_and_an_unreadable_one_stops(
    parsers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Path.read_bytes

    def read(path: Path) -> bytes:
        if path.name == "rows.py":
            raise FileNotFoundError(2, "gone", str(path))
        if path.name == "secret.py":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    installed_code("acme_parsers")()
    (parsers / "secret.py").write_text("")
    with pytest.raises(ConfigError, match=r"cannot read .*secret\.py.*Permission denied"):
        _again(installed_code("acme_parsers"))


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


def _distribution(
    site: Path, name: str, *, version: str = "1.0", source: Path | None = None
) -> Path:
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    (info / "METADATA").write_text(metadata)
    if name != "untaped":
        module = name.replace("-", "_")
        (info / "entry_points.txt").write_text(f"[untaped.plugins]\n{name} = {module}:P\n")
    (info / "RECORD").write_text(f"{name}.pth,,\n")
    if source is not None:
        url = json.dumps({"url": source.as_uri(), "dir_info": {"editable": True}})
        (info / "direct_url.json").write_text(url)
    return info


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An install of its own: untaped and the plugins under ``site``, nothing else."""
    site = tmp_path / "site-packages"
    _distribution(site, "untaped")

    def entry_points(*, group: str) -> importlib.metadata.EntryPoints:
        found = [
            entry
            for each in importlib.metadata.distributions(path=[str(site)])
            for entry in each.entry_points
            if entry.group == group
        ]
        return importlib.metadata.EntryPoints(found)

    def distribution(name: str) -> importlib.metadata.Distribution:
        [found] = importlib.metadata.distributions(name=name, path=[str(site)])
        return found

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    return site


@pytest.fixture
def acme(tmp_path: Path, site: Path) -> tuple[Path, Path]:
    source = tmp_path / "untaped-acme"
    (source / "src" / "untaped_acme").mkdir(parents=True)
    (source / "src" / "untaped_acme" / "__init__.py").write_text("")
    (source / "tests").mkdir()
    info = _distribution(site, "untaped-acme", source=source)
    return info, source / "src" / "untaped_acme"


def _touch(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_the_install_follows_every_plugin_distribution(acme: tuple[Path, Path]) -> None:
    info, package = acme
    code = installed_code()
    before = code()
    assert _again(code) == before
    (info / "RECORD").write_text("untaped-acme.pth,sha256=new,10\n")
    reinstalled = _again(code)
    assert reinstalled != before
    _touch(package / "__init__.py")
    assert _again(code) != reinstalled


def test_a_new_plugin_changes_the_install(site: Path, acme: tuple[Path, Path]) -> None:
    code = installed_code()
    before = code()
    _distribution(site, "untaped-other")
    assert _again(code) != before


def test_an_editable_install_counts_its_src_folder_only(acme: tuple[Path, Path]) -> None:
    _, package = acme
    code = installed_code()
    before = code()
    (package.parent.parent / "tests" / "test_acme.py").write_text("")
    (package / ".commands.py.swp").write_text("")
    (package / "commands.py~").write_text("")
    assert _again(code) == before
    (package / "commands.py").write_text("")
    assert _again(code) != before


def test_a_flat_editable_install_leaves_tools_trees_out(tmp_path: Path, site: Path) -> None:
    source = tmp_path / "untaped-flat"
    (source / "untaped_flat").mkdir(parents=True)
    (source / "untaped_flat" / "__init__.py").write_text("")
    _distribution(site, "untaped-flat", source=source)
    code = installed_code()
    before = code()
    (source / "venv").mkdir()
    (source / "venv" / "pyvenv.cfg").write_text("")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "x.js").write_text("")
    assert _again(code) == before
    (source / "untaped_flat" / "commands.py").write_text("")
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


def test_untaped_run_from_a_source_tree_counts_its_own_files(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_untaped(name: str) -> importlib.metadata.Distribution:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", no_untaped)
    seen: list[str] = []
    real = module._stat_parts

    def recorded(name: str, *roots: Path) -> list[bytes]:
        seen.append(name)
        return real(name, *roots)

    monkeypatch.setattr(module, "_stat_parts", recorded)
    installed_code()()
    assert seen == ["untaped"]

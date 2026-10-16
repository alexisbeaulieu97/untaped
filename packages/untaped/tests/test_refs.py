"""References: the install identity, one file per holder, liveness, and the per-run refresh."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from untaped import refs
from untaped.errors import ConfigError
from untaped.store import Store
from untaped.testing import write_reference


def _install(root: Path, version: str = "11.1.0") -> Path:
    """A directory that holds an untaped distribution, as any installer leaves one."""
    (root / f"untaped-{version}.dist-info").mkdir(parents=True)
    return root


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This process's environment: a fake site-packages holding untaped 11.1.0."""
    env = _install(tmp_path / "venv" / "site-packages")

    class _Dist:
        version = "11.1.0"

        @staticmethod
        def locate_file(_name: str) -> Path:
            return env

    monkeypatch.setattr(refs, "distribution", lambda _name: _Dist())
    return env


# --- identity ------------------------------------------------------------------


def test_the_holder_comes_from_the_distribution_directory_alone(site: Path) -> None:
    assert refs.environment() == site
    digest = hashlib.sha256(str(site).encode()).hexdigest()[:12]
    assert refs.install_holder(site) == f"install:{digest}"


def test_a_source_tree_has_no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> object:
        raise PackageNotFoundError("untaped")

    monkeypatch.setattr(refs, "distribution", missing)
    assert refs.environment() is None
    with pytest.raises(ConfigError, match="source tree"):
        refs.register(["acme.data@1"])
    assert refs.unregister() is None
    assert refs.this_install() is None


def test_the_real_environment_is_the_directory_holding_untaped() -> None:
    env = refs.environment()
    assert env is not None
    assert any(env.glob("untaped-*.dist-info"))


# --- the file ------------------------------------------------------------------


def test_register_writes_one_file_for_this_install(site: Path) -> None:
    ref = refs.register(["acme.data@2", "ansible.index@4"])

    holder = refs.install_holder(site)
    assert ref.file == refs.refs_dir() / f"install-{holder.partition(':')[2]}.json"
    data = json.loads(ref.file.read_text())
    assert data == {
        "untaped": "1",
        "holder": holder,
        "anchor": str(site),
        "version": "11.1.0",
        "targets": ["acme.data@2", "ansible.index@4"],
    }
    assert refs.this_install() == ref
    assert ref.live and not ref.pre_upgrade
    assert ref.layouts("acme.data") == {2}


def test_unregister_removes_the_file(site: Path) -> None:
    refs.register(["acme.data@1"])
    removed = refs.unregister()
    assert removed is not None and not removed.file.exists()
    assert refs.unregister() is None
    assert refs.this_install() is None


def test_a_holder_name_becomes_a_safe_file_name() -> None:
    long = "workspace:" + "p" * 300
    file = refs.reference_file(long)
    assert ":" not in file.name
    assert len(file.name.encode()) < 255
    write_reference(long, ["git.store#github.com/acme/pay"], plugin="workspace")
    [ref] = refs.read_references()
    assert ref.holder == long  # the full holder lives inside the file
    assert ref.kind == "workspace" and ref.plugin == "workspace"


def test_a_reference_is_live_while_its_directory_holds_untaped(tmp_path: Path) -> None:
    env = _install(tmp_path / "other", "11.0.0")
    write_reference("install:aaaaaaaaaaaa", ["acme.data@1"], anchor=env, version="11.0.0")
    [ref] = refs.read_references()
    assert ref.live and not ref.pre_upgrade

    (env / "untaped-11.0.0.dist-info").rename(env / "untaped-11.2.0.dist-info")
    assert ref.live and ref.pre_upgrade  # upgraded in place, not run since

    (env / "untaped-11.2.0.dist-info").rmdir()
    assert not ref.live  # uninstalled: the directory is still there, untaped is not
    env.rmdir()
    assert not ref.live


def test_an_anchorless_holder_lives_until_released() -> None:
    write_reference("workspace:pay", ["git.store#github.com/acme/pay"], plugin="workspace")
    [ref] = refs.read_references()
    assert ref.live
    assert refs.holders("git.store#github.com/acme/pay") == ["workspace:pay"]


def test_holders_lists_only_live_references(tmp_path: Path) -> None:
    alive = _install(tmp_path / "alive")
    write_reference("install:aaaaaaaaaaaa", ["acme.data@1"], anchor=alive, version="11.1.0")
    write_reference("install:bbbbbbbbbbbb", ["acme.data@1"], anchor=tmp_path / "gone")
    assert refs.holders("acme.data@1") == ["install:aaaaaaaaaaaa"]
    assert refs.holders("acme.data@2") == []


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ('{"untaped": "2", "holder": "install:x", "targets": []}', "newer untaped"),
        ('{"untaped": "x"}', "unreadable"),
        ("not json", "unreadable"),
        ("[]", "unreadable"),
        ('{"untaped": "1", "targets": []}', "unreadable"),
    ],
)
def test_an_unreadable_or_newer_reference_is_opaque(content: str, why: str) -> None:
    folder = refs.refs_dir()
    folder.mkdir(parents=True)
    (folder / "install-x.json").write_text(content)
    [ref] = refs.read_references()
    assert ref.opaque is not None and why in ref.opaque
    assert ref.live  # it pauses pruning
    with pytest.raises(ConfigError, match=r"cannot tell who holds acme\.data@1"):
        refs.holders("acme.data@1")  # unknown, never "nobody"


def test_a_missing_folder_has_no_references() -> None:
    assert refs.read_references() == []


# --- the refresh ---------------------------------------------------------------


@pytest.fixture
def composed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What this run's composition declares; a test edits it."""
    targets = ["acme.data@1"]
    monkeypatch.setattr(refs, "composed_targets", lambda: tuple(targets))
    return targets


def test_a_registered_install_refreshes_on_its_first_store_open(
    site: Path, composed: list[str], tmp_path: Path
) -> None:
    ref = refs.register(["acme.data@1"])
    composed[:] = ["acme.data@2"]  # upgraded in place: same directory, new layout

    Store("acme", "data", base=lambda: tmp_path / "data.db", layout=2).open()

    assert json.loads(ref.file.read_text())["targets"] == ["acme.data@2"]


def test_the_refresh_runs_once_per_process(site: Path, composed: list[str], tmp_path: Path) -> None:
    ref = refs.register(["acme.data@1"])
    refs.refresh()
    composed[:] = ["acme.data@9"]
    refs.refresh()
    assert json.loads(ref.file.read_text())["targets"] == ["acme.data@1"]


def test_an_unchanged_reference_is_not_rewritten(
    site: Path, composed: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    refs.register(["acme.data@1"])
    writes: list[object] = []
    monkeypatch.setattr(refs, "atomic_write", lambda *args, **_kw: writes.append(args))
    refs.refresh()
    assert writes == []


def test_no_run_registers(site: Path, composed: list[str], tmp_path: Path) -> None:
    Store("acme", "data", base=lambda: tmp_path / "data.db").open()
    assert refs.read_references() == []


def test_a_refresh_without_a_composition_keeps_the_file(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = refs.register(["acme.data@1"])
    monkeypatch.setattr(refs, "composed_targets", lambda: None)
    refs.refresh()
    assert json.loads(ref.file.read_text())["targets"] == ["acme.data@1"]


def test_a_read_only_refs_folder_gives_no_error(
    site: Path, composed: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    refs.register(["acme.data@0"])

    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(refs, "atomic_write", refused)
    refs.refresh()  # never raises: the next run tries again


def test_the_composition_names_every_declared_store() -> None:
    from untaped.bootstrap import compose_root, kept_composition
    from untaped.plugins.registry import PluginSpec
    from untaped.testing import plugin_candidate

    data = Store("acme", "data", base=lambda: Path("/x/data.db"), layout=3)
    other = Store("acme", "catalogue", base=lambda: Path("/x/catalogue"), layout=1)
    spec = PluginSpec(name="acme", stores=(data, other))
    with kept_composition(quiet=True):
        compose_root(candidates=[plugin_candidate(spec)])
        assert refs.composed_targets() == ("acme.catalogue@1", "acme.data@3")

from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.capabilities.workspace.domain import (
    ManifestDefaults,
    Repo,
    WorkspaceManifest,
)
from untaped.capabilities.workspace.errors import ManifestError
from untaped.capabilities.workspace.infrastructure import YamlManifestRepository


def test_read_missing_raises(tmp_path: Path) -> None:
    repo = YamlManifestRepository()
    with pytest.raises(ManifestError, match="no manifest"):
        repo.read(tmp_path)


def test_round_trip_creates_parent_dirs_and_writes_plain_yaml(tmp_path: Path) -> None:
    """``repos`` is a tuple at the type level but must serialise as a plain
    YAML sequence (no ``!!python/tuple`` tag)."""
    target = tmp_path / "deeply" / "nested"
    manifest = WorkspaceManifest(
        name="prod",
        defaults=ManifestDefaults(branch="main"),
        repos=[
            Repo(url="https://github.com/org/svc-a.git"),
            Repo(url="https://github.com/org/svc-b.git", name="bee", branch="develop"),
        ],
    )
    YamlManifestRepository().write(target, manifest)
    assert "python/tuple" not in (target / "untaped.yml").read_text()
    assert YamlManifestRepository().read(target) == manifest


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("not: valid: yaml: at all:", "invalid YAML"),
        (
            yaml.safe_dump({"repos": [{"url": "https://x/a.git", "weird_field": True}]}),
            "invalid manifest",
        ),
    ],
)
def test_read_rejects_bad_manifest(tmp_path: Path, text: str, match: str) -> None:
    (tmp_path / "untaped.yml").write_text(text)
    with pytest.raises(ManifestError, match=match):
        YamlManifestRepository().read(tmp_path)


def test_read_unreadable_manifest_wraps_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "untaped.yml"
    path.write_text("name: prod\n")
    original = Path.read_text

    def _read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == path:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    with pytest.raises(ManifestError, match=r"could not read manifest at .*untaped\.yml"):
        YamlManifestRepository().read(tmp_path)


def test_read_external(tmp_path: Path) -> None:
    src = tmp_path / "team-prod.yml"
    src.write_text(
        yaml.safe_dump(
            {
                "name": "team-prod",
                "defaults": {"branch": "main"},
                "repos": [{"url": "https://github.com/org/svc-a.git"}],
            }
        )
    )
    loaded = YamlManifestRepository().read_external(src)
    assert loaded.source == src
    assert loaded.manifest.name == "team-prod"


def test_read_external_missing(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found"):
        YamlManifestRepository().read_external(tmp_path / "absent.yml")


def test_read_external_unreadable_manifest_wraps_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "team-prod.yml"
    source.write_text("name: prod\n")
    original = Path.read_text

    def _read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == source:
            raise OSError("disk unavailable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    with pytest.raises(ManifestError, match=r"could not read manifest at .*team-prod\.yml"):
        YamlManifestRepository().read_external(source)


def test_write_empty_defaults_omitted(tmp_path: Path) -> None:
    YamlManifestRepository().write(tmp_path, WorkspaceManifest())
    raw = yaml.safe_load((tmp_path / "untaped.yml").read_text())
    assert "defaults" not in raw


def test_write_does_not_use_a_fixed_temp_name(tmp_path: Path) -> None:
    """A stale/concurrent ``untaped.yml.tmp`` must not break the write."""
    (tmp_path / "untaped.yml.tmp").mkdir()
    YamlManifestRepository().write(tmp_path, WorkspaceManifest(name="prod"))
    assert YamlManifestRepository().read(tmp_path).name == "prod"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == ["untaped.yml.tmp"]


def test_write_wraps_os_errors(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(ManifestError, match="could not write manifest"):
        YamlManifestRepository().write(blocker, WorkspaceManifest())


def test_reads_utf8_manifest(tmp_path: Path) -> None:
    (tmp_path / "untaped.yml").write_bytes("name: café\n".encode())
    assert YamlManifestRepository().read(tmp_path).name == "café"


def test_delete_removes_manifest_and_tolerates_missing(tmp_path: Path) -> None:
    repo = YamlManifestRepository()
    repo.write(tmp_path, WorkspaceManifest())
    repo.delete(tmp_path)
    assert not (tmp_path / "untaped.yml").exists()
    repo.delete(tmp_path)

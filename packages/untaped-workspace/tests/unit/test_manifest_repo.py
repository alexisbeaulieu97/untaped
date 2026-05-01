from pathlib import Path

import pytest
import yaml
from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    WorkspaceManifest,
)
from untaped_workspace.errors import ManifestError
from untaped_workspace.infrastructure import ManifestRepository


def test_read_missing_raises(tmp_path: Path) -> None:
    repo = ManifestRepository()
    with pytest.raises(ManifestError, match="no manifest"):
        repo.read(tmp_path)


def test_round_trip(tmp_path: Path) -> None:
    repo = ManifestRepository()
    manifest = WorkspaceManifest(
        name="prod",
        defaults=ManifestDefaults(branch="main"),
        repos=[
            Repo(url="https://github.com/org/svc-a.git"),
            Repo(url="https://github.com/org/svc-b.git", name="bee", branch="develop"),
        ],
    )
    repo.write(tmp_path, manifest)
    loaded = repo.read(tmp_path)
    assert loaded.name == "prod"
    assert loaded.defaults.branch == "main"
    assert len(loaded.repos) == 2
    assert loaded.repos[1].name == "bee"
    assert loaded.repos[1].branch == "develop"


def test_write_creates_dir(tmp_path: Path) -> None:
    target = tmp_path / "deeply" / "nested"
    ManifestRepository().write(target, WorkspaceManifest())
    assert (target / "untaped.yml").is_file()


def test_read_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "untaped.yml").write_text("not: valid: yaml: at all:")
    with pytest.raises(ManifestError, match="invalid YAML"):
        ManifestRepository().read(tmp_path)


def test_read_invalid_schema(tmp_path: Path) -> None:
    (tmp_path / "untaped.yml").write_text(
        yaml.safe_dump({"repos": [{"url": "https://x/a.git", "weird_field": True}]})
    )
    with pytest.raises(ManifestError, match="invalid manifest"):
        ManifestRepository().read(tmp_path)


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
    written = ManifestRepository().read_external(src)
    assert written.source == src
    assert written.manifest.name == "team-prod"


def test_read_external_missing(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found"):
        ManifestRepository().read_external(tmp_path / "absent.yml")


def test_write_empty_defaults_omitted(tmp_path: Path) -> None:
    ManifestRepository().write(tmp_path, WorkspaceManifest())
    raw = yaml.safe_load((tmp_path / "untaped.yml").read_text())
    assert "defaults" not in raw

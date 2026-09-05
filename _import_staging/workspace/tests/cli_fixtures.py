"""Shared fixtures for workspace CLI command tests."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped.settings import get_settings


@pytest.fixture
def isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


@pytest.fixture
def existing_clones(tmp_path: Path) -> Path:
    """A directory pre-populated with two real git clones on different branches."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    upstream_a = tmp_path / "_up_a.git"
    upstream_b = tmp_path / "_up_b.git"
    for upstream, branch in ((upstream_a, "main"), (upstream_b, "trunk")):
        subprocess.run(
            ["git", "init", "--bare", f"--initial-branch={branch}", str(upstream)],
            check=True,
            capture_output=True,
        )
        seed = tmp_path / f"_seed_{upstream.name}"
        subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
        (seed / "README.md").write_text("hi")
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "init"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(seed), "push", "origin", branch],
            check=True,
            capture_output=True,
        )
        shutil.rmtree(seed)

    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(
        ["git", "clone", "--branch", "main", str(upstream_a), str(ws / "alpha")],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clone", "--branch", "trunk", str(upstream_b), str(ws / "beta")],
        check=True,
        capture_output=True,
    )
    return ws


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    bare = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "_seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "tag.gpgsign", "false"], check=True)
    (seed / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "main"], check=True, capture_output=True
    )
    shutil.rmtree(seed)
    return bare


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "_cache"
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(cache))
    get_settings.cache_clear()
    return cache


def push_branch(upstream: Path, tmp_path: Path, *, branch: str) -> None:
    seed = tmp_path / f"_seed_{branch}"
    subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "checkout", "-b", branch],
        check=True,
        capture_output=True,
    )
    (seed / f"{branch}.txt").write_text(branch)
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", branch],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", branch],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(seed)

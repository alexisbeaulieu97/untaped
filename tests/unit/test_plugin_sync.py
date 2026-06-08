"""Tests for managed plugin environment sync helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from untaped.plugin_sync import uv_pip_compile_command


def test_uv_compile_resolves_transitive_direct_plugin_dependency(tmp_path: Path) -> None:
    child = _write_wheel(tmp_path, name="untaped-github-fixture")
    parent = _write_wheel(
        tmp_path,
        name="untaped-ansible-fixture",
        requires_dist=f"untaped-github-fixture @ {child.as_uri()}",
    )
    core = _write_wheel(tmp_path, name="untaped")
    requirements = tmp_path / "requirements.in"
    resolved = tmp_path / "requirements.txt"
    requirements.write_text(f"{core}\n{parent}\n", encoding="utf-8")

    result = subprocess.run(
        [
            *uv_pip_compile_command(Path(sys.executable), requirements, resolved),
            "--no-index",
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "untaped-github-fixture" not in requirements.read_text(encoding="utf-8")
    assert "untaped-github-fixture" in resolved.read_text(encoding="utf-8")


def _write_wheel(
    root: Path,
    *,
    name: str,
    version: str = "0.1.0",
    requires_dist: str | None = None,
) -> Path:
    normalized = name.replace("-", "_")
    wheel = root / f"{normalized}-{version}-py3-none-any.whl"
    dist_info = f"{normalized}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    if requires_dist is not None:
        metadata += f"Requires-Dist: {requires_dist}\n"
    with ZipFile(wheel, "w", ZIP_DEFLATED) as archive:
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: untaped-tests\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel

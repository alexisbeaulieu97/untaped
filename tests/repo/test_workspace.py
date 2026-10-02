"""The workspace layout: packages, their tests, and the root configuration agree."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"
EXPECTED_MEMBERS = ["untaped"]  # Tasks 4 and 5 extend this list


def _members() -> list[str]:
    return sorted(p.parent.name for p in PACKAGES.glob("*/pyproject.toml"))


def test_members_are_the_expected_packages_each_with_tests() -> None:
    assert _members() == EXPECTED_MEMBERS
    for name in EXPECTED_MEMBERS:
        assert (PACKAGES / name / "tests").is_dir()


def test_root_config_lists_every_package() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    src = sorted(f"packages/{name}/src" for name in _members())
    tests = sorted(f"packages/{name}/tests" for name in _members())
    assert sorted(config["tool"]["coverage"]["run"]["source"]) == src
    assert sorted(config["tool"]["mypy"]["files"]) == sorted([*src, "scripts/release.py"])
    assert sorted(config["tool"]["pytest"]["ini_options"]["testpaths"]) == sorted([*tests, "tests"])


def test_test_package_names_are_unique_and_never_tests() -> None:
    roots = [PACKAGES / name / "tests" for name in _members()] + [REPO_ROOT / "tests"]
    names: list[str] = []
    for root in roots:
        assert not (root / "__init__.py").exists(), root
        names += [p.parent.name for p in root.glob("*/__init__.py")]
        names += [p.stem for p in root.glob("*.py") if p.stem not in ("conftest", "__init__")]
    assert "tests" not in names
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)


def test_two_packages_tests_with_one_basename_both_run(tmp_path: Path) -> None:
    """Importlib mode keeps same-named test modules of different packages apart."""
    for pkg, value in (("alpha", 1), ("beta", 2)):
        unit = tmp_path / f"packages/{pkg}/tests/{pkg}/unit"
        unit.mkdir(parents=True)
        (unit.parent / "__init__.py").write_text("")
        (unit / "__init__.py").write_text("")
        (unit.parent / "conftest.py").write_text(
            f"import pytest\n@pytest.fixture\ndef value(): return {value}\n"
        )
        (unit / "test_same.py").write_text(f"def test_value(value): assert value == {value}\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            "packages",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_the_installed_untaped_lists_every_first_party_capability() -> None:
    """A fresh process: the editable installs' entry-point metadata is current."""
    listed = subprocess.run(
        [sys.executable, "-m", "untaped", "capabilities", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert listed.returncode == 0, listed.stderr
    names = [row["name"] for row in json.loads(listed.stdout)]
    assert names == ["ansible", "awx", "github", "jira", "recipe", "workspace"]

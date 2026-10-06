"""The workspace layout: packages, their tests, and the root configuration agree."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from typing import Any

import release
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from repo.support import FIRST_PARTY, PACKAGES, REPO_ROOT

EXPECTED_MEMBERS = [
    "untaped",
    "untaped-ansible",
    "untaped-awx",
    "untaped-dotfiles",
    "untaped-github",
    "untaped-jira",
    "untaped-recipe",
    "untaped-workspace",
]


def _members() -> list[str]:
    return sorted(p.parent.name for p in PACKAGES.glob("*/pyproject.toml"))


def _root_config() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def test_members_are_the_expected_packages_each_with_tests() -> None:
    assert _members() == EXPECTED_MEMBERS
    for name in EXPECTED_MEMBERS:
        assert (PACKAGES / name / "tests").is_dir()


def test_contributing_install_from_git_lists_every_package() -> None:
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text()
    assert set(re.findall(r"#subdirectory=packages/([\w-]+)", text)) == set(_members())


def test_root_config_lists_every_package() -> None:
    config = _root_config()
    src = sorted(f"packages/{name}/src" for name in _members())
    tests = sorted(f"packages/{name}/tests" for name in _members())
    distributions = set(release.packages(REPO_ROOT))
    assert set(config["tool"]["uv"]["sources"]) == distributions
    assert sorted(config["tool"]["coverage"]["run"]["source"]) == src
    scripts = ["scripts/check_pr.py", "scripts/release.py"]
    assert sorted(config["tool"]["mypy"]["files"]) == sorted([*src, *scripts])
    vulture = config["tool"]["vulture"]["paths"]
    assert sorted(vulture) == sorted([*src, "scripts/vulture_allowlist.py"])
    assert sorted(config["tool"]["mypy"]["mypy_path"]) == src
    pytest_options = config["tool"]["pytest"]["ini_options"]
    assert sorted(pytest_options["testpaths"]) == sorted([*tests, "tests"])
    assert {*tests, "scripts"} <= set(pytest_options["pythonpath"])


def test_test_package_names_are_unique_and_never_tests() -> None:
    roots = [PACKAGES / name / "tests" for name in _members()] + [REPO_ROOT / "tests"]
    names: list[str] = []
    for root in roots:
        assert not (root / "__init__.py").exists(), root
        names += [p.parent.name for p in root.glob("*/__init__.py")]
        names += [p.stem for p in root.glob("*.py") if p.stem not in ("conftest", "__init__")]
    assert "tests" not in names
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)


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
    assert names == list(FIRST_PARTY)


def test_capability_packages_declare_their_entry_point_and_pin_core() -> None:
    projects = release.packages(REPO_ROOT)
    core = projects.pop("untaped")
    version = core["version"]
    for project in projects.values():
        name = project["name"].removeprefix("untaped-")
        assert project["version"] == version
        assert project["entry-points"]["untaped.capabilities"] == {name: f"untaped_{name}:provider"}
        assert f"untaped=={version}" in project["dependencies"]
        assert core["optional-dependencies"][name] == [f"untaped-{name}=={version}"]
    assert sorted(core["optional-dependencies"]["all"]) == sorted(
        f"{project['name']}=={version}" for project in projects.values()
    )
    assert "entry-points" not in core


def test_every_project_and_the_plugin_template_share_a_python_floor_above_3_14_0() -> None:
    # uv treats a 3.14.0rcN interpreter as 3.14.0, so a ">=3.14" floor lets it
    # pick a release candidate. A plugin locking against untaped needs a floor
    # at least as high as untaped's, so the example and the template match.
    floors = {
        name: project["requires-python"] for name, project in release.packages(REPO_ROOT).items()
    }
    example = tomllib.loads((REPO_ROOT / "examples/untaped-hello/pyproject.toml").read_text())
    floors["examples/untaped-hello"] = example["project"]["requires-python"]
    template = re.search(
        r'^requires-python = "(.+)"$', (REPO_ROOT / "docs/plugins.md").read_text(), re.M
    )
    assert template is not None
    floors["docs/plugins.md"] = template.group(1)
    floor = floors["untaped"]
    assert floors == dict.fromkeys(floors, floor)
    assert Version("3.14.0") not in SpecifierSet(floor)


def test_dependent_capabilities_pin_github() -> None:
    projects = release.packages(REPO_ROOT)
    version = projects["untaped"]["version"]
    for name in ("ansible", "workspace"):
        project = projects[f"untaped-{name}"]
        assert project["dependencies"] == [f"untaped=={version}", f"untaped-github=={version}"]


def test_core_holds_only_the_registry_under_capabilities() -> None:
    root = PACKAGES / "untaped/src/untaped/capabilities"
    assert sorted(p.name for p in root.iterdir() if p.name != "__pycache__") == [
        "__init__.py",
        "registry.py",
    ]

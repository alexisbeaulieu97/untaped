"""The workspace layout: packages, their tests, and the root configuration agree."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from typing import Any

import pytest
import release
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from repo.support import FIRST_PARTY, PACKAGES, REPO_ROOT

EXPECTED_MEMBERS = [
    "untaped",
    "untaped-ansible",
    "untaped-awx",
    "untaped-dotfiles",
    "untaped-git",
    "untaped-github",
    "untaped-jira",
    "untaped-lint",
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
    scripts = [
        "scripts/changelog.py",
        "scripts/check_pr.py",
        "scripts/gen_config_reference.py",
        "scripts/release.py",
    ]
    # This repository's own lint rules (.untaped/lint) are type-checked like shipped code.
    assert sorted(config["tool"]["mypy"]["files"]) == sorted([*src, *scripts, ".untaped/lint"])
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


def test_the_installed_untaped_lists_every_first_party_plugin() -> None:
    """A fresh process: the editable installs' entry-point metadata is current."""
    listed = subprocess.run(
        [sys.executable, "-m", "untaped", "plugin", "list", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert listed.returncode == 0, listed.stderr
    names = [row["name"] for row in json.loads(listed.stdout) if not row["root"]]
    assert names == list(FIRST_PARTY)


def test_plugin_packages_declare_their_entry_point_and_pin_core() -> None:
    projects = release.packages(REPO_ROOT)
    core = projects.pop("untaped")
    version = core["version"]
    for project in projects.values():
        name = project["name"].removeprefix("untaped-")
        assert project["version"] == version
        [target] = project["entry-points"]["untaped.plugins"].values()
        assert project["entry-points"]["untaped.plugins"] == {name: target}
        # an 11.x spec, or the plugin class of the one plugin shape
        assert target in {f"untaped_{name}:SPEC", f"untaped_{name}.plugin:{name.title()}"}
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
    for example in sorted(REPO_ROOT.glob("examples/*/pyproject.toml")):
        project = tomllib.loads(example.read_text())["project"]
        floors[example.parent.relative_to(REPO_ROOT).as_posix()] = project["requires-python"]
    template = re.search(
        r'^requires-python = "(.+)"$', (REPO_ROOT / "docs/plugins.md").read_text(), re.M
    )
    assert template is not None
    floors["docs/plugins.md"] = template.group(1)
    floor = floors["untaped"]
    assert floors == dict.fromkeys(floors, floor)
    assert Version("3.14.0") not in SpecifierSet(floor)


def test_dependent_plugins_pin_their_siblings() -> None:
    projects = release.packages(REPO_ROOT)
    version = projects["untaped"]["version"]
    siblings = {
        "ansible": ("untaped-git", "untaped-github"),
        "github": ("untaped-git",),
        "workspace": ("untaped-git",),
    }
    for name, needs in siblings.items():
        project = projects[f"untaped-{name}"]
        pinned = [dep for dep in project["dependencies"] if dep.startswith("untaped")]
        assert pinned == [f"{dist}=={version}" for dist in ("untaped", *needs)]


#: The first release where a ``SkillAsset`` without ``source`` is a stub.
STUBS_SINCE = Version("11.1.0")


def test_the_example_plugin_leaves_out_releases_without_stub_skills() -> None:
    # untaped-hello's skill is a stub, which 11.0 refuses (its SkillAsset needs a source).
    core = tomllib.loads((PACKAGES / "untaped" / "pyproject.toml").read_text(encoding="utf-8"))
    if Version(core["project"]["version"]) < STUBS_SINCE:
        pytest.skip("no release has stub skills yet, so the floor can't rise")
    hello = REPO_ROOT / "examples" / "untaped-hello" / "pyproject.toml"
    project = tomllib.loads(hello.read_text(encoding="utf-8"))["project"]
    stale = [
        line
        for line in project["dependencies"]
        if (requirement := Requirement(line)).name == "untaped"
        and requirement.specifier.contains("11.0.0")
    ]
    assert not stale, f"raise examples/untaped-hello's floor to untaped>=11.1: {stale}"

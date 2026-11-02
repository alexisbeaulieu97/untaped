"""The workspace layout: packages, their tests, and the root configuration agree."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from typing import Any

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
        "scripts/upgrade_smoke.py",
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
        # the plugin class, named after the plugin
        # (``WorkspacePlugin`` where a record holds the name: ``Workspace``)
        shaped = {f"untaped_{name}.plugin:{name.title()}{end}" for end in ("", "Plugin")}
        assert target in shaped
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
        "awx": ("untaped-git", "untaped-lint"),
        "dotfiles": ("untaped-git",),
        "github": ("untaped-git", "untaped-workspace"),
        "lint": ("untaped-git",),
        "recipe": ("untaped-git",),
        "workspace": ("untaped-git",),
    }
    for name, needs in siblings.items():
        project = projects[f"untaped-{name}"]
        pinned = [dep for dep in project["dependencies"] if dep.startswith("untaped")]
        assert pinned == [f"{dist}=={version}" for dist in ("untaped", *needs)]


def test_the_examples_declare_a_floor_of_this_major_of_untaped() -> None:
    """Each example requires ``untaped`` by a floor in the core's major, ``>=X.m``, no upper limit.

    Plugins declare floors only, so one stale plugin never holds back an install.
    The release PR raises the floors with a major, so a major bump fails here until it does.
    """
    core = tomllib.loads((PACKAGES / "untaped" / "pyproject.toml").read_text(encoding="utf-8"))
    version = Version(Version(core["project"]["version"]).base_version)
    wrong = []
    for path in sorted((REPO_ROOT / "examples").glob("*/pyproject.toml")):
        project = tomllib.loads(path.read_text(encoding="utf-8"))
        groups = project.get("dependency-groups", {})
        required = [Requirement(line) for line in project["project"]["dependencies"]]
        if not any(requirement.name == "untaped" for requirement in required):
            wrong.append(f"{path.relative_to(REPO_ROOT)}: no untaped requirement")
        lines = [
            *project["project"]["dependencies"],
            *(each for group in groups.values() for each in group if isinstance(each, str)),
        ]
        for line in lines:
            requirement = Requirement(line)
            if not requirement.name.startswith("untaped"):
                continue
            operators = {each.operator for each in requirement.specifier}
            floors = [
                Version(each.version) for each in requirement.specifier if each.operator == ">="
            ]
            if operators - {">=", "!="} or not floors:
                wrong.append(f"{path.relative_to(REPO_ROOT)}: {line} is not a floor")
            elif requirement.name == "untaped" and (
                floors[0] > version or floors[0].major != version.major
            ):
                wrong.append(f"{path.relative_to(REPO_ROOT)}: {line}")
    major = f"untaped>={version.major}.0 (or a later minor of {version.major})"
    assert not wrong, f"set each to {major}:\n" + "\n".join(wrong)

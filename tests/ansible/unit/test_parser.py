"""Tests for parsing Ansible role dependency declarations."""

from __future__ import annotations

import pytest

from untaped.capabilities.ansible.domain.parser import parse_dependency_file

_TEMPLATED = "---\ngalaxy_info:\n  role_name: {@ role_slug @}\n"


def test_parse_requirements_roles_from_list_entries() -> None:
    report = parse_dependency_file(
        "roles/requirements.yml",
        """
        - src: https://github.com/acme/base
          version: v1.2.3
          name: base_role
        - geerlingguy.apache
        """,
    )

    assert [(dep.name, dep.src, dep.version) for dep in report.dependencies] == [
        ("base_role", "https://github.com/acme/base", "v1.2.3"),
        ("geerlingguy.apache", "geerlingguy.apache", None),
    ]
    assert report.ignored_collections == ()


def test_parse_requirements_roles_from_roles_key_and_reports_collections() -> None:
    report = parse_dependency_file(
        "requirements.yml",
        """
        roles:
          - src: git+https://github.com/acme/users.git
            version: main
        collections:
          - name: community.general
        """,
    )

    assert [(dep.name, dep.src, dep.version) for dep in report.dependencies] == [
        ("users", "git+https://github.com/acme/users.git", "main")
    ]
    assert report.ignored_collections == ("community.general",)


def test_parse_meta_main_dependencies_from_simple_and_complex_entries() -> None:
    report = parse_dependency_file(
        "meta/main.yml",
        """
        dependencies:
          - common
          - role: apache
            vars:
              port: 80
          - name: composer
            src: git+https://github.com/acme/composer.git
            version: 7753962
        """,
    )

    assert [(dep.name, dep.src, dep.version) for dep in report.dependencies] == [
        ("common", "common", None),
        ("apache", "apache", None),
        ("composer", "git+https://github.com/acme/composer.git", "7753962"),
    ]


@pytest.mark.parametrize(
    ("path", "content", "reason"),
    [
        ("roles/requirements.yml", "", None),
        ("meta/main.yml", "---\n", None),
        ("meta/main.yml", "dependencies:\n", None),
        ("meta/main.yml", "dependencies: null\n", None),
        ("requirements.yml", "roles: []\ncollections:\n", None),
        ("README.yml", "name: not a dependency file", "unsupported dependency file"),
        ("deps/custom.yml", "- src: acme/base\n", "unsupported dependency file"),
        ("meta/main.yml", _TEMPLATED, "could not parse dependency YAML"),
        ("meta/main.yml", "- common\n", "expected mapping at top level"),
        ("requirements.yml", "42\n", "expected mapping or list at top level"),
        ("meta/main.yml", "dependencies:\n  common: {}\n", "expected list at dependencies"),
        # blank, non-mapping and name-less entries are skipped silently
        ("requirements.yml", "- ''\n- [nested]\n- {version: v1}\n", None),
    ],
)
def test_files_without_dependencies_warn_only_when_malformed(
    path: str, content: str, reason: str | None
) -> None:
    report = parse_dependency_file(path, content)

    assert (report.dependencies, report.ignored_collections) == ((), ())
    assert [(warning.source_path, warning.reason) for warning in report.warnings] == (
        [] if reason is None else [(path, reason)]
    )


def test_parse_wrong_shaped_nested_dependency_sections_warn() -> None:
    invalid_roles_report = parse_dependency_file(
        "requirements.yml",
        """
        roles: "{{ roles }}"
        collections:
          - name: community.general
        """,
    )
    invalid_collections_report = parse_dependency_file(
        "requirements.yml",
        """
        roles:
          - src: https://github.com/acme/base
        collections: community.general
        """,
    )

    assert invalid_roles_report.dependencies == ()
    assert invalid_roles_report.ignored_collections == ("community.general",)
    assert [(warning.source_path, warning.reason) for warning in invalid_roles_report.warnings] == [
        ("requirements.yml", "expected list at roles")
    ]
    assert [
        (dep.name, dep.src, dep.version) for dep in invalid_collections_report.dependencies
    ] == [("base", "https://github.com/acme/base", None)]
    assert invalid_collections_report.ignored_collections == ()
    assert [
        (warning.source_path, warning.reason) for warning in invalid_collections_report.warnings
    ] == [("requirements.yml", "expected list at collections")]


def test_numeric_looking_versions_keep_their_original_text() -> None:
    report = parse_dependency_file(
        "requirements.yml",
        "roles:\n  - src: acme/base\n    version: 1.10\n  - src: acme/users\n    version: 2\n",
    )

    assert [dep.version for dep in report.dependencies] == ["1.10", "2"]


def test_meta_main_yaml_extension_is_supported() -> None:
    report = parse_dependency_file("meta/main.yaml", "dependencies:\n  - src: acme/base\n")

    assert [dep.src for dep in report.dependencies] == ["acme/base"]
    assert report.warnings == ()

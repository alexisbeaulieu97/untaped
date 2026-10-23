"""The setup screen reads and writes only through the config use cases, and names no setting.

The screen and ``config set`` cannot disagree because they run the same code:
the screen's modules import from ``untaped.core.config`` only the use cases, the
repository class, the records and the field mapping, and touch neither the
config file nor YAML; across the code base, only the repositories and the
editor write the file. The screen knows shapes, not settings: no string in its
modules is a section, a key or a field name of a first-party setting, so a
setting gets its row from its model, never from a case in the screen; and every
first-party setting has a shape with a field, so none falls back to JSON.
"""

from __future__ import annotations

import ast

from repo.support import PACKAGES
from untaped.core.config.fields import shape
from untaped.runtime import bootstrap
from untaped.runtime.settings import get_profile_settings_model
from untaped.sdk._schema import walk_settings

SRC = PACKAGES / "untaped" / "src" / "untaped"
SCREEN = ("core/commands/setup_screen.py", "core/commands/setup_sections.py")
#: The setup form, and the state behind it, which reads 11.0's token settings by name.
CONNECTION = "core/commands/setup_connection.py"
CONNECTION_KEYS = "core/commands/setup_state.py"
ALLOWED_CONFIG = frozenset(
    {
        "untaped.core.config.use_cases",
        "untaped.core.config.repository",
        "untaped.core.config.models",  # records and their display, no reader or writer
        "untaped.core.config.fields",
    }
)
BANNED_MODULES = frozenset(
    {"untaped.runtime.config_file", "yaml", "untaped.runtime.yaml_roundtrip"}
)
BANNED_CALLS = frozenset(
    {"load_settings_section", "read_config_dict", "mutate_config", "set_value", "unset_value"}
)
#: The modules that may write the config file.
FILE_WRITERS = frozenset(
    {
        "untaped/core/config/repository.py",
        "untaped/core/commands/config_editor.py",
        "untaped/core/profile/repository.py",
        "untaped/runtime/config_file.py",
    }
)
#: Strings of the screen's modules that are also a setting's name, and why each is not one.
ALLOWED_NAMES: dict[str, dict[str, str]] = {
    CONNECTION: {
        "url": "an acronym field labels write upper case (`Base URL`), not a setting's name",
    },
    CONNECTION_KEYS: {
        name: "11.0's token settings, read from the file until 12.0 removes them"
        for name in ("token", "token_command")
    },
}


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _calls(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _strings(tree: ast.Module) -> set[str]:
    """Every string constant, f-string parts included; docstrings are prose and left out."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def _setting_names() -> set[str]:
    bootstrap.compose_root()
    names: set[str] = set()
    for descriptor in walk_settings(get_profile_settings_model(), include_collections=True):
        names |= {descriptor.key, *descriptor.path, ".".join(descriptor.path[1:])}
    return names


def _tree(name: str) -> ast.Module:
    return ast.parse((SRC / name).read_text(encoding="utf-8"))


def test_the_screen_reads_and_writes_through_the_config_use_cases() -> None:
    found: list[str] = []
    for name in (*SCREEN, CONNECTION):
        tree = _tree(name)
        imports = _imports(tree)
        found += [
            f"{name} imports {module}"
            for module in sorted(imports)
            if module in BANNED_MODULES
            or (module.startswith("untaped.core.config.") and module not in ALLOWED_CONFIG)
        ]
        found += [f"{name} calls {call}" for call in sorted(_calls(tree) & BANNED_CALLS)]
    assert not found, (
        f"{found}: the setup screen reads through ListSettings and writes through the config "
        "use cases, so it can't disagree with `config set`"
    )


def test_only_the_repositories_and_the_editor_write_the_config_file() -> None:
    root = SRC.parent
    found = [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*.py"))
        if path.relative_to(root).as_posix() not in FILE_WRITERS
        and {"mutate_config", "replace_config_text"}
        & {
            alias.name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
    ]
    assert not found, f"{found}: write the config file through a repository's write"


def test_the_screen_names_no_setting() -> None:
    settings = _setting_names()
    found: list[str] = []
    for name in (*SCREEN, CONNECTION, CONNECTION_KEYS):
        allowed = ALLOWED_NAMES.get(name, {})
        found += [
            f"{name}: {text!r}"
            for text in sorted(_strings(_tree(name)) & settings)
            if text not in allowed
        ]
    assert not found, (
        f"{found}: the setup screen knows shapes, not settings; read the setting from its "
        "model (or allow the string here with the reason it is not a setting's name)"
    )


def test_every_first_party_setting_gets_a_field_of_its_shape() -> None:
    bootstrap.compose_root()
    found = [
        descriptor.key
        for descriptor in walk_settings(get_profile_settings_model(), include_collections=True)
        if shape(descriptor) == "json"
    ]
    assert not found, (
        f"{found}: these settings open as one line of JSON in `untaped setup`; give their "
        "shape a field in untaped.core.config.fields"
    )

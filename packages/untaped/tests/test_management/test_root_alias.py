"""``untaped alias``: per-profile command aliases stored in ``shell.aliases``.

``untaped NAME [ARGS…]`` runs the alias's argv with ``ARGS`` appended. An
alias never shadows a built-in command and never expands another alias.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from test_management.support import GithubProfile, make_spec, write_config
from untaped import bootstrap, config_file
from untaped.config_file import read_config_dict, write_config_dict
from untaped.testing import CliInvoker, CliResult, provider_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

_CONFIG = (
    "profiles:\n"
    "  default:\n    github:\n      base_url: https://d\n"
    "  work:\n    github:\n      base_url: https://w\n"
    "active: default\n"
)


def _invoke(*argv: str, interactive: bool = False) -> CliResult:
    root = bootstrap.build_root_app(
        candidates=(provider_candidate(make_spec("github", profile_model=GithubProfile)),)
    )
    return CliInvoker().invoke(root.meta, list(argv), interactive=interactive)


def _aliases(path: Path, profile: str = "default") -> Any:
    return read_config_dict(path)["profiles"][profile].get("shell", {}).get("aliases")


def test_set_then_invoke_an_alias_with_extra_args(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    created = _invoke(
        "alias", "set", "gb", "--format", "json", "--", "config", "get", "github.base_url"
    )
    assert created.exit_code == 0, created.output
    assert json.loads(created.stdout) == {"name": "gb", "profile": "default", "action": "created"}
    assert _aliases(_isolated_config) == {"gb": ["config", "get", "github.base_url"]}

    ran = _invoke("gb")
    assert ran.exit_code == 0, ran.output
    assert ran.stdout.strip() == "https://d"
    extra = _invoke("gb", "--format", "json")
    assert json.loads(extra.stdout)["value"] == "https://d"
    assert _invoke("--profile", "work", "gb").stdout.strip() == "https://w"


def test_an_alias_may_start_with_root_options(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    argv = ("alias", "set", "wb", "--", "--profile", "work", "config", "get", "github.base_url")
    assert _invoke(*argv).exit_code == 0
    assert _invoke("wb").stdout.strip() == "https://w"


def test_set_reports_unchanged_updated_and_planned(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    base = ("alias", "set", "ls", "--format", "json", "--")
    assert json.loads(_invoke(*base, "config", "list").stdout)["action"] == "created"
    assert json.loads(_invoke(*base, "config", "list").stdout)["action"] == "unchanged"
    planned = _invoke(
        "alias", "set", "ls", "--dry-run", "--format", "json", "--", "profile", "list"
    )
    assert json.loads(planned.stdout)["action"] == "planned"
    assert _aliases(_isolated_config) == {"ls": ["config", "list"]}
    assert json.loads(_invoke(*base, "profile", "list").stdout)["action"] == "updated"
    assert _aliases(_isolated_config) == {"ls": ["profile", "list"]}


@pytest.mark.parametrize(
    ("options", "profile"),
    [(["--profile", "work"], "work"), (["-v"], "default"), (["--quiet"], "default")],
)
def test_root_options_before_the_separator_apply_to_alias_set(
    _isolated_config: Path, options: list[str], profile: str
) -> None:
    write_config(_isolated_config, _CONFIG)
    result = _invoke("alias", "set", "wp", *options, "--", "profile", "list")
    assert result.exit_code == 0, result.output
    assert _aliases(_isolated_config, profile) == {"wp": ["profile", "list"]}


def test_the_alias_is_looked_up_in_the_profile_named_anywhere(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        _CONFIG.replace(
            "  work:\n", "  work:\n    shell:\n      aliases:\n        wb: [config, list]\n"
        ),
    )
    before = _invoke("--profile", "work", "wb", "--format", "raw")
    after = _invoke("wb", "--format", "raw", "--profile", "work")
    assert before.exit_code == after.exit_code == 0, after.output
    assert after.stdout == before.stdout
    assert "github.base_url" in after.stdout
    separated = _invoke("wb", "--", "--profile", "work")
    assert separated.exit_code == 2


def test_set_writes_into_the_root_profile(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    result = _invoke("--profile", "work", "alias", "set", "pl", "--", "profile", "list")
    assert result.exit_code == 0, result.output
    assert _aliases(_isolated_config, "work") == {"pl": ["profile", "list"]}
    assert _aliases(_isolated_config) is None


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["config", "--", "profile", "list"], "would shadow the built-in command 'config'"),
        (["github", "--", "profile", "list"], "would shadow the built-in command 'github'"),
        (["git-hub", "--", "profile", "list"], "would shadow the built-in command 'github'"),
        (["alias", "--", "profile", "list"], "would shadow the built-in command 'alias'"),
        (["Bad_Name", "--", "profile", "list"], "alias name must be"),
        (["empty"], "alias set requires a command"),
    ],
)
def test_set_rejects_bad_aliases(_isolated_config: Path, argv: list[str], message: str) -> None:
    write_config(_isolated_config, _CONFIG)
    result = _invoke("alias", "set", *argv)
    assert result.exit_code == 2
    assert message in result.stderr
    assert _aliases(_isolated_config) is None


def test_aliases_never_expand_recursively(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n"
        "        outer: [inner]\n        inner: [config, list]\n",
    )
    assert _invoke("inner").exit_code == 0
    result = _invoke("outer")
    assert result.exit_code == 2
    assert "inner" in result.stderr


def test_built_in_commands_win_over_a_stored_alias(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n        profile: [config, list]\n",
    )
    result = _invoke("profile", "current")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "default"


def test_list_merges_default_and_active_profiles(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n"
        "  default:\n    shell:\n      aliases:\n        a: [config, list]\n"
        "  work:\n    shell:\n      aliases:\n        b: [config, get, 'ui.theme']\n",
    )
    result = _invoke("--profile", "work", "alias", "list", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"name": "a", "command": "config list", "argv": ["config", "list"], "profile": "default"},
        {
            "name": "b",
            "command": "config get ui.theme",
            "argv": ["config", "get", "ui.theme"],
            "profile": "work",
        },
    ]
    empty = _invoke("alias", "list")
    assert "b" not in empty.stdout


def test_list_is_empty_without_aliases(_isolated_config: Path) -> None:
    result = _invoke("alias", "list")
    assert result.exit_code == 0, result.output
    assert "No aliases found." in result.stderr


def test_remove_needs_yes_when_not_interactive(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n        a: [config, list]\n",
    )
    refused = _invoke("alias", "remove", "a")
    assert refused.exit_code == 2
    assert "--yes" in refused.stderr
    planned = _invoke("alias", "remove", "a", "--dry-run", "--format", "json")
    assert json.loads(planned.stdout)["action"] == "planned"
    assert _aliases(_isolated_config) == {"a": ["config", "list"]}
    removed = _invoke("alias", "remove", "a", "--yes", "--format", "json")
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.stdout) == {"name": "a", "profile": "default", "action": "deleted"}
    assert _aliases(_isolated_config) is None


def _commit_before_lock(
    monkeypatch: pytest.MonkeyPatch, commit: Callable[[dict[str, Any]], None]
) -> None:
    """Let another writer commit ``commit`` just before this command takes the config lock."""
    real_lock = config_file.file_lock
    pending = [commit]

    @contextlib.contextmanager
    def racing_lock(*args: Any, **kwargs: Any) -> Iterator[None]:
        if pending:
            data = read_config_dict()
            pending.pop()(data)
            write_config_dict(data)
        with real_lock(*args, **kwargs):
            yield

    monkeypatch.setattr(config_file, "file_lock", racing_lock)


def _add_alias_b(data: dict[str, Any]) -> None:
    data["profiles"]["default"].setdefault("shell", {}).setdefault("aliases", {})["b"] = [
        "profile",
        "list",
    ]


def test_set_keeps_an_alias_another_command_saved_meanwhile(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _CONFIG)
    _commit_before_lock(monkeypatch, _add_alias_b)
    result = _invoke("alias", "set", "a", "--format", "json", "--", "config", "list")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["action"] == "created"
    assert _aliases(_isolated_config) == {"a": ["config", "list"], "b": ["profile", "list"]}


def test_set_reports_the_action_against_the_saved_aliases(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _CONFIG)
    _commit_before_lock(monkeypatch, _add_alias_b)
    result = _invoke("alias", "set", "b", "--format", "json", "--", "profile", "list")
    assert json.loads(result.stdout)["action"] == "unchanged"


def test_remove_keeps_an_alias_another_command_saved_meanwhile(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n        a: [config, list]\n",
    )
    _commit_before_lock(monkeypatch, _add_alias_b)
    result = _invoke("alias", "remove", "a", "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    assert _aliases(_isolated_config) == {"b": ["profile", "list"]}


def test_remove_reports_an_alias_another_command_removed_meanwhile(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n        a: [config, list]\n"
        "        c: [profile, list]\n",
    )
    _commit_before_lock(
        monkeypatch, lambda data: data["profiles"]["default"]["shell"]["aliases"].pop("a")
    )
    result = _invoke("alias", "remove", "a", "--yes")
    assert result.exit_code == 1
    assert "alias not found: 'a'; known: c" in result.stderr
    assert _aliases(_isolated_config) == {"c": ["profile", "list"]}


def test_remove_an_unknown_alias_names_the_known_ones(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n        a: [config, list]\n",
    )
    result = _invoke("alias", "remove", "nope", "--yes")
    assert result.exit_code == 1
    assert "alias not found: 'nope'; known: a" in result.stderr


def test_remove_an_inherited_alias_points_at_its_profile(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        _CONFIG.replace(
            "  default:\n", "  default:\n    shell:\n      aliases:\n        a: [config, list]\n"
        ),
    )
    result = _invoke("--profile", "work", "alias", "remove", "a", "--yes")
    assert result.exit_code == 1
    assert "alias 'a' is defined in profile default" in result.stderr
    assert "hint: run `untaped --profile default alias remove a`" in result.stderr


def test_a_malformed_stored_alias_is_reported(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n        a: config list\n",
    )
    result = _invoke("alias", "remove", "a", "--yes")
    assert result.exit_code == 4  # the stored config is malformed
    assert "shell.aliases" in result.stderr
    assert "Traceback" not in result.output


def test_config_rejects_an_invalid_alias(_isolated_config: Path) -> None:
    result = _invoke("config", "set", "shell.aliases", '{"Bad Name": ["config", "list"]}')
    assert result.exit_code == 1
    assert "invalid value for 'shell.aliases'" in result.stderr


def test_list_table_shows_the_default_columns(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    shell:\n      aliases:\n        a: [config, list]\n",
    )
    result = _invoke("alias", "list")
    assert result.exit_code == 0, result.output
    header = [cell.strip() for cell in result.stdout.splitlines()[1].strip("│").split("│")]
    assert header == ["name", "command", "profile"]


_DEPRECATION = (
    "is deprecated and will be removed in the next major release; use a shell alias or function"
)


_COMMAND_WARNING = f"warning: `untaped alias` {_DEPRECATION}\n"


@pytest.mark.parametrize(
    "argv",
    [
        ("alias", "set", "gb", "--", "config", "list"),
        ("alias", "list"),
        ("alias", "remove", "gb", "--yes"),
    ],
)
def test_every_alias_command_warns_once_that_the_command_is_deprecated(
    _isolated_config: Path, argv: tuple[str, ...]
) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    shell:\n      aliases:\n        gb: [x]\n"
    )
    result = _invoke(*argv)
    assert result.exit_code == 0, result.output
    assert result.stderr.count(_COMMAND_WARNING) == 1


def test_alias_help_and_version_print_no_deprecation_warning(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    for argv in (("alias", "--help"), ("alias", "set", "--help"), ("--version",)):
        assert "deprecated" not in _invoke(*argv).stderr


def test_using_a_stored_alias_warns_once(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    assert _invoke("alias", "set", "gb", "--", "config", "list").exit_code == 0
    ran = _invoke("gb")
    assert ran.exit_code == 0, ran.output
    assert ran.stderr.count(_DEPRECATION) == 1
    assert f"shell.aliases {_DEPRECATION}" in ran.stderr


def test_any_command_warns_once_while_an_alias_is_stored(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    assert "deprecated" not in _invoke("config", "list").stderr
    assert _invoke("alias", "set", "gb", "--", "config", "list").exit_code == 0
    listed = _invoke("config", "list")
    assert listed.stderr.count(f"shell.aliases {_DEPRECATION}") == 1

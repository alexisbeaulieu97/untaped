"""A 12.0 plugin class's blocks and kinds reach core's 11.x commands: config, setup, doctor."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel

from untaped.runtime.bootstrap import build_root_app, compose_root
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import (
    Cache,
    Command,
    Credential,
    CredentialKey,
    DeprecatedBehaviour,
    DirMigration,
    DoctorCheck,
    DoctorResult,
    Filled,
    Key,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    Output,
    Plugin,
    PluginContext,
    ProgressHandle,
    Refresh,
)
from untaped.sdk.testing import CliInvoker, CliResult

NOTES = Cache(label="notes", max_age=timedelta(hours=1))
FILLED: list[str] = []


class KeeperSettings(BaseModel):
    pass


class KeeperServer(CredentialKey):
    name: str = "keeper"


class Note(BaseModel):
    """One note kept."""

    title: Annotated[str, Key]


class Read(Command[KeeperSettings]):
    """Read the notes, filling the cache on first use."""

    refresh: Refresh = Refresh(NOTES)

    def run(self) -> str:
        def fill(_key: str, _progress: ProgressHandle) -> Filled:
            FILLED.append("filled")
            return Filled(summary="1 note")

        NOTES.serve({"all": "all"}, refresh=self.refresh.directive, fill=fill, read=lambda: "")
        return f"filled {len(FILLED)}"


class Show(Command[KeeperSettings]):
    """Show one note."""

    output: Output[Note]

    def run(self) -> Note:
        return Note(title="first")


def _preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
    return [MigrationRow(action="move", source="/old", destination="/new", bytes=0)]


def _apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
    return [MigrationOutcome(id="keeper.old-notes", action="unchanged")]


def _healthy(_ctx: PluginContext) -> DoctorResult:
    return DoctorResult(id="keeper.readable", ok=True, detail="the notes are readable")


class Keeper(Plugin):
    """Keep notes (a 12.0 plugin)."""

    settings = KeeperSettings
    commands = (Read, Show)
    notes = NOTES
    old_notes = DirMigration(title="notes into the plugin's folder", preview=_preview, apply=_apply)
    readable = DoctorCheck(title="notes are readable", run=_healthy)
    token = Credential(key=KeeperServer, env=("KEEPER_TOKEN",))
    plain_notes = DeprecatedBehaviour(removed_in=13, replacement="untaped keeper read")


KEEPER = PluginCandidate(distribution="untaped-keeper", name="keeper", target=Keeper)


def _run(*argv: str) -> CliResult:
    root = build_root_app(candidates=[KEEPER])
    return CliInvoker().invoke(root.meta, list(argv))


def test_a_cache_on_the_class_has_its_setting_and_serves_under_the_plugin() -> None:
    shown = _run("config", "get", "keeper.caches.notes.max_age_seconds")
    assert shown.exit_code == 0, shown.stderr
    assert shown.stdout.strip() == "3600"

    FILLED.clear()
    assert _run("keeper", "read").stdout == "filled 1\n"
    assert _run("keeper", "read").stdout == "filled 1\n"
    assert (Path.home() / ".untaped" / "plugins" / "keeper").is_dir()


def test_a_migration_on_the_class_is_listed_by_setup() -> None:
    preview = _run("setup", "migrate-dirs", "--dry-run", "--format", "json")
    assert preview.exit_code == 0, preview.stderr
    assert [(row["id"], row["action"]) for row in json.loads(preview.stdout)] == [
        ("keeper.old-notes", "move")
    ]


def test_a_doctor_check_on_the_class_runs_under_its_id() -> None:
    result = _run("doctor", "--format", "json")
    rows = {row["check"]: row for row in json.loads(result.stdout)}
    assert rows["keeper.readable"]["status"] == "pass"


def test_plugin_schema_finds_a_kind_the_declaration_names() -> None:
    result = _run("plugin", "schema", "keeper.note")
    assert result.exit_code == 0, result.stderr
    schema = json.loads(result.stdout)
    assert (schema["$id"], list(schema["properties"])) == ("keeper.note", ["title"])


def test_credentials_and_behaviours_reach_the_spec_named_and_compose_again() -> None:
    first = compose_root(candidates=[KEEPER])
    again = compose_root(candidates=[KEEPER])
    assert not first.quarantine and not again.quarantine
    [plugin] = again.plugins
    [token] = plugin.spec.credentials
    assert token.qualified == "keeper.token"
    assert token is first.plugins[0].spec.credentials[0]
    assert [each.name for each in plugin.spec.deprecations()] == ["plain-notes"]
    assert _run("keeper", "show").exit_code == 0


def test_an_unknown_kind_suggests_the_declaration_s_kinds() -> None:
    result = _run("plugin", "schema", "keeper.notte")
    assert result.exit_code == 2
    assert "keeper.note" in result.stderr

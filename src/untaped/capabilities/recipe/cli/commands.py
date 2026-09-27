"""Cyclopts composition root for ``untaped recipe``: recipe verbs plus noun groups."""

from __future__ import annotations

from untaped.capabilities.recipe.cli import backup_commands
from untaped.capabilities.recipe.cli.apply_commands import apply_command
from untaped.capabilities.recipe.cli.hook_commands import run_command as run_hook_command
from untaped.capabilities.recipe.cli.library_commands import (
    add_command,
    edit_command,
    edit_hook_command,
    edit_pack_command,
    get_command,
    get_hook_command,
    get_pack_command,
    list_command,
    list_hooks_command,
    list_packs_command,
    remove_command,
    sync_command,
    validate_command,
)
from untaped.capabilities.recipe.cli.new_commands import (
    init_hook_command,
    init_pack_command,
    init_recipe_command,
)
from untaped.capabilities.recipe.cli.test_commands import test_command
from untaped.capability_api import create_app

packs_app = create_app(name="packs", help="Install, sync, inspect, and scaffold recipe packs.")
packs_app.command(list_packs_command, name="list")
packs_app.command(get_pack_command, name="get")
packs_app.command(edit_pack_command, name="edit")
packs_app.command(init_pack_command, name="init")
packs_app.command(add_command, name="add")
packs_app.command(sync_command, name="sync")
packs_app.command(remove_command, name="remove")

hooks_app = create_app(name="hooks", help="Inspect, scaffold, and run recipe hooks.")
hooks_app.command(list_hooks_command, name="list")
hooks_app.command(get_hook_command, name="get")
hooks_app.command(edit_hook_command, name="edit")
hooks_app.command(init_hook_command, name="init")
hooks_app.command(run_hook_command, name="run")

backups_app = create_app(name="backups", help="Manage recipe backups.")
backups_app.command(backup_commands.list_command, name="list")
backups_app.command(backup_commands.get_command, name="get")
backups_app.command(backup_commands.restore_command, name="restore")
backups_app.command(backup_commands.prune_command, name="prune")

app = create_app(name="recipe", help="Apply reusable local recipes to plain directories.")
app.command(apply_command, name="apply")
app.command(list_command, name="list")
app.command(get_command, name="get")
app.command(edit_command, name="edit")
app.command(init_recipe_command, name="init")
app.command(validate_command, name="validate")
app.command(test_command, name="test")
app.command(packs_app, name="packs")
app.command(hooks_app, name="hooks")
app.command(backups_app, name="backups")

"""Cyclopts composition root for ``untaped recipe``."""

from __future__ import annotations

from untaped.capabilities.recipe.cli.apply_commands import apply_command
from untaped.capabilities.recipe.cli.backup_commands import app as backup_app
from untaped.capabilities.recipe.cli.hook_commands import app as hook_app
from untaped.capabilities.recipe.cli.library_commands import (
    add_command,
    edit_command,
    get_command,
    list_command,
    remove_command,
    sync_command,
    validate_command,
)
from untaped.capabilities.recipe.cli.new_commands import init_command
from untaped.capabilities.recipe.cli.test_commands import test_command
from untaped.capability_api import create_app, deprecated_alias

app = create_app(name="recipe", help="Apply reusable local recipes to plain directories.")
app.command(init_command, name="init")
app.command(hook_app, name="hook")
app.command(backup_app, name="backup")
app.command(test_command, name="test")
app.command(apply_command, name="apply")
app.command(add_command, name="add")
app.command(sync_command, name="sync")
app.command(list_command, name="list")
app.command(get_command, name="get")
app.command(validate_command, name="validate")
app.command(remove_command, name="remove")
app.command(edit_command, name="edit")

deprecated_alias(app, "new", "init")
deprecated_alias(backup_app, "show", "get")
deprecated_alias(app, "show", "get")
deprecated_alias(app, "check", "validate")
deprecated_alias(app["apply"], "--vars", "--vars-file")

"""Cyclopts composition root for ``untaped recipe``."""

from __future__ import annotations

from untaped.capabilities.recipe.cli.apply_commands import apply_command
from untaped.capabilities.recipe.cli.backup_commands import app as backup_app
from untaped.capabilities.recipe.cli.hook_commands import app as hook_app
from untaped.capabilities.recipe.cli.library_commands import (
    add_command,
    check_command,
    edit_command,
    list_command,
    remove_command,
    show_command,
)
from untaped.capabilities.recipe.cli.new_commands import app as new_app
from untaped.capabilities.recipe.cli.test_commands import test_command
from untaped.capability_api import create_app

app = create_app(name="recipe", help="Apply reusable local recipes to plain directories.")
app.command(new_app, name="new")
app.command(hook_app, name="hook")
app.command(backup_app, name="backup")
app.command(test_command, name="test")
app.command(apply_command, name="apply")
app.command(add_command, name="add")
app.command(list_command, name="list")
app.command(show_command, name="show")
app.command(check_command, name="check")
app.command(remove_command, name="remove")
app.command(edit_command, name="edit")

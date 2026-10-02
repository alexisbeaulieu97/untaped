"""Cyclopts app composition for Ansible dependency graphing."""

from __future__ import annotations

from untaped.capabilities.ansible.cli.graph_commands import register_graph_commands
from untaped.capabilities.ansible.cli.source_alias_commands import app as source_alias_app
from untaped.capabilities.ansible.cli.source_commands import app as source_app
from untaped.sdk import create_app

app = create_app(name="ansible", help="Analyze Ansible dependency graphs.")


app.command(source_app, name="source")
app.command(source_alias_app, name="source-alias")
register_graph_commands(app)

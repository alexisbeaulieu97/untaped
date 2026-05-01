"""Root Typer app — aggregates every domain's sub-app.

Each domain package exposes ``app: typer.Typer`` from its top-level module.
Adding a new domain is two lines below: import + ``add_typer``.
"""

from __future__ import annotations

import typer
from untaped_awx import app as awx_app
from untaped_config import app as config_app
from untaped_github import app as github_app
from untaped_workspace import app as workspace_app

app = typer.Typer(
    name="untaped",
    help="A personal DevOps CLI suite.",
    no_args_is_help=True,
)

app.add_typer(config_app, name="config")
app.add_typer(workspace_app, name="workspace")
app.add_typer(awx_app, name="awx")
app.add_typer(github_app, name="github")


if __name__ == "__main__":
    app()

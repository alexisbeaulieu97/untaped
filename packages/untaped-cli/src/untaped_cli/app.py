import typer

from untaped_core.logging import configure_logging

from .commands import create_app, delete_app, update_app
from .common import set_verbose


app = typer.Typer(help="untaped CLI")

ansible_app = typer.Typer(help="Manage Ansible Tower resources")
ansible_app.add_typer(create_app, name="create")
ansible_app.add_typer(update_app, name="update")
ansible_app.add_typer(delete_app, name="delete")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output")) -> None:
    set_verbose(verbose)
    configure_logging(level="DEBUG" if verbose else "INFO")


@app.command()
def version() -> None:
    """Show version information."""
    import importlib.metadata

    pkg = "untaped-cli"
    try:
        v = importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        v = "0.0.0"
    typer.echo(v)


app.add_typer(ansible_app, name="ansible")
app.add_typer(create_app, name="create")
app.add_typer(update_app, name="update")
app.add_typer(delete_app, name="delete")

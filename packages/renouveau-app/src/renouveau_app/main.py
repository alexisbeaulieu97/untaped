"""Main CLI application for renouveau."""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich import print as rprint

from renouveau_core import RenouveauSettings
from .commands import tower, ansible, config as config_cmd

app = typer.Typer(
    name="renouveau",
    help="🚀 Renouveau - Modern Ansible project management tool",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

# Add subcommands
app.add_typer(tower.app, name="tower", help="🗼 Manage Ansible Tower/AWX")
app.add_typer(ansible.app, name="ansible", help="📚 Manage local Ansible content")
app.add_typer(config_cmd.app, name="config", help="⚙️ Configuration management")

console = Console()


@app.callback()
def main(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Configuration file path",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug mode",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
    ),
) -> None:
    """🚀 Renouveau - Modern Ansible project management tool.
    
    A schema-driven CLI tool for managing Ansible Tower/AWX, GitHub repositories,
    and local Ansible content with beautiful terminal interfaces.
    """
    # Initialize global settings
    if config_file:
        settings = RenouveauSettings.load_from_file(config_file)
    else:
        settings = RenouveauSettings()
    
    if debug:
        settings.debug = True
        settings.log_level = "DEBUG"
    
    if verbose:
        settings.log_level = "DEBUG"
    
    # Store settings in context for subcommands
    app.info.params = {"settings": settings}


@app.command()
def version() -> None:
    """Show version information."""
    from renouveau_core import __version__ as core_version
    from renouveau_schema import __version__ as schema_version
    
    rprint(f"[bold blue]Renouveau[/bold blue] version information:")
    rprint(f"  • Core: [green]{core_version}[/green]")
    rprint(f"  • Schema: [green]{schema_version}[/green]")
    rprint(f"  • App: [green]0.1.0[/green]")


@app.command()
def init() -> None:
    """Initialize renouveau configuration."""
    rprint("[bold blue]🚀 Initializing Renouveau...[/bold blue]")
    
    settings = RenouveauSettings()
    config_file = settings.get_default_config_file()
    
    if config_file.exists():
        if not typer.confirm(f"Configuration file already exists at {config_file}. Overwrite?"):
            rprint("[yellow]Initialization cancelled.[/yellow]")
            raise typer.Exit()
    
    # Ensure config directory exists
    settings.ensure_config_dir()
    
    # Interactive configuration
    tower_url = typer.prompt("Ansible Tower/AWX URL (optional)", default="")
    tower_token = typer.prompt("Tower API token (optional)", default="", hide_input=True)
    
    github_org = typer.prompt("GitHub organization (optional)", default="")
    github_token = typer.prompt("GitHub token (optional)", default="", hide_input=True)
    
    project_root = typer.prompt("Ansible project root", default=".")
    
    # Update settings
    if tower_url:
        settings.tower.url = tower_url
    if tower_token:
        settings.tower.token = tower_token
    if github_org:
        settings.github.organization = github_org
    if github_token:
        settings.github.token = github_token
    
    settings.ansible.project_root = Path(project_root)
    
    # Save configuration
    settings.save_to_file(config_file)
    
    rprint(f"[green]✅ Configuration saved to {config_file}[/green]")
    rprint("\n[bold]Next steps:[/bold]")
    rprint("  • Run [cyan]renouveau tower job-template list[/cyan] to test Tower connection")
    rprint("  • Run [cyan]renouveau ansible project discover[/cyan] to analyze local project")


if __name__ == "__main__":
    app()

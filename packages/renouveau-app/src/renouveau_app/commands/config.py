"""Configuration management CLI commands."""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from renouveau_core import RenouveauSettings

app = typer.Typer(help="⚙️ Configuration management")
console = Console()


@app.command("show")
def show_config(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Configuration file path",
        exists=True,
    ),
) -> None:
    """Show current configuration."""
    try:
        if config_file:
            settings = RenouveauSettings.load_from_file(config_file)
            config_source = str(config_file)
        else:
            settings = RenouveauSettings()
            config_source = "Default/Environment"
        
        rprint(f"[bold blue]⚙️ Renouveau Configuration[/bold blue]")
        rprint(f"[bold]Source:[/bold] {config_source}")
        rprint(f"[bold]Config Directory:[/bold] {settings.config_dir}")
        
        # Global settings
        rprint(f"\n[bold yellow]🌐 Global Settings:[/bold yellow]")
        rprint(f"  • Debug mode: {settings.debug}")
        rprint(f"  • Log level: {settings.log_level}")
        
        # Tower configuration
        rprint(f"\n[bold yellow]🗼 Tower/AWX Configuration:[/bold yellow]")
        if settings.tower.url:
            rprint(f"  • URL: [green]{settings.tower.url}[/green]")
        else:
            rprint("  • URL: [red]Not configured[/red]")
        
        rprint(f"  • Token configured: {'✅' if settings.tower.token else '❌'}")
        rprint(f"  • Username configured: {'✅' if settings.tower.username else '❌'}")
        rprint(f"  • Verify SSL: {settings.tower.verify_ssl}")
        rprint(f"  • Timeout: {settings.tower.timeout}s")
        
        # GitHub configuration
        rprint(f"\n[bold yellow]🐙 GitHub Configuration:[/bold yellow]")
        rprint(f"  • Base URL: {settings.github.base_url}")
        rprint(f"  • Organization: {settings.github.organization or 'Not set'}")
        rprint(f"  • Token configured: {'✅' if settings.github.token else '❌'}")
        rprint(f"  • Timeout: {settings.github.timeout}s")
        
        # Ansible configuration
        rprint(f"\n[bold yellow]📚 Ansible Configuration:[/bold yellow]")
        rprint(f"  • Project root: {settings.ansible.project_root}")
        rprint(f"  • Playbook directories: {', '.join(settings.ansible.playbook_dirs)}")
        rprint(f"  • Role directories: {', '.join(settings.ansible.role_dirs)}")
        rprint(f"  • Inventory directories: {', '.join(settings.ansible.inventory_dirs)}")
        
        if settings.ansible.collections_path:
            rprint(f"  • Collections path: {settings.ansible.collections_path}")
        if settings.ansible.ansible_config:
            rprint(f"  • Ansible config: {settings.ansible.ansible_config}")
    
    except Exception as e:
        rprint(f"[red]❌ Error reading configuration: {e}[/red]")
        raise typer.Exit(1)


@app.command("init")
def init_config(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Configuration file path (defaults to ~/.config/renouveau/config.yaml)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing configuration",
    ),
) -> None:
    """Initialize configuration file interactively."""
    settings = RenouveauSettings()
    
    if config_file is None:
        config_file = settings.get_default_config_file()
    
    if config_file.exists() and not force:
        if not typer.confirm(f"Configuration file already exists at {config_file}. Overwrite?"):
            rprint("[yellow]Configuration initialization cancelled.[/yellow]")
            raise typer.Exit()
    
    rprint(f"[bold blue]⚙️ Initializing Renouveau Configuration[/bold blue]")
    rprint(f"Configuration will be saved to: [cyan]{config_file}[/cyan]\n")
    
    # Tower configuration
    rprint("[bold yellow]🗼 Tower/AWX Configuration:[/bold yellow]")
    tower_url = typer.prompt("Tower/AWX URL (optional)", default="")
    tower_token = typer.prompt("Tower API token (optional)", default="", hide_input=True)
    tower_username = typer.prompt("Tower username (optional)", default="")
    
    if tower_username:
        tower_password = typer.prompt("Tower password", hide_input=True)
    else:
        tower_password = ""
    
    tower_verify_ssl = typer.confirm("Verify SSL certificates?", default=True)
    
    # GitHub configuration
    rprint("\n[bold yellow]🐙 GitHub Configuration:[/bold yellow]")
    github_org = typer.prompt("GitHub organization (optional)", default="")
    github_token = typer.prompt("GitHub token (optional)", default="", hide_input=True)
    
    # Ansible configuration
    rprint("\n[bold yellow]📚 Ansible Configuration:[/bold yellow]")
    project_root = typer.prompt("Ansible project root", default=".")
    
    playbook_dirs = typer.prompt(
        "Playbook directories (comma-separated)", 
        default="playbooks/,site.yml"
    ).split(",")
    
    role_dirs = typer.prompt(
        "Role directories (comma-separated)", 
        default="roles/"
    ).split(",")
    
    inventory_dirs = typer.prompt(
        "Inventory directories (comma-separated)", 
        default="inventory/,inventories/"
    ).split(",")
    
    # Update settings
    if tower_url:
        settings.tower.url = tower_url
    if tower_token:
        settings.tower.token = tower_token
    if tower_username:
        settings.tower.username = tower_username
    if tower_password:
        settings.tower.password = tower_password
    settings.tower.verify_ssl = tower_verify_ssl
    
    if github_org:
        settings.github.organization = github_org
    if github_token:
        settings.github.token = github_token
    
    settings.ansible.project_root = Path(project_root)
    settings.ansible.playbook_dirs = [d.strip() for d in playbook_dirs if d.strip()]
    settings.ansible.role_dirs = [d.strip() for d in role_dirs if d.strip()]
    settings.ansible.inventory_dirs = [d.strip() for d in inventory_dirs if d.strip()]
    
    # Save configuration
    try:
        settings.save_to_file(config_file)
        rprint(f"\n[green]✅ Configuration saved to {config_file}[/green]")
        
        rprint("\n[bold]Next steps:[/bold]")
        if settings.tower.url:
            rprint("  • Run [cyan]renouveau tower job-template list[/cyan] to test Tower connection")
        rprint("  • Run [cyan]renouveau ansible project discover[/cyan] to analyze your Ansible project")
        rprint("  • Run [cyan]renouveau config show[/cyan] to verify your settings")
    
    except Exception as e:
        rprint(f"[red]❌ Error saving configuration: {e}[/red]")
        raise typer.Exit(1)


@app.command("validate")
def validate_config(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Configuration file path",
        exists=True,
    ),
) -> None:
    """Validate configuration settings."""
    try:
        if config_file:
            settings = RenouveauSettings.load_from_file(config_file)
            config_source = str(config_file)
        else:
            settings = RenouveauSettings()
            config_source = "Default/Environment"
        
        rprint(f"[bold blue]🔍 Validating Configuration[/bold blue]")
        rprint(f"[bold]Source:[/bold] {config_source}")
        
        issues = []
        warnings = []
        
        # Validate Tower configuration
        if settings.tower.url:
            if not settings.tower.url.startswith(("http://", "https://")):
                issues.append("Tower URL should start with http:// or https://")
            
            if not (settings.tower.token or (settings.tower.username and settings.tower.password)):
                warnings.append("Tower authentication not configured (token or username/password required)")
        else:
            warnings.append("Tower URL not configured")
        
        # Validate Ansible configuration
        if not settings.ansible.project_root.exists():
            issues.append(f"Ansible project root does not exist: {settings.ansible.project_root}")
        
        # Check if project root has Ansible content
        project_root = settings.ansible.project_root
        has_ansible_content = False
        
        for playbook_dir in settings.ansible.playbook_dirs:
            if (project_root / playbook_dir).exists():
                has_ansible_content = True
                break
        
        for role_dir in settings.ansible.role_dirs:
            if (project_root / role_dir).exists():
                has_ansible_content = True
                break
        
        if not has_ansible_content:
            warnings.append("No Ansible content found in configured directories")
        
        # Validate GitHub configuration
        if settings.github.token and not settings.github.organization:
            warnings.append("GitHub token configured but no organization specified")
        
        # Display results
        if not issues and not warnings:
            rprint("[green]✅ Configuration is valid![/green]")
        else:
            if issues:
                rprint("[red]❌ Configuration issues found:[/red]")
                for issue in issues:
                    rprint(f"  • {issue}")
            
            if warnings:
                rprint("[yellow]⚠️ Configuration warnings:[/yellow]")
                for warning in warnings:
                    rprint(f"  • {warning}")
        
        if issues:
            raise typer.Exit(1)
    
    except Exception as e:
        rprint(f"[red]❌ Error validating configuration: {e}[/red]")
        raise typer.Exit(1)


@app.command("edit")
def edit_config(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Configuration file path",
    ),
) -> None:
    """Open configuration file in default editor."""
    import os
    import subprocess
    
    settings = RenouveauSettings()
    
    if config_file is None:
        config_file = settings.get_default_config_file()
    
    if not config_file.exists():
        if typer.confirm(f"Configuration file does not exist at {config_file}. Create it?"):
            settings.save_to_file(config_file)
        else:
            rprint("[yellow]Edit cancelled.[/yellow]")
            raise typer.Exit()
    
    # Determine editor
    editor = os.environ.get("EDITOR", "nano")
    
    try:
        rprint(f"[blue]Opening {config_file} with {editor}...[/blue]")
        subprocess.run([editor, str(config_file)], check=True)
        rprint("[green]✅ Configuration file editing completed.[/green]")
    
    except subprocess.CalledProcessError:
        rprint(f"[red]❌ Error opening editor '{editor}'[/red]")
        rprint(f"[dim]Try setting the EDITOR environment variable or edit manually: {config_file}[/dim]")
        raise typer.Exit(1)
    except FileNotFoundError:
        rprint(f"[red]❌ Editor '{editor}' not found[/red]")
        rprint(f"[dim]Try setting the EDITOR environment variable or edit manually: {config_file}[/dim]")
        raise typer.Exit(1)

"""Local Ansible content CLI commands."""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from renouveau_core import AnsibleProjectDiscovery, AnsibleConfig

app = typer.Typer(help="📚 Manage local Ansible content")
console = Console()


def get_ansible_discovery() -> AnsibleProjectDiscovery:
    """Get an Ansible project discovery instance."""
    config = AnsibleConfig()
    return AnsibleProjectDiscovery(config)


# Project commands
project_app = typer.Typer(help="📁 Local project operations")


@project_app.command("discover")
def discover_project(
    path: Optional[Path] = typer.Option(None, "--path", "-p", help="Project path (defaults to current directory)"),
) -> None:
    """Discover and analyze Ansible project structure."""
    discovery = get_ansible_discovery()
    
    try:
        with console.status("🔍 Analyzing Ansible project..."):
            project = discovery.discover_project(path)
        
        # Display project overview
        rprint(f"[bold blue]📁 Ansible Project: {project.name}[/bold blue]")
        rprint(f"[bold]Root:[/bold] {project.root_path}")
        rprint(f"[bold]Playbooks:[/bold] {len(project.playbooks)}")
        rprint(f"[bold]Roles:[/bold] {len(project.roles)}")
        rprint(f"[bold]Inventories:[/bold] {len(project.inventories)}")
        
        # Configuration files
        rprint("\n[bold]📋 Configuration Files:[/bold]")
        if project.ansible_cfg:
            rprint(f"  • ansible.cfg: [green]✅ {project.ansible_cfg}[/green]")
        else:
            rprint("  • ansible.cfg: [yellow]❌ Not found[/yellow]")
        
        if project.requirements_yml:
            rprint(f"  • requirements.yml: [green]✅ {project.requirements_yml}[/green]")
        else:
            rprint("  • requirements.yml: [yellow]❌ Not found[/yellow]")
        
        # Variable directories
        if project.group_vars_dir:
            rprint(f"  • group_vars: [green]✅ {project.group_vars_dir}[/green]")
        if project.host_vars_dir:
            rprint(f"  • host_vars: [green]✅ {project.host_vars_dir}[/green]")
        
        # Collections
        if project.collections_path:
            rprint(f"  • collections: [green]✅ {project.collections_path}[/green]")
        
        # Warnings
        if project.warnings:
            rprint("\n[bold yellow]⚠️ Warnings:[/bold yellow]")
            for warning in project.warnings:
                rprint(f"  • {warning}")
        
        # Summary tables
        if project.playbooks:
            rprint("\n[bold]📚 Playbooks:[/bold]")
            table = Table()
            table.add_column("Name", style="bold white")
            table.add_column("Hosts", style="green")
            table.add_column("Tasks", style="blue")
            table.add_column("Roles", style="magenta")
            table.add_column("Valid", style="cyan")
            
            for playbook in project.playbooks:
                valid = "✅" if playbook.is_valid else "❌"
                table.add_row(
                    playbook.name,
                    playbook.hosts or "N/A",
                    str(playbook.tasks_count),
                    str(len(playbook.roles_used)),
                    valid
                )
            console.print(table)
        
        if project.roles:
            rprint("\n[bold]🎭 Roles:[/bold]")
            table = Table()
            table.add_column("Name", style="bold white")
            table.add_column("Description", style="green")
            table.add_column("Tasks", style="blue")
            table.add_column("Handlers", style="magenta")
            table.add_column("Dependencies", style="cyan")
            
            for role in project.roles:
                tasks = "✅" if role.has_tasks else "❌"
                handlers = "✅" if role.has_handlers else "❌"
                deps = str(len(role.dependencies))
                
                table.add_row(
                    role.name,
                    (role.description or "No description")[:40],
                    tasks,
                    handlers,
                    deps
                )
            console.print(table)
    
    except Exception as e:
        rprint(f"[red]❌ Error analyzing project: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(project_app, name="project", help="📁 Local project operations")


# Playbook commands
playbook_app = typer.Typer(help="📖 Playbook operations")


@playbook_app.command("list")
def list_playbooks(
    path: Optional[Path] = typer.Option(None, "--path", "-p", help="Project path (defaults to current directory)"),
) -> None:
    """List playbooks in the project."""
    discovery = get_ansible_discovery()
    
    try:
        with console.status("🔍 Finding playbooks..."):
            project = discovery.discover_project(path)
        
        if not project.playbooks:
            rprint("[yellow]📭 No playbooks found in this project.[/yellow]")
            return
        
        # Create detailed table
        table = Table(title=f"📖 Playbooks ({len(project.playbooks)} found)")
        table.add_column("Name", style="bold white")
        table.add_column("Path", style="blue")
        table.add_column("Hosts", style="green")
        table.add_column("Tasks", style="cyan")
        table.add_column("Roles Used", style="magenta")
        table.add_column("Tags", style="yellow")
        table.add_column("Status", style="red")
        
        for playbook in project.playbooks:
            status = "✅ Valid" if playbook.is_valid else "❌ Invalid"
            roles = ", ".join(playbook.roles_used[:3])
            if len(playbook.roles_used) > 3:
                roles += "..."
            
            tags = ", ".join(playbook.tags[:3])
            if len(playbook.tags) > 3:
                tags += "..."
            
            table.add_row(
                playbook.name,
                str(playbook.path.relative_to(project.root_path)),
                playbook.hosts or "N/A",
                str(playbook.tasks_count),
                roles or "None",
                tags or "None",
                status
            )
        
        console.print(table)
    
    except Exception as e:
        rprint(f"[red]❌ Error listing playbooks: {e}[/red]")
        raise typer.Exit(1)


@playbook_app.command("validate")
def validate_playbook(
    playbook_name: str = typer.Argument(..., help="Playbook name to validate"),
    path: Optional[Path] = typer.Option(None, "--path", "-p", help="Project path (defaults to current directory)"),
) -> None:
    """Validate a specific playbook."""
    discovery = get_ansible_discovery()
    
    try:
        with console.status(f"🔍 Validating playbook '{playbook_name}'..."):
            project = discovery.discover_project(path)
        
        # Find the playbook
        playbook = None
        for pb in project.playbooks:
            if pb.name == playbook_name:
                playbook = pb
                break
        
        if not playbook:
            rprint(f"[red]❌ Playbook '{playbook_name}' not found.[/red]")
            available = [pb.name for pb in project.playbooks]
            if available:
                rprint(f"[dim]Available playbooks: {', '.join(available)}[/dim]")
            raise typer.Exit(1)
        
        # Display validation results
        if playbook.is_valid:
            rprint(f"[green]✅ Playbook '{playbook_name}' is valid![/green]")
        else:
            rprint(f"[red]❌ Playbook '{playbook_name}' has validation errors:[/red]")
            for error in playbook.errors:
                rprint(f"  • {error}")
            raise typer.Exit(1)
        
        # Show playbook details
        rprint(f"\n[bold]📖 Playbook Details:[/bold]")
        rprint(f"  • Path: {playbook.path}")
        rprint(f"  • Target hosts: {playbook.hosts or 'Not specified'}")
        rprint(f"  • Tasks: {playbook.tasks_count}")
        rprint(f"  • Roles used: {len(playbook.roles_used)}")
        rprint(f"  • Available tags: {len(playbook.tags)}")
        
        if playbook.roles_used:
            rprint(f"  • Roles: {', '.join(playbook.roles_used)}")
        
        if playbook.tags:
            rprint(f"  • Tags: {', '.join(playbook.tags)}")
    
    except Exception as e:
        rprint(f"[red]❌ Error validating playbook: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(playbook_app, name="playbook", help="📖 Playbook operations")


# Role commands
role_app = typer.Typer(help="🎭 Role operations")


@role_app.command("list")
def list_roles(
    path: Optional[Path] = typer.Option(None, "--path", "-p", help="Project path (defaults to current directory)"),
) -> None:
    """List roles in the project."""
    discovery = get_ansible_discovery()
    
    try:
        with console.status("🔍 Finding roles..."):
            project = discovery.discover_project(path)
        
        if not project.roles:
            rprint("[yellow]📭 No roles found in this project.[/yellow]")
            return
        
        # Create detailed table
        table = Table(title=f"🎭 Roles ({len(project.roles)} found)")
        table.add_column("Name", style="bold white")
        table.add_column("Description", style="green")
        table.add_column("Author", style="blue")
        table.add_column("Tasks", style="cyan")
        table.add_column("Handlers", style="magenta")
        table.add_column("Variables", style="yellow")
        table.add_column("Dependencies", style="red")
        
        for role in project.roles:
            tasks = "✅" if role.has_tasks else "❌"
            handlers = "✅" if role.has_handlers else "❌"
            variables = "✅" if (role.has_vars or role.has_defaults) else "❌"
            
            table.add_row(
                role.name,
                (role.description or "No description")[:30],
                (role.author or "Unknown")[:20],
                tasks,
                handlers,
                variables,
                str(len(role.dependencies))
            )
        
        console.print(table)
    
    except Exception as e:
        rprint(f"[red]❌ Error listing roles: {e}[/red]")
        raise typer.Exit(1)


@role_app.command("validate")
def validate_role(
    role_name: str = typer.Argument(..., help="Role name to validate"),
    path: Optional[Path] = typer.Option(None, "--path", "-p", help="Project path (defaults to current directory)"),
) -> None:
    """Validate a specific role."""
    discovery = get_ansible_discovery()
    
    try:
        with console.status(f"🔍 Validating role '{role_name}'..."):
            project = discovery.discover_project(path)
        
        # Find the role
        role = None
        for r in project.roles:
            if r.name == role_name:
                role = r
                break
        
        if not role:
            rprint(f"[red]❌ Role '{role_name}' not found.[/red]")
            available = [r.name for r in project.roles]
            if available:
                rprint(f"[dim]Available roles: {', '.join(available)}[/dim]")
            raise typer.Exit(1)
        
        # Display validation results
        if role.is_valid:
            rprint(f"[green]✅ Role '{role_name}' is valid![/green]")
        else:
            rprint(f"[red]❌ Role '{role_name}' has validation errors:[/red]")
            for error in role.errors:
                rprint(f"  • {error}")
            raise typer.Exit(1)
        
        # Show role details
        rprint(f"\n[bold]🎭 Role Details:[/bold]")
        rprint(f"  • Path: {role.path}")
        rprint(f"  • Description: {role.description or 'Not provided'}")
        rprint(f"  • Author: {role.author or 'Unknown'}")
        rprint(f"  • Version: {role.version or 'Not specified'}")
        
        # Structure information
        structure = []
        if role.has_tasks:
            structure.append("tasks")
        if role.has_handlers:
            structure.append("handlers")
        if role.has_vars:
            structure.append("vars")
        if role.has_defaults:
            structure.append("defaults")
        if role.has_templates:
            structure.append("templates")
        if role.has_files:
            structure.append("files")
        if role.has_meta:
            structure.append("meta")
        
        rprint(f"  • Components: {', '.join(structure) if structure else 'None'}")
        
        if role.dependencies:
            rprint(f"  • Dependencies: {', '.join(role.dependencies)}")
        
        if role.galaxy_tags:
            rprint(f"  • Galaxy tags: {', '.join(role.galaxy_tags)}")
    
    except Exception as e:
        rprint(f"[red]❌ Error validating role: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(role_app, name="role", help="🎭 Role operations")

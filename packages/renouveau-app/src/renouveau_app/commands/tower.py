"""Ansible Tower/AWX CLI commands."""

import typer
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from renouveau_core import TowerClient, TowerConfig
from renouveau_core.tower.exceptions import TowerError, TowerConnectionError, TowerAuthenticationError

app = typer.Typer(help="🗼 Manage Ansible Tower/AWX")
console = Console()


def get_tower_client() -> TowerClient:
    """Get a Tower client from global settings."""
    # This would get settings from the main app context
    # For now, we'll create a basic client
    config = TowerConfig()
    
    if not config.url:
        rprint("[red]❌ Tower URL not configured. Run 'renouveau init' first.[/red]")
        raise typer.Exit(1)
    
    try:
        return TowerClient(config)
    except TowerConnectionError as e:
        rprint(f"[red]❌ Cannot connect to Tower: {e}[/red]")
        raise typer.Exit(1)
    except TowerAuthenticationError as e:
        rprint(f"[red]❌ Tower authentication failed: {e}[/red]")
        raise typer.Exit(1)
    except TowerError as e:
        rprint(f"[red]❌ Tower error: {e}[/red]")
        raise typer.Exit(1)


# Job Template commands
job_template_app = typer.Typer(help="📋 Job template operations")


@job_template_app.command("list")
def list_job_templates(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search filter"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by name"),
    project: Optional[int] = typer.Option(None, "--project", "-p", help="Filter by project ID"),
    page_size: Optional[int] = typer.Option(25, "--page-size", help="Results per page"),
) -> None:
    """List job templates."""
    client = get_tower_client()
    
    try:
        with console.status("🔍 Fetching job templates..."):
            response = client.list_job_templates(
                search=search,
                name=name,
                project=project,
                page_size=page_size
            )
        
        templates = response.get("results", [])
        
        if not templates:
            rprint("[yellow]📭 No job templates found.[/yellow]")
            return
        
        # Create table
        table = Table(title=f"📋 Job Templates ({response.get('count', 0)} total)")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="bold white")
        table.add_column("Project", style="green")
        table.add_column("Playbook", style="blue")
        table.add_column("Last Job", style="magenta")
        
        for template in templates:
            last_job = "Never" if not template.get("last_job_run") else template["last_job_run"][:19]
            table.add_row(
                str(template["id"]),
                template["name"],
                f"ID: {template.get('project', 'N/A')}",
                template.get("playbook", "N/A"),
                last_job
            )
        
        console.print(table)
        
        # Show pagination info
        if response.get("next"):
            rprint(f"[dim]📄 Page {(response.get('count', 0) // page_size) + 1} - Use pagination options to see more[/dim]")
    
    except TowerError as e:
        rprint(f"[red]❌ Error listing job templates: {e}[/red]")
        raise typer.Exit(1)


@job_template_app.command("show")
def show_job_template(
    template_id: int = typer.Argument(..., help="Job template ID"),
) -> None:
    """Show detailed information about a job template."""
    client = get_tower_client()
    
    try:
        with console.status(f"🔍 Fetching job template {template_id}..."):
            template = client.get_job_template(template_id)
        
        # Display template details
        rprint(f"[bold blue]📋 Job Template: {template['name']}[/bold blue]")
        rprint(f"[bold]ID:[/bold] {template['id']}")
        rprint(f"[bold]Description:[/bold] {template.get('description', 'No description')}")
        rprint(f"[bold]Project ID:[/bold] {template.get('project', 'N/A')}")
        rprint(f"[bold]Playbook:[/bold] {template.get('playbook', 'N/A')}")
        rprint(f"[bold]Inventory ID:[/bold] {template.get('inventory', 'N/A')}")
        rprint(f"[bold]Job Type:[/bold] {template.get('job_type', 'run')}")
        rprint(f"[bold]Verbosity:[/bold] {template.get('verbosity', 0)}")
        rprint(f"[bold]Forks:[/bold] {template.get('forks', 5)}")
        
        if template.get("job_tags"):
            rprint(f"[bold]Job Tags:[/bold] {template['job_tags']}")
        
        if template.get("skip_tags"):
            rprint(f"[bold]Skip Tags:[/bold] {template['skip_tags']}")
        
        if template.get("extra_vars"):
            rprint(f"[bold]Extra Vars:[/bold] {template['extra_vars']}")
        
        rprint(f"[bold]Last Job Run:[/bold] {template.get('last_job_run', 'Never')}")
        rprint(f"[bold]Status:[/bold] {template.get('status', 'unknown')}")
    
    except TowerError as e:
        rprint(f"[red]❌ Error fetching job template: {e}[/red]")
        raise typer.Exit(1)


@job_template_app.command("launch")
def launch_job_template(
    template_id: int = typer.Argument(..., help="Job template ID"),
    extra_vars: Optional[str] = typer.Option(None, "--extra-vars", "-e", help="Extra variables (JSON)"),
    limit: Optional[str] = typer.Option(None, "--limit", "-l", help="Inventory subset"),
    tags: Optional[str] = typer.Option(None, "--tags", "-t", help="Job tags to run"),
    skip_tags: Optional[str] = typer.Option(None, "--skip-tags", help="Job tags to skip"),
    inventory: Optional[int] = typer.Option(None, "--inventory", "-i", help="Inventory ID"),
    credential: Optional[int] = typer.Option(None, "--credential", help="Credential ID"),
) -> None:
    """Launch a job template."""
    client = get_tower_client()
    
    try:
        # Parse extra vars if provided
        extra_vars_dict = None
        if extra_vars:
            import json
            try:
                extra_vars_dict = json.loads(extra_vars)
            except json.JSONDecodeError:
                rprint("[red]❌ Invalid JSON format for extra variables[/red]")
                raise typer.Exit(1)
        
        with console.status(f"🚀 Launching job template {template_id}..."):
            job = client.launch_job_template(
                template_id=template_id,
                extra_vars=extra_vars_dict,
                limit=limit,
                job_tags=tags,
                skip_tags=skip_tags,
                inventory=inventory,
                credential=credential
            )
        
        rprint(f"[green]✅ Job launched successfully![/green]")
        rprint(f"[bold]Job ID:[/bold] {job['id']}")
        rprint(f"[bold]Job Name:[/bold] {job['name']}")
        rprint(f"[bold]Status:[/bold] {job['status']}")
        rprint(f"[bold]Created:[/bold] {job['created']}")
        
        rprint(f"\n[dim]💡 Use 'renouveau tower job show {job['id']}' to monitor job progress[/dim]")
    
    except TowerError as e:
        rprint(f"[red]❌ Error launching job template: {e}[/red]")
        raise typer.Exit(1)


@job_template_app.command("workflows")
def list_workflows_using_template(
    template_id: int = typer.Argument(..., help="Job template ID"),
) -> None:
    """List workflows that use a specific job template."""
    client = get_tower_client()
    
    try:
        # Get job template details first
        with console.status(f"🔍 Fetching job template {template_id}..."):
            template = client.get_job_template(template_id)
        
        rprint(f"[bold blue]🔍 Finding workflows that use job template: {template['name']} (ID: {template_id})[/bold blue]")
        
        with console.status("🔄 Searching through workflows..."):
            workflows_using_template = client.find_workflows_using_job_template(template_id)
        
        if not workflows_using_template:
            rprint(f"[yellow]📭 No workflows found that use job template '{template['name']}'.[/yellow]")
            return
        
        rprint(f"[green]✅ Found {len(workflows_using_template)} workflow(s) using this job template:[/green]\n")
        
        # Create detailed table
        table = Table(title=f"🔄 Workflows Using Job Template: {template['name']}")
        table.add_column("Workflow ID", style="cyan", no_wrap=True)
        table.add_column("Workflow Name", style="bold white")
        table.add_column("Node ID", style="yellow")
        table.add_column("Node Identifier", style="green")
        table.add_column("Job Type", style="blue")
        table.add_column("Job Tags", style="magenta")
        table.add_column("Organization", style="red")
        
        for workflow_info in workflows_using_template:
            workflow = workflow_info['workflow']
            node = workflow_info['node']
            
            table.add_row(
                str(workflow['id']),
                workflow['name'],
                str(node['id']),
                node.get('identifier', 'N/A'),
                node.get('job_type', 'N/A'),
                node.get('job_tags', 'N/A') or 'All',
                f"ID: {workflow.get('organization', 'N/A')}"
            )
        
        console.print(table)
        
        # Show usage summary
        rprint(f"\n[bold]📊 Usage Summary:[/bold]")
        rprint(f"  • Job template is used in {len(workflows_using_template)} workflow(s)")
        
        # Count unique workflows (in case a job template is used multiple times in the same workflow)
        unique_workflows = set(w['workflow']['id'] for w in workflows_using_template)
        rprint(f"  • Across {len(unique_workflows)} unique workflow template(s)")
        
        # Show next steps
        rprint(f"\n[dim]💡 Use 'renouveau tower workflow show <ID>' to view workflow details[/dim]")
    
    except TowerError as e:
        rprint(f"[red]❌ Error finding workflows: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(job_template_app, name="job-template", help="📋 Job template operations")


# Inventory commands
inventory_app = typer.Typer(help="📦 Inventory operations")


@inventory_app.command("list")
def list_inventories(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search filter"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by name"),
    organization: Optional[int] = typer.Option(None, "--organization", "-o", help="Filter by organization ID"),
    page_size: Optional[int] = typer.Option(25, "--page-size", help="Results per page"),
) -> None:
    """List inventories."""
    client = get_tower_client()
    
    try:
        with console.status("🔍 Fetching inventories..."):
            response = client.list_inventories(
                search=search,
                name=name,
                organization=organization,
                page_size=page_size
            )
        
        inventories = response.get("results", [])
        
        if not inventories:
            rprint("[yellow]📭 No inventories found.[/yellow]")
            return
        
        # Create table
        table = Table(title=f"📦 Inventories ({response.get('count', 0)} total)")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="bold white")
        table.add_column("Hosts", style="green")
        table.add_column("Groups", style="blue")
        table.add_column("Kind", style="magenta")
        
        for inventory in inventories:
            table.add_row(
                str(inventory["id"]),
                inventory["name"],
                str(inventory.get("total_hosts", 0)),
                str(inventory.get("total_groups", 0)),
                inventory.get("kind", "standard") or "standard"
            )
        
        console.print(table)
    
    except TowerError as e:
        rprint(f"[red]❌ Error listing inventories: {e}[/red]")
        raise typer.Exit(1)


@inventory_app.command("hosts")
def list_inventory_hosts(
    inventory_id: int = typer.Argument(..., help="Inventory ID"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search filter"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by name"),
    page_size: Optional[int] = typer.Option(25, "--page-size", help="Results per page"),
) -> None:
    """List hosts in an inventory."""
    client = get_tower_client()
    
    try:
        with console.status(f"🔍 Fetching hosts for inventory {inventory_id}..."):
            response = client.list_inventory_hosts(
                inventory_id=inventory_id,
                search=search,
                name=name,
                page_size=page_size
            )
        
        hosts = response.get("results", [])
        
        if not hosts:
            rprint("[yellow]📭 No hosts found in this inventory.[/yellow]")
            return
        
        # Create table
        table = Table(title=f"🖥️ Hosts in Inventory {inventory_id} ({response.get('count', 0)} total)")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="bold white")
        table.add_column("Description", style="green")
        table.add_column("Enabled", style="blue")
        table.add_column("Last Job", style="magenta")
        
        for host in hosts:
            enabled = "✅" if host.get("enabled", True) else "❌"
            last_job = host.get("last_job") or "Never"
            table.add_row(
                str(host["id"]),
                host["name"],
                host.get("description", "")[:50],
                enabled,
                str(last_job)
            )
        
        console.print(table)
    
    except TowerError as e:
        rprint(f"[red]❌ Error listing inventory hosts: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(inventory_app, name="inventory", help="📦 Inventory operations")


# Project commands  
project_app = typer.Typer(help="📁 Project operations")


@project_app.command("list")
def list_projects(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search filter"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by name"),
    organization: Optional[int] = typer.Option(None, "--organization", "-o", help="Filter by organization ID"),
    scm_type: Optional[str] = typer.Option(None, "--scm-type", help="Filter by SCM type"),
    page_size: Optional[int] = typer.Option(25, "--page-size", help="Results per page"),
) -> None:
    """List projects."""
    client = get_tower_client()
    
    try:
        with console.status("🔍 Fetching projects..."):
            response = client.list_projects(
                search=search,
                name=name,
                organization=organization,
                scm_type=scm_type,
                page_size=page_size
            )
        
        projects = response.get("results", [])
        
        if not projects:
            rprint("[yellow]📭 No projects found.[/yellow]")
            return
        
        # Create table
        table = Table(title=f"📁 Projects ({response.get('count', 0)} total)")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="bold white")
        table.add_column("SCM Type", style="green")
        table.add_column("SCM URL", style="blue")
        table.add_column("Status", style="magenta")
        
        for project in projects:
            scm_url = project.get("scm_url", "")
            if len(scm_url) > 40:
                scm_url = scm_url[:37] + "..."
            
            table.add_row(
                str(project["id"]),
                project["name"],
                project.get("scm_type", "manual") or "manual",
                scm_url,
                project.get("status", "unknown")
            )
        
        console.print(table)
    
    except TowerError as e:
        rprint(f"[red]❌ Error listing projects: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("update")
def update_project(
    project_id: int = typer.Argument(..., help="Project ID"),
) -> None:
    """Update a project from SCM."""
    client = get_tower_client()
    
    try:
        with console.status(f"🔄 Updating project {project_id}..."):
            update = client.update_project(project_id)
        
        rprint(f"[green]✅ Project update initiated![/green]")
        rprint(f"[bold]Update ID:[/bold] {update['id']}")
        rprint(f"[bold]Status:[/bold] {update['status']}")
        rprint(f"[bold]Created:[/bold] {update['created']}")
        
        rprint(f"\n[dim]💡 Monitor update progress with job monitoring commands[/dim]")
    
    except TowerError as e:
        rprint(f"[red]❌ Error updating project: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(project_app, name="project", help="📁 Project operations")


# Workflow commands
workflow_app = typer.Typer(help="🔄 Workflow operations")


@workflow_app.command("list")
def list_workflows(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search filter"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Filter by name"),
    organization: Optional[int] = typer.Option(None, "--organization", "-o", help="Filter by organization ID"),
    page_size: Optional[int] = typer.Option(25, "--page-size", help="Results per page"),
) -> None:
    """List workflow job templates."""
    client = get_tower_client()
    
    try:
        with console.status("🔍 Fetching workflow job templates..."):
            response = client.list_workflow_job_templates(
                search=search,
                name=name,
                organization=organization,
                page_size=page_size
            )
        
        workflows = response.get("results", [])
        
        if not workflows:
            rprint("[yellow]📭 No workflow job templates found.[/yellow]")
            return
        
        # Create table
        table = Table(title=f"🔄 Workflow Job Templates ({response.get('count', 0)} total)")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="bold white")
        table.add_column("Organization", style="green")
        table.add_column("Last Job", style="magenta")
        table.add_column("Status", style="blue")
        
        for workflow in workflows:
            last_job = "Never" if not workflow.get("last_job_run") else workflow["last_job_run"][:19]
            table.add_row(
                str(workflow["id"]),
                workflow["name"],
                f"ID: {workflow.get('organization', 'N/A')}",
                last_job,
                workflow.get('status', 'unknown')
            )
        
        console.print(table)
        
        # Show pagination info
        if response.get("next"):
            rprint(f"[dim]📄 Page {(response.get('count', 0) // page_size) + 1} - Use pagination options to see more[/dim]")
    
    except TowerError as e:
        rprint(f"[red]❌ Error listing workflow job templates: {e}[/red]")
        raise typer.Exit(1)


@workflow_app.command("show")
def show_workflow(
    workflow_id: int = typer.Argument(..., help="Workflow job template ID"),
) -> None:
    """Show detailed information about a workflow job template."""
    client = get_tower_client()
    
    try:
        with console.status(f"🔍 Fetching workflow job template {workflow_id}..."):
            workflow = client.get_workflow_job_template(workflow_id)
        
        # Display workflow details
        rprint(f"[bold blue]🔄 Workflow Job Template: {workflow['name']}[/bold blue]")
        rprint(f"[bold]ID:[/bold] {workflow['id']}")
        rprint(f"[bold]Description:[/bold] {workflow.get('description', 'No description')}")
        rprint(f"[bold]Organization ID:[/bold] {workflow.get('organization', 'N/A')}")
        rprint(f"[bold]Survey Enabled:[/bold] {workflow.get('survey_enabled', False)}")
        rprint(f"[bold]Allow Simultaneous:[/bold] {workflow.get('allow_simultaneous', False)}")
        
        if workflow.get("extra_vars"):
            rprint(f"[bold]Extra Vars:[/bold] {workflow['extra_vars']}")
        
        rprint(f"[bold]Last Job Run:[/bold] {workflow.get('last_job_run', 'Never')}")
        rprint(f"[bold]Status:[/bold] {workflow.get('status', 'unknown')}")
        
        # Show workflow nodes
        rprint(f"\n[bold]🔗 Workflow Nodes:[/bold]")
        with console.status("🔍 Fetching workflow nodes..."):
            nodes_response = client.list_workflow_nodes(workflow_id, page_size=100)
        
        nodes = nodes_response.get("results", [])
        if nodes:
            table = Table()
            table.add_column("Node ID", style="cyan")
            table.add_column("Job Template ID", style="yellow")
            table.add_column("Identifier", style="green")
            table.add_column("Job Type", style="blue")
            table.add_column("Success Nodes", style="magenta")
            
            for node in nodes:
                success_nodes = ", ".join(map(str, node.get('success_nodes', [])))
                table.add_row(
                    str(node['id']),
                    str(node.get('unified_job_template', 'N/A')),
                    node.get('identifier', 'N/A'),
                    node.get('job_type', 'N/A'),
                    success_nodes or "None"
                )
            
            console.print(table)
        else:
            rprint("[yellow]No workflow nodes found.[/yellow]")
    
    except TowerError as e:
        rprint(f"[red]❌ Error fetching workflow job template: {e}[/red]")
        raise typer.Exit(1)


@workflow_app.command("nodes")
def list_workflow_nodes(
    workflow_id: int = typer.Argument(..., help="Workflow job template ID"),
    page_size: Optional[int] = typer.Option(50, "--page-size", help="Results per page"),
) -> None:
    """List nodes in a workflow job template."""
    client = get_tower_client()
    
    try:
        with console.status(f"🔍 Fetching workflow nodes for {workflow_id}..."):
            response = client.list_workflow_nodes(workflow_id, page_size=page_size)
        
        nodes = response.get("results", [])
        
        if not nodes:
            rprint("[yellow]📭 No workflow nodes found.[/yellow]")
            return
        
        # Create detailed table
        table = Table(title=f"🔗 Workflow Nodes ({response.get('count', 0)} total)")
        table.add_column("Node ID", style="cyan", no_wrap=True)
        table.add_column("Job Template", style="bold white")
        table.add_column("Identifier", style="green")
        table.add_column("Job Type", style="blue")
        table.add_column("Tags", style="yellow")
        table.add_column("Success Nodes", style="magenta")
        table.add_column("Failure Nodes", style="red")
        
        for node in nodes:
            success_nodes = ", ".join(map(str, node.get('success_nodes', [])))
            failure_nodes = ", ".join(map(str, node.get('failure_nodes', [])))
            
            table.add_row(
                str(node['id']),
                str(node.get('unified_job_template', 'N/A')),
                node.get('identifier', 'N/A'),
                node.get('job_type', 'N/A'),
                node.get('job_tags', 'N/A'),
                success_nodes or "None",
                failure_nodes or "None"
            )
        
        console.print(table)
    
    except TowerError as e:
        rprint(f"[red]❌ Error listing workflow nodes: {e}[/red]")
        raise typer.Exit(1)


app.add_typer(workflow_app, name="workflow", help="🔄 Workflow operations")

import typer

job_templates_app = typer.Typer(help="Manage job templates")
workflow_job_templates_app = typer.Typer(help="Manage workflow job templates")


@job_templates_app.command("create")
def create_job_template():
    typer.echo("create job template (stub)")


@workflow_job_templates_app.command("create")
def create_workflow_job_template():
    typer.echo("create workflow job template (stub)")

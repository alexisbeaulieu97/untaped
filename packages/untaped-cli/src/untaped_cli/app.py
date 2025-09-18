import typer

from .commands import job_templates_app, workflow_job_templates_app


app = typer.Typer(help="untaped CLI")


@app.callback()
def main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose output"
    )
):
    return


@app.command()
def version():
    """Show version information."""
    import importlib.metadata

    pkg = "untaped-cli"
    try:
        v = importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        v = "0.0.0"
    typer.echo(v)


app.add_typer(job_templates_app, name="job-templates")
app.add_typer(workflow_job_templates_app, name="workflow-job-templates")



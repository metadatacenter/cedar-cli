import typer

from org.metadatacenter import start_frontend

app = typer.Typer(no_args_is_help=True)
app.add_typer(start_frontend.app, name="frontend")

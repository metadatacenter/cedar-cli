import typer

from org.metadatacenter import clean_maven

app = typer.Typer(no_args_is_help=True)
app.add_typer(clean_maven.app, name="maven")

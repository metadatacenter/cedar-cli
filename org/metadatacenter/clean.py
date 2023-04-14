import typer

from org.metadatacenter import clean_maven

app = typer.Typer()
app.add_typer(clean_maven.app, name="maven")

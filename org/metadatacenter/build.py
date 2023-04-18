import typer

from org.metadatacenter.BuildWorker import BuildWorker
from org.metadatacenter.Repos import Repos

app = typer.Typer()

repos = Repos()
build_worker = BuildWorker(repos)


@app.command("parent")
def parent():
    build_worker.parent()


@app.command("libraries")
def libraries():
    build_worker.libraries()


@app.command("project")
def project():
    build_worker.project()


@app.command("clients")
def clients():
    build_worker.clients()


@app.command("all")
def all():
    build_worker.parent()
    build_worker.libraries()
    build_worker.project()
    build_worker.clients()


@app.command("frontends")
def frontends():
    build_worker.frontends()

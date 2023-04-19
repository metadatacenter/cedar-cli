import typer

from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.worker.BuildWorker import BuildWorker

app = typer.Typer()

repos = Repos()
build_worker = BuildWorker(repos)


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory")):
    build_worker.this(wd)


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


@app.command("java")
def java():
    build_worker.parent()
    build_worker.libraries()
    build_worker.project()
    build_worker.clients()


@app.command("frontends")
def frontends():
    build_worker.frontends()


@app.command("all")
def all():
    build_worker.parent()
    build_worker.libraries()
    build_worker.project()
    build_worker.clients()
    build_worker.frontends()

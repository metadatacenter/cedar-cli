import typer

from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.worker.DeployWorker import DeployWorker

app = typer.Typer()

repos = Repos()
deploy_worker = DeployWorker(repos)


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory")):
    deploy_worker.this(wd)


@app.command("parent")
def parent():
    deploy_worker.parent()


@app.command("libraries")
def libraries():
    deploy_worker.libraries()


@app.command("project")
def project():
    deploy_worker.project()


@app.command("clients")
def clients():
    deploy_worker.clients()


@app.command("java")
def java():
    deploy_worker.parent()
    deploy_worker.libraries()
    deploy_worker.project()
    deploy_worker.clients()


@app.command("frontends")
def frontends():
    deploy_worker.frontends()


@app.command("all")
def all():
    deploy_worker.parent()
    deploy_worker.libraries()
    deploy_worker.project()
    deploy_worker.clients()
    deploy_worker.frontends()

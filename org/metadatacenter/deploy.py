import typer

from org.metadatacenter.assembler.DeployAssembler import DeployAssembler

app = typer.Typer()

deploy_assembler = DeployAssembler()


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory")):
    deploy_assembler.this(wd)
    deploy_assembler.execute_task_list()


@app.command("parent")
def parent():
    deploy_assembler.parent()
    deploy_assembler.execute_task_list()


@app.command("libraries")
def libraries():
    deploy_assembler.libraries()
    deploy_assembler.execute_task_list()


@app.command("project")
def project():
    deploy_assembler.project()
    deploy_assembler.execute_task_list()


@app.command("clients")
def clients():
    deploy_assembler.clients()
    deploy_assembler.execute_task_list()


@app.command("java")
def java():
    deploy_assembler.parent()
    deploy_assembler.libraries()
    deploy_assembler.project()
    deploy_assembler.clients()
    deploy_assembler.execute_task_list()


@app.command("frontends")
def frontends():
    deploy_assembler.frontends()
    deploy_assembler.execute_task_list()


@app.command("all")
def all():
    deploy_assembler.parent()
    deploy_assembler.libraries()
    deploy_assembler.project()
    deploy_assembler.clients()
    deploy_assembler.frontends()
    deploy_assembler.execute_task_list()

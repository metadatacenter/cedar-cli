import typer

from org.metadatacenter.assembler.BuildAssembler import BuildAssembler

app = typer.Typer()

build_assembler = BuildAssembler()


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory")):
    build_assembler.this(wd)
    build_assembler.execute_task_list()


@app.command("parent")
def parent():
    build_assembler.parent()
    build_assembler.execute_task_list()


@app.command("libraries")
def libraries():
    build_assembler.libraries()
    build_assembler.execute_task_list()


@app.command("project")
def project():
    build_assembler.project()
    build_assembler.execute_task_list()


@app.command("clients")
def clients():
    build_assembler.clients()
    build_assembler.execute_task_list()


@app.command("java")
def java():
    build_assembler.parent()
    build_assembler.libraries()
    build_assembler.project()
    build_assembler.clients()
    build_assembler.execute_task_list()


@app.command("frontends")
def frontends():
    build_assembler.frontends()
    build_assembler.execute_task_list()


@app.command("all")
def all():
    build_assembler.parent()
    build_assembler.libraries()
    build_assembler.project()
    build_assembler.clients()
    build_assembler.frontends()
    build_assembler.execute_task_list()

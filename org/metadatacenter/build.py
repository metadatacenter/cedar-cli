import typer

app = typer.Typer()


@app.command("parent")
def parent():
    print("Not implemented yet!")


@app.command("libraries")
def libraries():
    print("Not implemented yet!")


@app.command("project")
def project():
    print("Not implemented yet!")


@app.command("clients")
def clients():
    print("Not implemented yet!")


@app.command("frontends")
def frontends():
    print("Not implemented yet!")


@app.command("all")
def all():
    print("Not implemented yet!")

import typer

app = typer.Typer()


@app.command("parent")
def parent():
    print("Not implemented yet! 41")


@app.command("libraries")
def libraries():
    print("Not implemented yet! 42")


@app.command("project")
def project():
    print("Not implemented yet! 43")


@app.command("clients")
def clients():
    print("Not implemented yet! 44")


@app.command("frontends")
def frontends():
    print("Not implemented yet! 45")


@app.command("all")
def all():
    print("Not implemented yet! 46")

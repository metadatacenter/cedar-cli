import typer

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def all():
    print("Not implemented yet! 31")


@app.command("cedar")
def cedar():
    print("Not implemented yet! 32")

import typer

from org.metadatacenter import git, server, build, deploy, clean

app = typer.Typer()
app.add_typer(git.app, name="git")
app.add_typer(server.app, name="server")
app.add_typer(build.app, name="build")
app.add_typer(deploy.app, name="deploy")
app.add_typer(clean.app, name="clean")

if __name__ == "__main__":
    app()

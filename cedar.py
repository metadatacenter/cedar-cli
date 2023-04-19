import typer

from org.metadatacenter import git, server, build, deploy, clean, repo, env

app = typer.Typer()
app.add_typer(repo.app, name="repo")
app.add_typer(git.app, name="git")
app.add_typer(server.app, name="server")
app.add_typer(build.app, name="build")
app.add_typer(deploy.app, name="deploy")
app.add_typer(clean.app, name="clean")
app.add_typer(env.app, name="env")

if __name__ == "__main__":
    app()

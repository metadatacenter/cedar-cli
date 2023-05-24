import typer

from org.metadatacenter import git, server, build, deploy, clean, repo, env, release, start, version, docker
from org.metadatacenter.util.GlobalContext import GlobalContext

GlobalContext()

app = typer.Typer(no_args_is_help=True)
app.add_typer(repo.app, name="repo")
app.add_typer(git.app, name="git")
app.add_typer(server.app, name="server")
app.add_typer(build.app, name="build")
app.add_typer(deploy.app, name="deploy")
app.add_typer(clean.app, name="clean")
app.add_typer(env.app, name="env")
app.add_typer(release.app, name="release")
app.add_typer(start.app, name="start")
app.add_typer(version.app, name="version")
app.add_typer(docker.app, name="docker")

if __name__ == "__main__":
    app()

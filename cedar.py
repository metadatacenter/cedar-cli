import typer

from org.metadatacenter import git, server, build, deploy, clean, repo, env, release, start, stop, check, docker, dev, cert, prod
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.CheatWorker import CheatWorker
from org.metadatacenter.worker.ServerWorker import ServerWorker

GlobalContext()

app = typer.Typer(no_args_is_help=True)
app.add_typer(repo.app, name="repo", help="Configured repo info...")
app.add_typer(git.app, name="git", help="Git operations on all repos...")
app.add_typer(server.app, name="server", help="Server status...")
app.add_typer(build.app, name="build", help="Build various components...")
app.add_typer(deploy.app, name="deploy", help="Deploy various components...")
app.add_typer(clean.app, name="clean", help="Clean various locations...")
app.add_typer(env.app, name="env", help="List environment variables...")
app.add_typer(release.app, name="release", help="Create a CEDAR release...")
app.add_typer(start.app, name="start", help="Start various components...")
app.add_typer(stop.app, name="stop", help="Stop various components...")
app.add_typer(check.app, name="check", help="Check various artifacts...")
app.add_typer(docker.app, name="docker", help="Docker related operations...")
app.add_typer(dev.app, name="dev", help="Development related operations...")
app.add_typer(prod.app, name="prod", help="Production server related operations...")
app.add_typer(cert.app, name="cert", help="Self-signed certificates...")


@app.command("cheat", help="Open cheatsheet")
def cheat():
    CheatWorker.cheat()


@app.command("status", help="Shortcut for 'server status'")
def status():
    ServerWorker.status()


# @app.command("test")
# def test():
#     Worker.execute_generic_shell_commands([
#         'echo "$SHELL"'
#     ],
#         title="Test",
#     )


if __name__ == "__main__":
    app()

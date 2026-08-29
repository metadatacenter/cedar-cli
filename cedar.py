import sys

import typer

from org.metadatacenter.util.ModeManager import ModeError, ModeManager

try:
    ModeManager.bootstrap(sys.argv[1:])
except ModeError as error:
    typer.echo(str(error), err=True)
    raise SystemExit(1)

from org.metadatacenter import git, build, publish, repo, env, release, release_train, check, docker, dev, cert, prod, native, mode
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.CheatWorker import CheatWorker

GlobalContext()

app = typer.Typer(no_args_is_help=True)
app.add_typer(repo.app, name="repo", help="Configured repo info...")
app.add_typer(git.app, name="git", help="Git operations on all repos...")
app.add_typer(build.app, name="build", help="Build various components...")
app.add_typer(publish.app, name="publish", help="Publish build artifacts...")
app.add_typer(env.app, name="env", help="Inspect the effective CEDAR environment safely...")
app.add_typer(release.app, name="release", help="Create a CEDAR release...")
release.app.add_typer(release_train.app)
app.add_typer(check.app, name="check", help="Check repository and version consistency...")
app.add_typer(docker.app, name="docker", help="Docker related operations...")
app.add_typer(native.app, name="native", help="Inspect and manage headless native applications...")
app.add_typer(dev.app, name="dev", help="Development related operations...")
app.add_typer(prod.app, name="prod", help="Production server related operations...")
app.add_typer(cert.app, name="cert", help="Self-signed certificates...")
app.command("mode")(mode.mode)


@app.command("cheat", help="Open cheatsheet")
def cheat():
    CheatWorker.cheat()


# @app.command("test")
# def test():
#     Worker.execute_generic_shell_commands([
#         'echo "$SHELL"'
#     ],
#         title="Test",
#     )


if __name__ == "__main__":
    app()

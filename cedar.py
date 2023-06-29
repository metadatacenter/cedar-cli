import typer

from org.metadatacenter import git, server, build, deploy, clean, repo, env, release, start, check, docker, dev, cert
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.CheatWorker import CheatWorker

GlobalContext()

app = typer.Typer(no_args_is_help=True)
app.add_typer(repo.app, name="repo", help="Git repos vs working directories")
app.add_typer(git.app, name="git")
app.add_typer(server.app, name="server")
app.add_typer(build.app, name="build")
app.add_typer(deploy.app, name="deploy")
app.add_typer(clean.app, name="clean")
app.add_typer(env.app, name="env")
app.add_typer(release.app, name="release")
app.add_typer(start.app, name="start")
app.add_typer(check.app, name="check")
app.add_typer(docker.app, name="docker")
app.add_typer(dev.app, name="dev")
app.add_typer(cert.app, name="cert")


@app.command("cheat")
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

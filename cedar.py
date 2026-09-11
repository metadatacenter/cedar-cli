import typer
from typer.core import TyperGroup
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context

from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.util.OutputBuffering import line_buffer_when_redirected

from org.metadatacenter import (
    build, cert, check, dev, docker, env, git, mode, native, prod, publish,
    release_train, repo, test_processes,
)
from org.metadatacenter.worker.CheatWorker import CheatWorker
from org.metadatacenter.util.CliResult import exit_on_failure

class InvocationGroup(TyperGroup):
    def parse_args(self, ctx, args):
        ctx.meta['cedar_arguments'] = list(args)
        return super().parse_args(ctx, args)

    def invoke(self, ctx):
        context = ctx.obj if isinstance(ctx.obj, InvocationContext) else InvocationContext()
        with use_context(context):
            try:
                ModeManager.bootstrap(ctx.meta['cedar_arguments'])
            except ModeError as error:
                typer.echo(str(error), err=True)
                raise typer.Exit(1) from error
            ctx.obj = context
            return super().invoke(ctx)


def create_app():
    """Register commands without inspecting the host or changing its environment."""
    app = typer.Typer(no_args_is_help=True, cls=InvocationGroup)
    app.add_typer(repo.app, name="repo", help="Configured repo info...")
    app.add_typer(git.app, name="git", help="Git operations on all repos...")
    app.add_typer(build.app, name="build", help="Build various components...")
    app.add_typer(publish.app, name="publish", help="Publish build artifacts...")
    app.add_typer(env.app, name="env", help="Inspect the effective CEDAR environment safely...")
    app.add_typer(release_train.app, name="release", help="Create a CEDAR release...")
    app.add_typer(check.app, name="check", help="Check repository and version consistency...")
    app.add_typer(docker.app, name="docker", help="Docker related operations...")
    app.add_typer(native.app, name="native", help="Inspect and manage headless native applications...")
    app.add_typer(dev.app, name="dev", help="Development related operations...")
    app.add_typer(prod.app, name="prod", help="Production server related operations...")
    app.add_typer(cert.app, name="cert", help="Self-signed certificates...")
    app.add_typer(test_processes.app, name="test", help="Inspect and clean test-owned processes...")
    app.command("mode")(mode.mode)


    @app.command("cheat", help="Open cheatsheet")
    def cheat():
        exit_on_failure(CheatWorker.cheat())

    return app


app = create_app()


def main(args=None):
    line_buffer_when_redirected()
    app(args=args)


if __name__ == "__main__":
    main()

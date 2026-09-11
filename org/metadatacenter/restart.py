"""Native application restart targets, with the original flat syntax retained."""
from typing import List

import typer
from typer.core import TyperGroup

from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.worker.NativeWorker import NativeWorker


class RestartGroup(TyperGroup):
    def resolve_command(self, ctx, args):
        # Route the whole legacy list together so a later invalid name cannot
        # cause a partially executed restart. Grouped targets use normal parsing.
        if args and args[0] in (*NativeWorker.MICROSERVICES, *NativeWorker.FRONTENDS):
            return 'legacy', self.get_command(ctx, 'legacy'), args
        return super().resolve_command(ctx, args)


app = typer.Typer(cls=RestartGroup, invoke_without_command=True, no_args_is_help=False)
microservices = typer.Typer(no_args_is_help=True)
frontends = typer.Typer(no_args_is_help=True)
app.add_typer(microservices, name='microservice', help='Restart one Java microservice.')
app.add_typer(frontends, name='frontend', help='Restart one frontend development server.')


def _restart(services):
    known = set(NativeWorker.MICROSERVICES) | set(NativeWorker.FRONTENDS)
    unknown = [name for name in services if name not in known]
    if unknown:
        typer.echo(f'Unknown native applications: {", ".join(unknown)}', err=True)
        raise typer.Exit(1)
    try:
        mode = ModeManager.require_surface('native')
        if mode is CedarMode.HYBRID:
            ModeManager.require_native_frontend_services(services, 'restart')
    except ModeError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    exit_on_failure(NativeWorker.restart(services))


@app.callback()
def restart_default(ctx: typer.Context):
    """Restart applications; infrastructure is left running. No target means all applications."""
    if ctx.invoked_subcommand is None:
        _restart(())


@app.command('legacy', hidden=True)
def legacy(services: List[str] = typer.Argument(...)):
    _restart(services)


@app.command('all')
def all_applications():
    """Restart all managed applications, keeping infrastructure running."""
    _restart(())


@app.command('microservices')
@microservices.command('all')
def all_microservices():
    _restart(NativeWorker.MICROSERVICES)


@app.command('frontends')
@frontends.command('all')
def all_frontends():
    _restart(NativeWorker.FRONTENDS)


@frontends.command('split-frontends')
def split_frontends():
    _restart(('ui-workspace', 'ui-designer'))


def _single_service(name):
    def command():
        _restart((name,))
    return command


for _name in NativeWorker.MICROSERVICES:
    microservices.command(_name)(_single_service(_name))
microservices.command('open', hidden=True)(_single_service('openview'))
for _name in NativeWorker.FRONTENDS:
    frontends.command(_name.removeprefix('ui-'))(_single_service(_name))

import typer
from typing import List
from rich.console import Console

from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.CertificateWorker import CertificateError, CertificateWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


def run_certificate_action(action):
    try:
        exit_on_failure(action())
    except (CertificateError, KeyError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


@app.command("setup", help="Set up working directory and config files for CA")
def setup():
    """Create missing CA directories and configuration without resetting existing state."""
    run_certificate_action(CertificateWorker.setup)


@app.command("ca", help="Generate self-signed CA cert")
def ca(force: bool = typer.Option(False, "--force", help="Replace an existing CA key and certificate.")):
    """Generate the local certificate authority."""
    run_certificate_action(lambda: CertificateWorker.generate_ca(force=force))


@app.command("domains", help="Generate self-signed certificates for all or selected subdomains")
def domains(
        names: List[str] = typer.Argument(None, help="Subdomain names; omit to generate all"),
        force: bool = typer.Option(False, "--force", help="Renew and replace existing leaf files.")):
    """Generate all leaf certificates, or only the named leaves."""
    run_certificate_action(lambda: CertificateWorker.generate_domains(names, force=force))

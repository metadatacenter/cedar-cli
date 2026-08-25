import typer
from typing import List

from org.metadatacenter.worker.CertificateWorker import CertificateWorker

app = typer.Typer(no_args_is_help=True)


@app.command("setup", help="Set up working directory and config files for CA")
def setup():
    CertificateWorker.setup()


@app.command("ca", help="Generate self-signed CA cert")
def ca():
    CertificateWorker.generate_ca()


@app.command("domains", help="Generate self-signed certificates for all or selected subdomains")
def domains(names: List[str] = typer.Argument(None, help="Subdomain names; omit to generate all")):
    CertificateWorker.generate_domains(names)

import typer

from org.metadatacenter import clean_maven
from org.metadatacenter.worker.CertificateWorker import CertificateWorker

app = typer.Typer(no_args_is_help=True)


@app.command("setup", help="Set up working directory and config files for CA")
def setup():
    CertificateWorker.setup()


@app.command("ca", help="Generate self-signed CA cert")
def ca():
    CertificateWorker.generate_ca()


@app.command("all", help="Generate self-signed certificates for all subdomains")
def all():
    CertificateWorker.generate_all()


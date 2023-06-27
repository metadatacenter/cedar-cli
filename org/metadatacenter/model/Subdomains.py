from rich.console import Console

from org.metadatacenter.model.DomainMode import DomainMode
from org.metadatacenter.model.Subdomain import Subdomain

console = Console()


class Subdomains:
    def __init__(self):
        self.map = {}

    def add_microservice(self, name: str):
        server = Subdomain(name, DomainMode.UPSTREAM)
        self.map[name] = server

    def add_frontend(self, name: str):
        server = Subdomain(name, DomainMode.UPSTREAM)
        self.map[name] = server

    def add_redirect(self, name: str):
        server = Subdomain(name, DomainMode.UPSTREAM)
        self.map[name] = server

    def add_backend(self, name: str, port: int):
        server = Subdomain(name, DomainMode.UPSTREAM)
        self.map[name] = server

    def add_static(self, name: str, static_location=None):
        server = Subdomain(name, DomainMode.UPSTREAM)
        self.map[name] = server

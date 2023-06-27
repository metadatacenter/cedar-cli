import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class CertificateWorker(Worker):

    # TODO:Check env variables
    # At the beginning of every call: ca, all check working dir and env vars

    @classmethod
    def set_paths(cls):
        cls.cedar_ca_home = os.environ[Const.CEDAR_CA_HOME]

    @classmethod
    def generate_domain_configs(cls):
        source_file = Util.read_file(Util.get_asset_file_path(['certs', 'openssl-domain.cnf']))
        command_all = ''
        for subdomain in GlobalContext.subdomains.map.values():
            subdomain_name = subdomain.name
            subdomain_file_name = subdomain.get_config_file_name()
            subdomain_directory = subdomain.get_cert_directory_name()
            target_path = os.path.join(cls.cedar_ca_home, 'configs', subdomain_file_name)
            target_content = source_file.replace('<CEDAR.COMMON_NAME>', subdomain_name)
            Util.write_file(target_path, target_content)

            command = """
mkdir -p certs/{subdomain_directory}
"""
            command = command.format(subdomain_directory=subdomain_directory)
            command_all += command.strip() + "\n"
        Worker.execute_generic_shell_commands([
            command_all
        ],
            cwd=cls.cedar_ca_home,
            title="Creating domain cert subdirectory",
        )

    @classmethod
    def setup(cls):
        if Const.CEDAR_CA_HOME in os.environ:
            cls.set_paths()
            ca_config = Util.get_asset_file_path(['certs', 'openssl--ca.cnf'])
            command = """
mkdir -p {cedar_ca_home}
"""
            command = command.format(cedar_ca_home=cls.cedar_ca_home)
            Worker.execute_generic_shell_commands([
                command
            ],
                title="Making sure CA working directory exists",
            )

            command = """
mkdir -p configs
mkdir -p certs

cp {ca_config} configs/.

echo 00 > serial
touch index.txt
touch index.txt.attr
"""
            command = command.format(ca_config=ca_config)
            Worker.execute_generic_shell_commands([
                command
            ],
                cwd=cls.cedar_ca_home,
                title="Creating CA working directories and files",
            )

            cls.generate_domain_configs()
        else:
            err = 'CEDAR_CA_HOME environment variable is not set. In order to proceed, please set it to an existing folder'
            console.print(Panel(err, title="[bold red]Error", subtitle="[bold red]cedarcli", style=Style(color="yellow")))
            sys.exit(1)

    @classmethod
    def generate_ca(cls):
        cls.set_paths()
        Worker.execute_generic_shell_commands([
            '''
openssl genrsa -des3 -passout pass:${CEDAR_CA_PASSWORD} -out ca.key 4096
openssl req -new -x509 -days 3650 -passin pass:${CEDAR_CA_PASSWORD} -key ca.key -out ca.crt -config ./configs/openssl--ca.cnf
'''
        ],
            cwd=cls.cedar_ca_home,
            title="Generating self-signed CA certificate",
        )

    @classmethod
    def generate_all(cls):
        cls.set_paths()
        for subdomain in GlobalContext.subdomains.map.values():
            subdomain_name = subdomain.get_fqdn()
            config_file_name = subdomain.get_config_file_name()
            subdomain_directory = subdomain.get_cert_directory_name()
            command = '''
openssl genrsa -out {subdomain_name}.key 2048
openssl req -new -sha256 -key {subdomain_name}.key -out {subdomain_name}.csr -config ../../configs/{config_file_name}
openssl ca -batch -cert ../../ca.crt -keyfile ../../ca.key -in {subdomain_name}.csr -out {subdomain_name}.crt \
    -passin pass:$CEDAR_CA_PASSWORD \
    -outdir ./ -config ../../configs/{config_file_name} -verbose -extensions v3_req'''

            command = command.format(subdomain_name=subdomain_name, config_file_name=config_file_name)
            Worker.execute_generic_shell_commands([
                command
            ],
                cwd=cls.cedar_ca_home + '/certs/' + subdomain_directory,
                title="Generating certificate for subdomain: " + subdomain_name,
            )

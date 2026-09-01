import os
import shlex
import shutil
from pathlib import Path

from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker


class CertificateError(ValueError):
    pass


class CertificateWorker(Worker):

    REQUIRED_CA_ENVIRONMENT = (
        'CEDAR_CA_PASSWORD', 'CEDAR_CA_COUNTRY', 'CEDAR_CA_STATE', 'CEDAR_CA_LOC',
        'CEDAR_CA_ORG', 'CEDAR_CA_ORG_UNIT', 'CEDAR_CA_COMMON_NAME', 'CEDAR_CA_EMAIL',
    )

    @classmethod
    def set_paths(cls):
        cedar_ca_home = os.environ.get(Const.CEDAR_CA_HOME)
        if not cedar_ca_home:
            raise CertificateError(
                "CEDAR_CA_HOME is not set. Load a CEDAR profile before managing certificates."
            )
        cls.cedar_ca_home = cedar_ca_home
        return Path(cedar_ca_home)

    @classmethod
    def require_ca_environment(cls):
        missing = [name for name in cls.REQUIRED_CA_ENVIRONMENT if not os.environ.get(name)]
        if missing:
            raise CertificateError(
                f"Missing certificate environment variables: {', '.join(missing)}. "
                "Load a CEDAR profile before generating certificates."
            )

    @classmethod
    def select_subdomains(cls, names=None):
        if not names:
            return list(GlobalContext.subdomains.map.values())

        selected = []
        unknown = []
        for name in dict.fromkeys(names):
            subdomain = GlobalContext.subdomains.map.get(name)
            if subdomain is None:
                unknown.append(name)
            else:
                selected.append(subdomain)
        if unknown:
            available = ", ".join(
                "<root>" if name == "" else name
                for name in sorted(GlobalContext.subdomains.map)
            )
            raise ValueError(
                f"Unknown certificate subdomain(s): {', '.join(unknown)}. Available: {available}"
            )
        return selected

    @classmethod
    def generate_domain_configs(cls, names=None):
        ca_home = cls.set_paths()
        source_file = Util.read_file(Util.get_asset_file_path(['certs', 'openssl-domain.cnf']))
        if source_file is None:
            raise CertificateError("The domain certificate configuration template is missing.")
        selected = cls.select_subdomains(names)
        (ca_home / 'configs').mkdir(parents=True, exist_ok=True)
        (ca_home / 'certs').mkdir(parents=True, exist_ok=True)
        for subdomain in selected:
            subdomain_name = subdomain.name
            subdomain_file_name = subdomain.get_config_file_name()
            subdomain_directory = subdomain.get_cert_directory_name()
            target_path = ca_home / 'configs' / subdomain_file_name
            common_name = '$ENV::CEDAR_CA_COMMON_NAME'
            if subdomain_name:
                common_name = f'{subdomain_name}.{common_name}'
            target_content = source_file.replace(
                '<CEDAR.COMMON_NAME>.$ENV::CEDAR_CA_COMMON_NAME', common_name)
            target_path.write_text(target_content)
            (ca_home / 'certs' / subdomain_directory).mkdir(parents=True, exist_ok=True)
        return selected

    @staticmethod
    def _enable_duplicate_subjects(index_attributes):
        """Keep the CA history while allowing a leaf subject to be renewed."""
        lines = index_attributes.read_text().splitlines() if index_attributes.exists() else []
        retained = [line for line in lines if not line.strip().startswith('unique_subject')]
        retained.append('unique_subject = no')
        index_attributes.write_text('\n'.join(retained) + '\n')

    @staticmethod
    def _remove_files(paths):
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @classmethod
    def setup(cls):
        ca_home = cls.set_paths()
        configs = ca_home / 'configs'
        configs.mkdir(parents=True, exist_ok=True)
        (ca_home / 'certs').mkdir(parents=True, exist_ok=True)
        (ca_home / 'newcerts').mkdir(parents=True, exist_ok=True)

        source_config = Path(Util.get_asset_file_path(['certs', 'openssl--ca.cnf']))
        if not source_config.is_file():
            raise CertificateError("The CA certificate configuration template is missing.")
        target_config = configs / source_config.name
        if not target_config.exists():
            shutil.copyfile(source_config, target_config)

        serial = ca_home / 'serial'
        if not serial.exists():
            serial.write_text('00\n')
        (ca_home / 'index.txt').touch(exist_ok=True)
        cls._enable_duplicate_subjects(ca_home / 'index.txt.attr')
        cls.generate_domain_configs()
        return 0

    @classmethod
    def ensure_ca_and_domains(cls):
        """Create the local CA and any missing runtime certificate pairs without rotating existing ones."""
        returncode = cls.setup()
        if returncode:
            return returncode

        ca_home = cls.set_paths()
        ca_key = ca_home / 'ca.key'
        ca_certificate = ca_home / 'ca.crt'
        existing_ca = [path for path in (ca_key, ca_certificate) if path.exists()]
        if not existing_ca:
            returncode = cls.generate_ca()
            if returncode:
                return returncode
        elif len(existing_ca) != 2:
            missing = ca_certificate.name if ca_key.exists() else ca_key.name
            raise CertificateError(
                f"Certificate authority is incomplete ({missing} missing). Refusing to replace its other half."
            )

        missing_domains = []
        incomplete_domains = []
        for subdomain in cls.select_subdomains():
            cert_dir = ca_home / 'certs' / subdomain.get_cert_directory_name()
            fqdn = subdomain.get_fqdn()
            key = cert_dir / f'{fqdn}.key'
            request = cert_dir / f'{fqdn}.csr'
            certificate = cert_dir / f'{fqdn}.crt'
            existing = [path for path in (key, request, certificate) if path.exists()]
            if not existing:
                missing_domains.append(subdomain.name)
            elif not key.is_file() or not certificate.is_file():
                incomplete_domains.append(fqdn)

        if incomplete_domains:
            raise CertificateError(
                "Certificate pairs are incomplete for: " + ", ".join(incomplete_domains)
                + ". Refusing to overwrite partial local certificate state."
            )
        if missing_domains:
            return cls.generate_domains(missing_domains)
        return 0

    @classmethod
    def generate_ca(cls, force=False):
        ca_home = cls.set_paths()
        cls.require_ca_environment()
        config = ca_home / 'configs' / 'openssl--ca.cnf'
        if not config.is_file():
            raise CertificateError("The CA is not set up. Run 'cedarcli cert setup' first.")

        key = ca_home / 'ca.key'
        certificate = ca_home / 'ca.crt'
        existing = [path.name for path in (key, certificate) if path.exists()]
        if existing and not force:
            raise CertificateError(
                f"Refusing to overwrite {', '.join(existing)}. Re-run with --force only when replacing the CA deliberately."
            )

        temp_key = ca_home / '.ca.key.tmp'
        temp_certificate = ca_home / '.ca.crt.tmp'
        cls._remove_files((temp_key, temp_certificate))
        command = (
            f"openssl genrsa -des3 -passout pass:${{CEDAR_CA_PASSWORD}} -out {shlex.quote(temp_key.name)} 4096"
            " && "
            f"openssl req -new -x509 -days 3650 -passin pass:${{CEDAR_CA_PASSWORD}} "
            f"-key {shlex.quote(temp_key.name)} -out {shlex.quote(temp_certificate.name)} "
            f"-config {shlex.quote(str(config.relative_to(ca_home)))}"
        )
        result = Worker.execute_generic_shell_commands(
            [command],
            cwd=str(ca_home),
            title="Generating self-signed CA certificate",
        )
        if result.returncode:
            cls._remove_files((temp_key, temp_certificate))
            return result.returncode
        os.replace(temp_key, key)
        os.replace(temp_certificate, certificate)
        return 0

    @classmethod
    def generate_domains(cls, names=None, force=False):
        ca_home = cls.set_paths()
        cls.require_ca_environment()
        required = (ca_home / 'ca.key', ca_home / 'ca.crt', ca_home / 'configs' / 'openssl--ca.cnf')
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            raise CertificateError(
                f"Certificate authority is incomplete ({', '.join(missing)} missing). Run 'cedarcli cert setup' and 'cedarcli cert ca' first."
            )

        selected = cls.select_subdomains(names)
        existing = []
        for subdomain in selected:
            cert_dir = ca_home / 'certs' / subdomain.get_cert_directory_name()
            fqdn = subdomain.get_fqdn()
            existing.extend(path for path in (
                cert_dir / f'{fqdn}.key', cert_dir / f'{fqdn}.csr', cert_dir / f'{fqdn}.crt'
            ) if path.exists())
        if existing and not force:
            preview = ', '.join(str(path.relative_to(ca_home)) for path in existing[:3])
            remainder = f" and {len(existing) - 3} more" if len(existing) > 3 else ""
            raise CertificateError(
                f"Refusing to overwrite existing certificate files: {preview}{remainder}. Re-run with --force to renew them."
            )

        cls.generate_domain_configs(names)
        cls._enable_duplicate_subjects(ca_home / 'index.txt.attr')
        for subdomain in selected:
            subdomain_name = subdomain.get_fqdn()
            config_file_name = subdomain.get_config_file_name()
            subdomain_directory = subdomain.get_cert_directory_name()
            cert_dir = ca_home / 'certs' / subdomain_directory
            key = cert_dir / f'{subdomain_name}.key'
            request = cert_dir / f'{subdomain_name}.csr'
            certificate = cert_dir / f'{subdomain_name}.crt'
            temp_key = cert_dir / f'.{subdomain_name}.key.tmp'
            temp_request = cert_dir / f'.{subdomain_name}.csr.tmp'
            temp_certificate = cert_dir / f'.{subdomain_name}.crt.tmp'
            cls._remove_files((temp_key, temp_request, temp_certificate))
            command = (
                f"openssl genrsa -out {shlex.quote(temp_key.name)} 2048"
                " && "
                f"openssl req -new -sha256 -key {shlex.quote(temp_key.name)} "
                f"-out {shlex.quote(temp_request.name)} -config ../../configs/{shlex.quote(config_file_name)}"
                " && "
                f"openssl ca -batch -cert ../../ca.crt -keyfile ../../ca.key "
                f"-in {shlex.quote(temp_request.name)} -out {shlex.quote(temp_certificate.name)} "
                "-passin pass:$CEDAR_CA_PASSWORD -outdir ./ "
                f"-config ../../configs/{shlex.quote(config_file_name)} -verbose -extensions v3_req"
            )
            result = Worker.execute_generic_shell_commands(
                [command],
                cwd=str(cert_dir),
                title="Generating certificate for subdomain: " + subdomain_name,
            )
            if result.returncode:
                cls._remove_files((temp_key, temp_request, temp_certificate))
                return result.returncode
            os.replace(temp_key, key)
            os.replace(temp_request, request)
            os.replace(temp_certificate, certificate)
        return 0

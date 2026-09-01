import unittest
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import cert
from org.metadatacenter.config.SubdomainsFactory import SubdomainsFactory
from org.metadatacenter.worker.CertificateWorker import CertificateError, CertificateWorker


class CertificateTargetsTest(unittest.TestCase):

    CA_ENVIRONMENT = {
        "CEDAR_CA_PASSWORD": "secret",
        "CEDAR_CA_COUNTRY": "US",
        "CEDAR_CA_STATE": "California",
        "CEDAR_CA_LOC": "Stanford",
        "CEDAR_CA_ORG": "CEDAR",
        "CEDAR_CA_ORG_UNIT": "Development",
        "CEDAR_CA_COMMON_NAME": "metadatacenter.orgx",
        "CEDAR_CA_EMAIL": "cedar@example.org",
    }

    def setUp(self):
        self.runner = CliRunner()

    def test_split_frontend_certificate_subdomains_are_registered(self):
        subdomains = SubdomainsFactory.build_subdomains()

        self.assertIn("workspace", subdomains.map)
        self.assertIn("designer", subdomains.map)

    @patch("org.metadatacenter.worker.CertificateWorker.GlobalContext.subdomains")
    def test_selected_certificate_generation_is_exact_and_deduplicated(self, subdomains):
        configured = SubdomainsFactory.build_subdomains()
        subdomains.map = configured.map

        selected = CertificateWorker.select_subdomains(
            ["workspace", "designer", "workspace"]
        )

        self.assertEqual(["workspace", "designer"], [item.name for item in selected])

    @patch("org.metadatacenter.worker.CertificateWorker.GlobalContext.subdomains")
    def test_unknown_certificate_subdomain_is_rejected(self, subdomains):
        configured = SubdomainsFactory.build_subdomains()
        subdomains.map = configured.map

        with self.assertRaisesRegex(ValueError, "Unknown certificate subdomain"):
            CertificateWorker.select_subdomains(["not-configured"])

    @patch("org.metadatacenter.worker.CertificateWorker.CertificateWorker.generate_domain_configs")
    def test_setup_preserves_existing_ca_state(self, generate_domain_configs):
        with tempfile.TemporaryDirectory() as temp_dir:
            ca_home = Path(temp_dir, "ca")
            configs = ca_home / "configs"
            configs.mkdir(parents=True)
            source = Path(temp_dir, "openssl--ca.cnf")
            source.write_text("new template")
            target = configs / source.name
            target.write_text("local configuration")
            (ca_home / "serial").write_text("42\n")
            (ca_home / "index.txt").write_text("existing issuance\n")

            with patch.dict(os.environ, {"CEDAR_CA_HOME": str(ca_home)}), \
                    patch("org.metadatacenter.worker.CertificateWorker.Util.get_asset_file_path",
                          return_value=str(source)):
                returncode = CertificateWorker.setup()

            self.assertEqual(0, returncode)
            self.assertEqual("local configuration", target.read_text())
            self.assertEqual("42\n", (ca_home / "serial").read_text())
            self.assertEqual("existing issuance\n", (ca_home / "index.txt").read_text())
            self.assertEqual("unique_subject = no\n", (ca_home / "index.txt.attr").read_text())
            self.assertTrue((ca_home / "newcerts").is_dir())
            generate_domain_configs.assert_called_once_with()

    @patch.object(CertificateWorker, "generate_domains", return_value=0)
    @patch.object(CertificateWorker, "generate_ca", return_value=0)
    @patch.object(CertificateWorker, "setup", return_value=0)
    def test_ensure_ca_and_domains_creates_missing_material(self, setup, generate_ca, generate_domains):
        with tempfile.TemporaryDirectory() as temp_dir:
            domains = [
                SimpleNamespace(
                    name="",
                    get_fqdn=lambda: "metadatacenter.orgx",
                    get_cert_directory_name=lambda: "-metadatacenter.orgx",
                ),
                SimpleNamespace(
                    name="workspace",
                    get_fqdn=lambda: "workspace.metadatacenter.orgx",
                    get_cert_directory_name=lambda: "workspace.metadatacenter.orgx",
                ),
            ]
            environment = {**self.CA_ENVIRONMENT, "CEDAR_CA_HOME": temp_dir}
            with patch.dict(os.environ, environment), \
                    patch.object(CertificateWorker, "select_subdomains", return_value=domains):
                returncode = CertificateWorker.ensure_ca_and_domains()

        self.assertEqual(0, returncode)
        setup.assert_called_once_with()
        generate_ca.assert_called_once_with()
        generate_domains.assert_called_once_with(["", "workspace"])

    @patch.object(CertificateWorker, "generate_domains", return_value=0)
    @patch.object(CertificateWorker, "generate_ca", return_value=0)
    @patch.object(CertificateWorker, "setup", return_value=0)
    def test_ensure_ca_and_domains_preserves_complete_pairs(
            self, _setup, generate_ca, generate_domains):
        with tempfile.TemporaryDirectory() as temp_dir:
            ca_home = Path(temp_dir)
            (ca_home / "ca.key").write_text("key")
            (ca_home / "ca.crt").write_text("certificate")
            root_dir = ca_home / "certs" / "-metadatacenter.orgx"
            root_dir.mkdir(parents=True)
            (root_dir / "metadatacenter.orgx.key").write_text("key")
            (root_dir / "metadatacenter.orgx.crt").write_text("certificate")
            domains = [
                SimpleNamespace(
                    name="",
                    get_fqdn=lambda: "metadatacenter.orgx",
                    get_cert_directory_name=lambda: "-metadatacenter.orgx",
                ),
                SimpleNamespace(
                    name="workspace",
                    get_fqdn=lambda: "workspace.metadatacenter.orgx",
                    get_cert_directory_name=lambda: "workspace.metadatacenter.orgx",
                ),
            ]
            environment = {**self.CA_ENVIRONMENT, "CEDAR_CA_HOME": temp_dir}
            with patch.dict(os.environ, environment), \
                    patch.object(CertificateWorker, "select_subdomains", return_value=domains):
                returncode = CertificateWorker.ensure_ca_and_domains()

        self.assertEqual(0, returncode)
        generate_ca.assert_not_called()
        generate_domains.assert_called_once_with(["workspace"])

    @patch.object(CertificateWorker, "setup", return_value=0)
    def test_ensure_ca_and_domains_rejects_incomplete_ca(self, _setup):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "ca.key").write_text("key")
            environment = {**self.CA_ENVIRONMENT, "CEDAR_CA_HOME": temp_dir}

            with patch.dict(os.environ, environment):
                with self.assertRaisesRegex(CertificateError, "ca.crt missing"):
                    CertificateWorker.ensure_ca_and_domains()

    @patch.object(CertificateWorker, "setup", return_value=0)
    def test_ensure_ca_and_domains_rejects_incomplete_leaf_pair(self, _setup):
        with tempfile.TemporaryDirectory() as temp_dir:
            ca_home = Path(temp_dir)
            (ca_home / "ca.key").write_text("key")
            (ca_home / "ca.crt").write_text("certificate")
            cert_dir = ca_home / "certs" / "workspace.metadatacenter.orgx"
            cert_dir.mkdir(parents=True)
            (cert_dir / "workspace.metadatacenter.orgx.key").write_text("key")
            domain = SimpleNamespace(
                name="workspace",
                get_fqdn=lambda: "workspace.metadatacenter.orgx",
                get_cert_directory_name=lambda: "workspace.metadatacenter.orgx",
            )
            environment = {**self.CA_ENVIRONMENT, "CEDAR_CA_HOME": temp_dir}

            with patch.dict(os.environ, environment), \
                    patch.object(CertificateWorker, "select_subdomains", return_value=[domain]):
                with self.assertRaisesRegex(CertificateError, "workspace.metadatacenter.orgx"):
                    CertificateWorker.ensure_ca_and_domains()

    @patch("org.metadatacenter.worker.CertificateWorker.Worker.execute_generic_shell_commands")
    def test_ca_refuses_to_overwrite_without_force(self, execute):
        with tempfile.TemporaryDirectory() as temp_dir:
            ca_home = Path(temp_dir)
            Path(ca_home, "configs").mkdir()
            Path(ca_home, "configs", "openssl--ca.cnf").write_text("config")
            Path(ca_home, "ca.key").write_text("existing key")

            environment = {**self.CA_ENVIRONMENT, "CEDAR_CA_HOME": str(ca_home)}
            with patch.dict(os.environ, environment):
                with self.assertRaisesRegex(CertificateError, "Refusing to overwrite ca.key"):
                    CertificateWorker.generate_ca()

        execute.assert_not_called()

    @patch("org.metadatacenter.worker.CertificateWorker.Worker.execute_generic_shell_commands")
    def test_failed_forced_ca_generation_preserves_existing_ca(self, execute):
        execute.return_value = SimpleNamespace(returncode=7)
        with tempfile.TemporaryDirectory() as temp_dir:
            ca_home = Path(temp_dir)
            Path(ca_home, "configs").mkdir()
            Path(ca_home, "configs", "openssl--ca.cnf").write_text("config")
            Path(ca_home, "ca.key").write_text("existing key")
            Path(ca_home, "ca.crt").write_text("existing certificate")

            environment = {**self.CA_ENVIRONMENT, "CEDAR_CA_HOME": str(ca_home)}
            with patch.dict(os.environ, environment):
                returncode = CertificateWorker.generate_ca(force=True)

            self.assertEqual(7, returncode)
            self.assertEqual("existing key", Path(ca_home, "ca.key").read_text())
            self.assertEqual("existing certificate", Path(ca_home, "ca.crt").read_text())

    @patch("org.metadatacenter.worker.CertificateWorker.GlobalContext.subdomains")
    def test_domains_refuse_to_overwrite_without_force(self, subdomains):
        configured = SubdomainsFactory.build_subdomains()
        subdomains.map = configured.map
        with tempfile.TemporaryDirectory() as temp_dir:
            ca_home = Path(temp_dir)
            Path(ca_home, "configs").mkdir()
            Path(ca_home, "configs", "openssl--ca.cnf").write_text("config")
            Path(ca_home, "ca.key").write_text("key")
            Path(ca_home, "ca.crt").write_text("certificate")
            cert_dir = Path(ca_home, "certs", "workspace.metadatacenter.orgx")
            cert_dir.mkdir(parents=True)
            Path(cert_dir, "workspace.metadatacenter.orgx.crt").write_text("existing")

            environment = {**self.CA_ENVIRONMENT, "CEDAR_CA_HOME": str(ca_home)}
            with patch.dict(os.environ, environment):
                with self.assertRaisesRegex(CertificateError, "Refusing to overwrite"):
                    CertificateWorker.generate_domains(["workspace"])

    @patch("org.metadatacenter.worker.CertificateWorker.GlobalContext.subdomains")
    def test_root_domain_configuration_has_no_leading_dot(self, subdomains):
        configured = SubdomainsFactory.build_subdomains()
        subdomains.map = configured.map
        with tempfile.TemporaryDirectory() as temp_dir:
            template = Path(temp_dir, "openssl-domain.cnf")
            template.write_text(
                "commonName=<CEDAR.COMMON_NAME>.$ENV::CEDAR_CA_COMMON_NAME\n"
                "DNS.1=<CEDAR.COMMON_NAME>.$ENV::CEDAR_CA_COMMON_NAME\n"
            )
            ca_home = Path(temp_dir, "ca")
            with patch.dict(os.environ, {
                    "CEDAR_CA_HOME": str(ca_home),
                    "CEDAR_CA_COMMON_NAME": "metadatacenter.orgx",
            }), patch("org.metadatacenter.worker.CertificateWorker.Util.get_asset_file_path",
                      return_value=str(template)):
                CertificateWorker.generate_domain_configs([""])

            config = Path(ca_home, "configs", "openssl--nosubdomain.cnf").read_text()
            self.assertIn("commonName=$ENV::CEDAR_CA_COMMON_NAME", config)
            self.assertNotIn("commonName=.$ENV", config)

    @patch("org.metadatacenter.cert.CertificateWorker.generate_ca",
           side_effect=CertificateError("safe failure"))
    def test_certificate_cli_reports_clean_error(self, generate_ca):
        result = self.runner.invoke(cert.app, ["ca"])

        self.assertEqual(1, result.exit_code)
        self.assertIn("safe failure", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()

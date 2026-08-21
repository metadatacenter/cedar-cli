import unittest
from unittest.mock import patch

from org.metadatacenter.config.SubdomainsFactory import SubdomainsFactory
from org.metadatacenter.worker.CertificateWorker import CertificateWorker


class CertificateTargetsTest(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()

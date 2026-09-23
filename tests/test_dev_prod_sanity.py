import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import dev, prod
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.DevWorker import DevWorker
from org.metadatacenter.worker.ProdWorker import ProdError, ProdWorker


class DevSanityTest(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    def test_create_directories_includes_current_native_runtime_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(Util, "cedar_home", temp_dir):
            self.assertEqual(0, DevWorker.create_directories())

            self.assertTrue(Path(temp_dir, "log", "run").is_dir())
            self.assertTrue(Path(temp_dir, "log", "frontend-workspace").is_dir())
            self.assertTrue(Path(temp_dir, "log", "frontend-designer").is_dir())

    def test_create_directories_leaves_out_the_terminology_cache(self):
        """
        Nothing reads `cache/terminology`: the configuration property and the constants
        naming its files are gone, so bring-up stopped creating it. An empty directory
        that looks like part of a running system is worse than no directory, and this
        is here so it does not come back with the next reader who finds one on an old
        machine and assumes it is missing.
        """
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(Util, "cedar_home", temp_dir):
            self.assertEqual(0, DevWorker.create_directories())

            self.assertFalse(Path(temp_dir, "cache").exists())

    @patch("org.metadatacenter.worker.DevWorker.console")
    def test_api_key_output_does_not_disclose_salt(self, dev_console):
        with patch.dict(os.environ, {"CEDAR_SALT_API_KEY": "do-not-print-this"}):
            api_key = DevWorker.generate_api_key("user-id")

        self.assertEqual(64, len(api_key))
        table = dev_console.print.call_args.args[0]
        rendered_cells = [str(cell) for column in table.columns for cell in column.cells]
        self.assertNotIn("do-not-print-this", rendered_cells)
        self.assertIn("user-id", rendered_cells)

    @patch("org.metadatacenter.dev.DevWorker.create_directories", return_value=6)
    def test_dev_command_propagates_worker_failure(self, create_directories):
        result = self.runner.invoke(dev.app, ["create-directories"])

        self.assertEqual(6, result.exit_code)


class ProdSanityTest(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    @staticmethod
    def create_frontend_payloads(cedar_home, bundle_names=None):
        """Fixtures shaped like what the Angular build actually emits.

        The predecessor of these tests wrote an index.html carrying `window.cedarDomain`, a shape
        the estate stopped producing in 2.9.8. The tests passed while the command could only raise
        in the field, so the fixture must be the built bundle: a content-hashed main-*.js with the
        configuration compiled into it, beside an index.html that references it and carries no
        configuration of its own.
        """
        names = bundle_names or {}
        for repo in ("cedar-openview", "cedar-bridging", "cedar-monitoring"):
            dist = Path(cedar_home, repo, f"{repo}-dist")
            dist.mkdir(parents=True)
            for bundle in names.get(repo, ["main-A3S6MZD7.js"]):
                Path(dist, bundle).write_text(
                    'const e={production:!0,cedarDomain:"metadatacenter.org"};'
                )
            Path(dist, "index.html").write_text(
                '<script src="main-A3S6MZD7.js" type="module"></script>'
            )

    def test_configure_frontends_rewrites_the_compiled_domain_in_every_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_payloads(temp_dir)
            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": "staging.example.org"}):
                self.assertEqual(0, ProdWorker.configure_frontends())
                payload_files = ProdWorker.frontend_payload_files()

            self.assertEqual(3, len(payload_files))
            for _, path in payload_files:
                content = path.read_text()
                self.assertIn('cedarDomain:"staging.example.org"', content)
                self.assertNotIn('cedarDomain:"metadatacenter.org"', content)

    def test_configure_frontends_leaves_no_temporary_files_behind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_payloads(temp_dir)
            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": "staging.example.org"}):
                ProdWorker.configure_frontends()

            leftovers = list(Path(temp_dir).rglob(".*.cedarcli.tmp"))
            self.assertEqual([], leftovers)

    def test_configure_frontends_refuses_a_bundle_without_a_compiled_domain(self):
        """The guard the hand `sed` could not have: a bundle that matches nothing must fail loudly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_payloads(temp_dir)
            broken = Path(temp_dir, "cedar-monitoring", "cedar-monitoring-dist", "main-A3S6MZD7.js")
            broken.write_text("const e={production:!0};")
            original_openview = Path(
                temp_dir, "cedar-openview", "cedar-openview-dist", "main-A3S6MZD7.js").read_text()

            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": "staging.example.org"}):
                with self.assertRaisesRegex(ProdError, "Cannot find a compiled cedarDomain"):
                    ProdWorker.configure_frontends()

            self.assertEqual(original_openview, Path(
                temp_dir, "cedar-openview", "cedar-openview-dist", "main-A3S6MZD7.js").read_text())

    def test_configure_frontends_refuses_an_unbuilt_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_payloads(temp_dir)
            Path(temp_dir, "cedar-bridging", "cedar-bridging-dist", "main-A3S6MZD7.js").unlink()

            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": "staging.example.org"}):
                with self.assertRaisesRegex(ProdError, "no built bundle"):
                    ProdWorker.configure_frontends()

    def test_configure_frontends_refuses_an_ambiguous_dist(self):
        """Two bundles means picking one is a guess about which tree nginx serves."""
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_payloads(
                temp_dir, {"cedar-monitoring": ["main-A3S6MZD7.js", "main-EMS6U332.js"]})

            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": "staging.example.org"}):
                with self.assertRaisesRegex(ProdError, "holds several bundles"):
                    ProdWorker.configure_frontends()

    def test_configure_frontends_requires_a_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_payloads(temp_dir)
            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": ""}):
                with self.assertRaisesRegex(ProdError, "CEDAR_HOST is not set"):
                    ProdWorker.configure_frontends()

    @patch("org.metadatacenter.worker.ProdWorker.Worker.execute_generic_shell_commands")
    def test_reset_frontends_restores_the_bundle_by_repository_relative_path(self, execute):
        execute.return_value = SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_payloads(temp_dir)
            with patch.object(Util, "cedar_home", temp_dir):
                self.assertEqual(0, ProdWorker.reset_frontends())

        self.assertEqual(3, execute.call_count)
        for call in execute.call_args_list:
            command = call.args[0][0]
            self.assertTrue(command.startswith("git restore --source=HEAD -- "))
            self.assertIn("main-A3S6MZD7.js", command)
            self.assertNotIn(temp_dir, command)

    @patch("org.metadatacenter.prod.ProdWorker.configure_frontends", return_value=8)
    def test_prod_command_propagates_worker_failure(self, configure_frontends):
        result = self.runner.invoke(prod.app, ["configure-frontends"])

        self.assertEqual(8, result.exit_code)


if __name__ == "__main__":
    unittest.main()

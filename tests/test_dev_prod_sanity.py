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
            self.assertTrue(Path(temp_dir, "cache", "terminology").is_dir())

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
    def create_frontend_indexes(cedar_home):
        for repo, dist in (
                ("cedar-openview", "cedar-openview-dist"),
                ("cedar-bridging", "cedar-bridging-dist"),
                ("cedar-monitoring", "cedar-monitoring-dist")):
            path = Path(cedar_home, repo, dist, "index.html")
            path.parent.mkdir(parents=True)
            path.write_text(
                '<script>window.cedarDomain = "metadatacenter.org";</script> '
                'https://content.metadatacenter.org/example'
            )

    def test_configure_frontends_updates_all_entry_points(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_indexes(temp_dir)
            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": "example.org"}):
                self.assertEqual(0, ProdWorker.configure_frontends())
                index_files = ProdWorker.frontend_index_files()

            for _, path in index_files:
                content = path.read_text()
                self.assertIn('window.cedarDomain = "example.org"', content)
                self.assertIn('content.example.org/', content)

    def test_configure_frontends_refuses_an_unrecognized_entry_point(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_indexes(temp_dir)
            broken = Path(temp_dir, "cedar-monitoring", "cedar-monitoring-dist", "index.html")
            broken.write_text("no domain declaration")
            original_openview = Path(
                temp_dir, "cedar-openview", "cedar-openview-dist", "index.html").read_text()

            with patch.object(Util, "cedar_home", temp_dir), \
                    patch.dict(os.environ, {"CEDAR_HOST": "example.org"}):
                with self.assertRaisesRegex(ProdError, "Cannot find window.cedarDomain"):
                    ProdWorker.configure_frontends()

            self.assertEqual(original_openview, Path(
                temp_dir, "cedar-openview", "cedar-openview-dist", "index.html").read_text())

    @patch("org.metadatacenter.worker.ProdWorker.Worker.execute_generic_shell_commands")
    def test_reset_frontends_uses_repository_relative_git_restore(self, execute):
        execute.return_value = SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as temp_dir:
            self.create_frontend_indexes(temp_dir)
            with patch.object(Util, "cedar_home", temp_dir):
                self.assertEqual(0, ProdWorker.reset_frontends())

        self.assertEqual(3, execute.call_count)
        for call in execute.call_args_list:
            command = call.args[0][0]
            self.assertTrue(command.startswith("git restore --source=HEAD -- "))
            self.assertNotIn(temp_dir, command)

    @patch("org.metadatacenter.prod.ProdWorker.configure_frontends", return_value=8)
    def test_prod_command_propagates_worker_failure(self, configure_frontends):
        result = self.runner.invoke(prod.app, ["configure-frontends"])

        self.assertEqual(8, result.exit_code)


if __name__ == "__main__":
    unittest.main()

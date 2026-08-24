import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import build
from org.metadatacenter.util.BuildTrain import BuildTrain, DockerTrain
from org.metadatacenter.util.Util import Util


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class BuildTrainTest(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_allocate_uses_parent_snapshot_and_utc_minute(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Util, 'cedar_home', directory):
            parent = Path(directory) / 'cedar-parent'
            parent.mkdir()
            (parent / 'pom.xml').write_text(
                '<project><artifactId>cedar-parent</artifactId><version>2.9.3-SNAPSHOT</version></project>',
                encoding='utf-8',
            )
            self.assertEqual(
                '2.9.3-dev.20260824.1847',
                BuildTrain.allocate(dt.datetime(2026, 8, 24, 18, 47, tzinfo=dt.timezone.utc)),
            )

    def test_current_reads_completed_pointer(self):
        def opener(url, timeout):
            self.assertTrue(url.endswith('/current.json'))
            self.assertEqual(15, timeout)
            return Response(json.dumps({'version': '2.9.3-dev.20260824.1847'}).encode())

        self.assertEqual(
            '2.9.3-dev.20260824.1847',
            BuildTrain.current(environment={}, opener=opener),
        )

    def test_docker_current_reads_only_the_verified_image_pointer(self):
        def opener(url, timeout):
            self.assertTrue(url.endswith('/docker/current.json'))
            self.assertEqual(15, timeout)
            return Response(json.dumps({'version': '2.9.3-dev.20260824.1847'}).encode())

        self.assertEqual(
            '2.9.3-dev.20260824.1847',
            DockerTrain.current(environment={}, opener=opener),
        )

    @patch.object(BuildTrain, 'allocate', return_value='2.9.3-dev.20260824.1847')
    @patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run')
    def test_cli_allocates_and_dispatches_a_new_train(self, run, allocate):
        run.return_value.returncode = 0
        result = self.runner.invoke(build.app, ['train'])
        self.assertEqual(0, result.exit_code, result.output)
        allocate.assert_called_once_with()
        self.assertIn('version=2.9.3-dev.20260824.1847', run.call_args.args[0])
        self.assertIn('resume=false', run.call_args.args[0])
        self.assertIn('main', run.call_args.args[0])

    def test_cli_does_not_expose_a_version_option(self):
        result = self.runner.invoke(build.app, [
            'train', '--version', '2.9.3-dev.20260824.1847',
        ])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn('No such option', result.output)

    @patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run')
    def test_cli_resume_uses_recorded_id(self, run):
        run.return_value.returncode = 0
        result = self.runner.invoke(build.app, [
            'train', '--resume', '2.9.3-dev.20260824.1847',
        ])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('resume=true', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()

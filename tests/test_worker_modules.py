"""Worker components are independent of their compatibility APIs."""
import subprocess
import sys
import unittest


class WorkerModuleTest(unittest.TestCase):
    def test_components_do_not_import_the_worker_facades(self):
        result = subprocess.run([sys.executable, '-c', '''
import importlib,pkgutil,sys
for name in ('docker_support', 'train_support'):
    package = importlib.import_module('org.metadatacenter.' + name)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(package.__name__ + '.' + module.name)
assert 'org.metadatacenter.worker.DockerWorker' not in sys.modules
assert 'org.metadatacenter.worker.BuildTrainWorker' not in sys.modules
'''], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

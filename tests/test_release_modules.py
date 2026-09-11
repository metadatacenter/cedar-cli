"""Component imports must remain usable without loading the CLI facade."""
import importlib
import inspect
import pkgutil
import subprocess
import sys
import typing
import unittest

from org.metadatacenter import release_support, release_train


class ReleaseModuleTest(unittest.TestCase):
    def test_components_import_independently_of_commands(self):
        result = subprocess.run([sys.executable, '-c',
            'import importlib,pkgutil,sys; from org.metadatacenter import release_support; '
            '[importlib.import_module(release_support.__name__+"."+m.name) '
            'for m in pkgutil.iter_modules(release_support.__path__)]; '
            'assert "org.metadatacenter.release_train" not in sys.modules'],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_reexports_preserve_identity_and_annotations_resolve(self):
        for entry in pkgutil.iter_modules(release_support.__path__):
            module = importlib.import_module(release_support.__name__ + '.' + entry.name)
            for name, value in vars(module).items():
                if getattr(value, '__module__', None) != module.__name__:
                    continue
                if inspect.isclass(value) or inspect.isfunction(value):
                    self.assertIs(value, getattr(release_train, name))
                targets = ([value] if inspect.isfunction(value) else
                           [member for _, member in inspect.getmembers(value)
                            if inspect.isfunction(member) or inspect.ismethod(member)]
                           if inspect.isclass(value) else [])
                for target in targets:
                    typing.get_type_hints(target)

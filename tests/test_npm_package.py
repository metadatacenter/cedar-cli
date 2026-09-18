import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from org.metadatacenter import npm_package
from org.metadatacenter.util.ProcessRunner import CommandOutput


def archive_bytes(manifests, *, symlink=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for manifest in manifests:
            member = tarfile.TarInfo("package/package.json")
            if symlink:
                member.type = tarfile.SYMTYPE
                member.linkname = "elsewhere"
                archive.addfile(member)
            else:
                member.size = len(manifest)
                archive.addfile(member, io.BytesIO(manifest))
    return output.getvalue()


class InspectionTest(unittest.TestCase):
    def test_hashes_describe_exact_archive_bytes(self):
        package = {"name": "test-package", "version": "1.0.0", "gitHead": "provenance"}
        content = archive_bytes([json.dumps(package).encode()])
        result = npm_package.inspect_tarball(content, "test-package")
        self.assertEqual(package, result.package)
        self.assertEqual(hashlib.sha256(content).hexdigest(), result.sha256)
        self.assertEqual("sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode(),
                         result.integrity)

    def test_rejects_ambiguous_nonregular_or_invalid_manifests(self):
        for content in (b"not gzip", archive_bytes([]), archive_bytes([b'{}', b'{}']),
                        archive_bytes([b'{}'], symlink=True), archive_bytes([b'[]']),
                        archive_bytes([b'bad json']), archive_bytes([b'\xff'])):
            with self.subTest(content=content[:20]), self.assertRaises(npm_package.NpmPackageError):
                npm_package.inspect_tarball(content, "broken")


class PackFailureTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "package.json").write_text(json.dumps({"name": "test-package", "version": "1.0.0"}))
        self.destination = self.root / "packed"

    def pack(self):
        return npm_package.pack_and_inspect(self.source, self.destination)

    def test_command_failure_and_missing_output_are_not_success(self):
        for result in (CommandOutput(["pack failed"], 1), CommandOutput([], -9), CommandOutput([], 0)):
            with self.subTest(returncode=result.returncode), \
                    patch.object(npm_package, "run_process", return_value=result), \
                    self.assertRaises(npm_package.NpmPackageError):
                self.pack()

    def test_rejects_wrong_identity_or_previous_output(self):
        def fake_pack(*args, **kwargs):
            (self.destination / "package.tgz").write_bytes(archive_bytes([
                b'{"name":"different-package","version":"1.0.0"}']))
            return CommandOutput([], 0)
        with patch.object(npm_package, "run_process", side_effect=fake_pack):
            with self.assertRaisesRegex(npm_package.NpmPackageError, "identity differs"):
                self.pack()
        # A caller cannot accidentally mistake a previous pack's artifact for new output.
        with patch.object(npm_package, "run_process") as run:
            with self.assertRaisesRegex(npm_package.NpmPackageError, "already contains"):
                self.pack()
            run.assert_not_called()

    def test_passes_build_environment_and_disables_lifecycle_scripts(self):
        environment = {"npm_config_cache": str(self.root / "private-cache")}
        def fake_pack(argv, *, cwd, env):
            self.assertEqual(str(self.source), cwd)
            self.assertIs(environment, env)
            self.assertIn("--ignore-scripts", argv)
            self.assertEqual(str(self.destination), argv[argv.index("--pack-destination") + 1])
            (self.destination / "package.tgz").write_bytes(archive_bytes([
                (self.source / "package.json").read_bytes()]))
            return CommandOutput([], 0)
        with patch.object(npm_package, "run_process", side_effect=fake_pack):
            path, result = npm_package.pack_and_inspect(self.source, self.destination, environment=environment)
        self.assertEqual(self.destination / "package.tgz", path)
        self.assertEqual("test-package", result.package["name"])

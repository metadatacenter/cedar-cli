"""The release's Node is provisioned from a verified nodejs.org archive when no installed one fits."""
import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter.release_support import archives, node_provision
from org.metadatacenter.release_support.node_provision import (
    NodeProvisionError,
    node_platform,
    provision_node,
)
from org.metadatacenter.release_support.policy import REQUIRED_NODE_VERSION
from org.metadatacenter.release_support.toolchain import ToolchainResolver

NAME = f"node-{REQUIRED_NODE_VERSION}-darwin-arm64"


def archive(version=REQUIRED_NODE_VERSION, name=NAME) -> bytes:
    """A nodejs.org-shaped archive whose node only reports its version."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        script = f"#!/bin/sh\necho {version}\n".encode()
        info = tarfile.TarInfo(f"{name}/bin/node")
        info.size, info.mode = len(script), 0o755
        tar.addfile(info, io.BytesIO(script))
        link = tarfile.TarInfo(f"{name}/bin/npm")
        link.type, link.linkname = tarfile.SYMTYPE, "../lib/node_modules/npm/bin/npm-cli.js"
        tar.addfile(link)
    return buffer.getvalue()


class Fetcher:
    def __init__(self, body: bytes):
        self.body, self.urls = body, []

    def __call__(self, url, destination):
        self.urls.append(url)
        Path(destination).write_bytes(self.body)


def pinned(body: bytes):
    return patch.dict(node_provision.REQUIRED_NODE_ARCHIVE_SHA256,
                      {"darwin-arm64": hashlib.sha256(body).hexdigest()})


class NodeProvisionTest(unittest.TestCase):

    def test_names_the_platforms_nodejs_publishes(self):
        self.assertEqual("darwin-arm64", node_platform("Darwin", "arm64"))
        self.assertEqual("darwin-x64", node_platform("Darwin", "x86_64"))
        self.assertEqual("linux-arm64", node_platform("Linux", "aarch64"))
        self.assertEqual("linux-x64", node_platform("Linux", "amd64"))
        self.assertIsNone(node_platform("Windows", "AMD64"))

    def test_a_verified_archive_is_unpacked_once_and_then_reused(self):
        body = archive()
        fetch = Fetcher(body)
        with tempfile.TemporaryDirectory() as home, pinned(body):
            first = provision_node(home, system="Darwin", machine="arm64", fetch=fetch)
            second = provision_node(home, system="Darwin", machine="arm64", fetch=fetch)
            self.assertEqual(Path(home) / ".cedar" / "toolchains" / NAME / "bin", first)
            self.assertEqual(first, second)
            self.assertTrue(os.access(first / "node", os.X_OK))
        self.assertEqual([f"https://nodejs.org/dist/{REQUIRED_NODE_VERSION}/{NAME}.tar.xz"], fetch.urls)

    def test_an_archive_that_does_not_match_the_pin_is_never_unpacked(self):
        with tempfile.TemporaryDirectory() as home, pinned(b"another archive"):
            with self.assertRaisesRegex(NodeProvisionError, "nothing was unpacked"):
                provision_node(home, system="Darwin", machine="arm64", fetch=Fetcher(archive()))
            toolchains = Path(home) / ".cedar" / "toolchains"
            self.assertEqual([], [path.name for path in toolchains.iterdir()])

    def test_a_host_the_cli_pins_no_archive_for_is_refused(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaisesRegex(NodeProvisionError, "pins no Node"):
                provision_node(home, system="Windows", machine="AMD64", fetch=Fetcher(b""))

    def test_a_failed_download_is_reported_rather_than_raised(self):
        def offline(url, destination):
            raise OSError("network is unreachable")
        with tempfile.TemporaryDirectory() as home, pinned(b""):
            with self.assertRaisesRegex(NodeProvisionError, "could not be downloaded"):
                provision_node(home, system="Darwin", machine="arm64", fetch=offline)

    def test_a_python_without_the_data_filter_unpacks_the_archive_and_its_links(self):
        # Python 3.10.11, the macOS CI baseline, has no extractall(filter=...).
        body = archive()
        with tempfile.TemporaryDirectory() as home, pinned(body), \
                patch.object(archives, "HAS_DATA_FILTER", False):
            bin_dir = provision_node(home, system="Darwin", machine="arm64", fetch=Fetcher(body))
            self.assertTrue(os.access(bin_dir / "node", os.X_OK))
            self.assertEqual("../lib/node_modules/npm/bin/npm-cli.js", os.readlink(bin_dir / "npm"))

    def test_a_python_without_the_data_filter_refuses_a_link_that_leaves_the_toolchain(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
            link = tarfile.TarInfo(f"{NAME}/bin/node")
            link.type, link.linkname = tarfile.SYMTYPE, "../../../../etc/passwd"
            tar.addfile(link)
        body = buffer.getvalue()
        with tempfile.TemporaryDirectory() as home, pinned(body), \
                patch.object(archives, "HAS_DATA_FILTER", False):
            with self.assertRaisesRegex(NodeProvisionError, "leaves its destination"):
                provision_node(home, system="Darwin", machine="arm64", fetch=Fetcher(body))
            self.assertEqual([], list((Path(home) / ".cedar" / "toolchains").iterdir()))


class ResolverProvisionTest(unittest.TestCase):

    @staticmethod
    def runner(answers):
        """A command runner answering `node --version` by which node is asked."""
        def run(args, **_):
            result = type("Result", (), {})()
            result.returncode, result.stderr = 0, ""
            result.stdout = answers.get(args[0], "")
            if not result.stdout:
                result.returncode = 127
            return result
        return run

    def test_the_provisioned_node_goes_first_on_path_when_no_installed_one_fits(self):
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home) / ".cedar" / "toolchains" / NAME / "bin"
            environment = {"PATH": "/usr/bin", "CEDAR_HOME": home}
            resolver = ToolchainResolver(
                environment, exists=lambda path: False, provision=lambda _: directory,
                command_runner=self.runner({"node": "v26.13.1", str(directory / "node"): REQUIRED_NODE_VERSION}))
            notes = resolver._resolve_node()
        self.assertEqual(f"{directory}{os.pathsep}/usr/bin", environment["PATH"])
        self.assertIn("provisions from nodejs.org; the shell offered Node v26.13.1", notes[0])

    def test_a_failed_provision_is_reported_and_leaves_path_alone(self):
        def refuse(_):
            raise NodeProvisionError("network is unreachable")
        environment = {"PATH": "/usr/bin", "CEDAR_HOME": "/nowhere"}
        resolver = ToolchainResolver(environment, exists=lambda path: False, provision=refuse,
                                     command_runner=self.runner({"node": "v26.13.1"}))
        notes = resolver._resolve_node()
        self.assertEqual("/usr/bin", environment["PATH"])
        self.assertEqual([f"Node {REQUIRED_NODE_VERSION} could not be provisioned: network is unreachable"], notes)

    def test_an_installed_node_of_the_release_version_is_used_without_provisioning(self):
        def never(_):
            raise AssertionError("provisioned although the shell offered the release's Node")
        environment = {"PATH": "/usr/bin", "CEDAR_HOME": "/nowhere"}
        resolver = ToolchainResolver(environment, provision=never,
                                     command_runner=self.runner({"node": REQUIRED_NODE_VERSION}))
        self.assertEqual([], resolver._resolve_node())


if __name__ == "__main__":
    unittest.main()

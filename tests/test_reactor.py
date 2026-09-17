import json
import tempfile
import unittest
from pathlib import Path

from org.metadatacenter import reactor
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType


class InstallCommandsTest(unittest.TestCase):
    """npm ci refuses a manifest that disagrees with the lock, which a rewrite always does."""

    def test_every_command_shape_the_frontends_use(self):
        self.assertEqual(
            ["npm install",
             "npm --prefix visual install",
             "npm install --legacy-peer-deps",
             "npm --prefix visual run bundle",
             "npm run dist",
             'bash "$CEDAR_HOME/ops/build-native-split-frontend.sh" designer'],
            reactor.install_commands([
                "npm ci",
                "npm --prefix visual ci",
                "npm ci --legacy-peer-deps",
                "npm --prefix visual run bundle",
                "npm run dist",
                'bash "$CEDAR_HOME/ops/build-native-split-frontend.sh" designer']))

    def test_a_script_named_ci_is_not_the_subcommand(self):
        self.assertEqual(["npm run ci"], reactor.install_commands(["npm run ci"]))


class PublishTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "CEDAR"
        self.build = Path(self.temp.name) / "build" / "cedar-embeddable-designer"
        (self.build / "dist-npm" / "cedar-embeddable-designer").mkdir(parents=True)

    def _staged(self, name="@org.metadatacenter/cedar-embeddable-designer"):
        staged = self.build / "dist-npm" / "cedar-embeddable-designer"
        (staged / "package.json").write_text(json.dumps({"name": name, "version": "0.1.0"}))
        (staged / "cedar-embeddable-designer.js").write_text("// bundle")
        return staged

    def _repo(self, path="dist-npm/cedar-embeddable-designer"):
        return Repo("cedar-embeddable-designer", RepoType.ANGULAR, [], published_package_path=path)

    def test_the_published_package_is_stored_under_its_unscoped_name(self):
        self._staged()

        self.assertEqual("cedar-embeddable-designer",
                         reactor.publish(self._repo(), self.build, self.home))

        stored = self.home / reactor.STORE / "cedar-embeddable-designer"
        self.assertEqual("// bundle", (stored / "cedar-embeddable-designer.js").read_text())

    def test_storing_again_replaces_what_was_there(self):
        self._staged()
        reactor.publish(self._repo(), self.build, self.home)
        (self.build / "dist-npm" / "cedar-embeddable-designer" / "stale.js").unlink(missing_ok=True)
        (self.build / "dist-npm" / "cedar-embeddable-designer"
         / "cedar-embeddable-designer.js").write_text("// rebuilt")

        reactor.publish(self._repo(), self.build, self.home)

        stored = self.home / reactor.STORE / "cedar-embeddable-designer"
        self.assertEqual("// rebuilt", (stored / "cedar-embeddable-designer.js").read_text())

    def test_a_repository_that_publishes_nothing_stores_nothing(self):
        self.assertIsNone(reactor.publish(self._repo(path=None), self.build, self.home))
        self.assertFalse((self.home / reactor.STORE).exists())

    def test_a_declared_path_with_no_manifest_stores_nothing(self):
        """A build that did not stage its package leaves the store as it was."""
        self.assertIsNone(reactor.publish(self._repo(), self.build, self.home))


class ResolveTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "CEDAR"
        self.build = Path(self.temp.name) / "build" / "cedar-embeddable-designer"
        self.build.mkdir(parents=True)

    def _store(self, *names):
        for name in names:
            stored = self.home / reactor.STORE / name
            stored.mkdir(parents=True)
            (stored / "package.json").write_text(json.dumps({"name": name, "version": "0.1.0"}))

    def _manifest(self, payload, *parts):
        path = self.build.joinpath(*parts) if parts else self.build / "package.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        return path

    def test_an_empty_store_rewrites_nothing(self):
        """The property that lets this be the default: no store, and the pins are what build."""
        manifest = self._manifest({"dependencies": {"cedar-design-tokens": "0.1.0"}})

        self.assertEqual([], reactor.resolve(self.build, self.home))
        self.assertEqual("0.1.0",
                         json.loads(manifest.read_text())["dependencies"]["cedar-design-tokens"])

    def test_a_dependency_the_store_holds_points_at_the_store(self):
        self._store("cedar-design-tokens")
        manifest = self._manifest({"devDependencies": {
            "@org.metadatacenter/cedar-design-tokens": "0.1.0-dev.20260916.964cc2a9"}})

        notes = reactor.resolve(self.build, self.home)

        written = json.loads(manifest.read_text())["devDependencies"][
            "@org.metadatacenter/cedar-design-tokens"]
        self.assertTrue(written.startswith("file:"), written)
        self.assertTrue(written.endswith("cedar-design-tokens"), written)
        self.assertEqual(1, len(notes))

    def test_the_alias_form_is_matched_by_the_package_it_reaches(self):
        self._store("cedar-model-typescript-library")
        manifest = self._manifest({"dependencies": {"cedar-model-typescript-library":
            "npm:@org.metadatacenter/cedar-model-typescript-library@1.0.12"}})

        reactor.resolve(self.build, self.home)

        written = json.loads(manifest.read_text())["dependencies"][
            "cedar-model-typescript-library"]
        self.assertTrue(written.startswith("file:"), written)

    def test_a_dependency_the_store_does_not_hold_is_left_alone(self):
        self._store("cedar-design-tokens")
        manifest = self._manifest({"dependencies": {"rxjs": "7.8.1",
                                                    "cedar-embeddable-editor": "2.0.15"}})

        reactor.resolve(self.build, self.home)

        dependencies = json.loads(manifest.read_text())["dependencies"]
        self.assertEqual("7.8.1", dependencies["rxjs"])
        self.assertEqual("2.0.15", dependencies["cedar-embeddable-editor"])

    def test_a_repository_never_resolves_itself_from_the_store(self):
        self._store("cedar-embeddable-designer")
        manifest = self._manifest({"name": "cedar-embeddable-designer",
                                   "dependencies": {"cedar-embeddable-designer": "0.1.0"}})

        self.assertEqual([], reactor.resolve(self.build, self.home))
        self.assertEqual("0.1.0", json.loads(manifest.read_text())
                         ["dependencies"]["cedar-embeddable-designer"])

    def test_a_nested_manifest_is_rewritten_too(self):
        """CEE carries one under visual/, and each multi-repository one per sub-project."""
        self._store("cedar-model-typescript-library")
        nested = self._manifest({"dependencies": {"cedar-model-typescript-library": "1.0.12"}},
                                "visual", "package.json")

        reactor.resolve(self.build, self.home)

        self.assertTrue(json.loads(nested.read_text())["dependencies"][
            "cedar-model-typescript-library"].startswith("file:"))

    def test_manifests_under_build_output_and_dependencies_are_ignored(self):
        self._store("cedar-design-tokens")
        for parts in (("node_modules", "something", "package.json"),
                      ("dist", "package.json"),
                      ("dist-npm", "cedar-x", "package.json")):
            manifest = self._manifest({"dependencies": {"cedar-design-tokens": "0.1.0"}}, *parts)
            reactor.resolve(self.build, self.home)
            self.assertEqual("0.1.0", json.loads(manifest.read_text())
                             ["dependencies"]["cedar-design-tokens"], str(parts))


if __name__ == "__main__":
    unittest.main()

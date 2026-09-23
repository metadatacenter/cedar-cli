import contextlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from org.metadatacenter import reactor
from org.metadatacenter.npm_package import NpmPackageError
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


@unittest.skipUnless(shutil.which("npm"), "requires npm")
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

    def test_storing_again_keeps_old_artifact_and_advances_reference(self):
        staged = self._staged()
        self.assertEqual("cedar-embeddable-designer",
                         reactor.publish(self._repo(), self.build, self.home))
        old = reactor._available(self.home)[self._repo().name]
        (staged / "cedar-embeddable-designer.js").write_text("// rebuilt")
        reactor.publish(self._repo(), self.build, self.home)
        new = reactor._available(self.home)[self._repo().name]
        self.assertNotEqual(old, new)
        with tarfile.open(old) as archive:
            self.assertEqual(b"// bundle", archive.extractfile("package/cedar-embeddable-designer.js").read())
        with tarfile.open(new) as archive:
            self.assertEqual(b"// rebuilt", archive.extractfile("package/cedar-embeddable-designer.js").read())

    def test_a_repository_that_publishes_nothing_stores_nothing(self):
        self.assertIsNone(reactor.publish(self._repo(path=None), self.build, self.home))
        self.assertFalse((self.home / reactor.STORE).exists())

    def test_declared_package_requires_valid_output(self):
        staged = self.build / self._repo().published_package_path
        for contents in (None, "bad json", "[]", '{}', '{"name":"wrong","version":"1.0.0"}'):
            with self.subTest(contents=contents):
                if contents is not None:
                    (staged / "package.json").write_text(contents)
                with self.assertRaises(reactor.ReactorError):
                    reactor.publish(self._repo(), self.build, self.home)

    def test_failed_pack_and_failed_reference_write_are_errors(self):
        self._staged()
        reactor.publish(self._repo(), self.build, self.home)
        previous = reactor._available(self.home)
        with patch.object(reactor, "pack_and_inspect", side_effect=NpmPackageError("pack failed")):
            with self.assertRaisesRegex(reactor.ReactorError, "pack failed"):
                reactor.publish(self._repo(), self.build, self.home)
        for operation in ("link", "replace"):
            with self.subTest(operation=operation), patch.object(reactor.os, operation, side_effect=OSError("disk full")):
                with self.assertRaisesRegex(reactor.ReactorError, "disk full"):
                    reactor.publish(self._repo(), self.build, self.home)
        self.assertEqual(previous, reactor._available(self.home))

    def test_tarball_install_never_runs_prepare_or_follows_future_publications(self):
        staged = self._staged()
        manifest = staged / "package.json"
        package = json.loads(manifest.read_text())
        package.update({"main": "cedar-embeddable-designer.js", "files": ["cedar-embeddable-designer.js"],
                        "scripts": {"prepare": "node -e \"process.exit(71)\""}})
        manifest.write_text(json.dumps(package))
        (staged / "cedar-embeddable-designer.js").write_text('module.exports = "first";')
        (staged / "unpublished.txt").write_text("not in the npm package")
        reactor.publish(self._repo(), self.build, self.home)
        consumer = self.home / "consumer"
        consumer.mkdir()
        consumer_manifest = consumer / "package.json"
        def reset_consumer():
            consumer_manifest.write_text(json.dumps({"name": "consumer", "version": "1.0.0", "dependencies": {
                "cedar-embeddable-designer": "npm:@org.metadatacenter/cedar-embeddable-designer@0.1.0"}}))
        reset_consumer()
        with reactor.session(self.home):
            reactor.resolve(consumer, self.home)
            selected = consumer_manifest.read_text()
            # Nested session represents another invocation, publishing before the first
            # consumer even installs. Both installation and later resolution must stay pinned.
            with reactor.session(self.home):
                (staged / "cedar-embeddable-designer.js").write_text('module.exports = "second";')
                reactor.publish(self._repo(), self.build, self.home)
            reset_consumer()
            reactor.resolve(consumer, self.home)
            self.assertEqual(selected, consumer_manifest.read_text())
            result = subprocess.run(["npm", "install", "--offline", "--no-audit", "--no-fund",
                                     "--cache", str(self.home / "cache")], cwd=consumer,
                                    capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            installed = consumer / "node_modules/cedar-embeddable-designer"
            self.assertFalse(installed.is_symlink())
            self.assertFalse((installed / "unpublished.txt").exists())
            result = subprocess.check_output(["node", "-p", "require('cedar-embeddable-designer')"],
                                             cwd=consumer, text=True)
            self.assertEqual("first", result.strip())
        reset_consumer()
        reactor.resolve(consumer, self.home)
        self.assertNotEqual(selected, consumer_manifest.read_text())

    def test_pending_or_failed_producer_cannot_fall_back_to_old_package(self):
        self._staged()
        reactor.publish(self._repo(), self.build, self.home)
        consumer = self.home / "consumer"
        consumer.mkdir()
        (consumer / "package.json").write_text(json.dumps({"dependencies": {
            "cedar-embeddable-designer": "0.1.0"}}))
        with reactor.session(self.home, [self._repo().name]):
            with patch.object(reactor, "pack_and_inspect", side_effect=NpmPackageError("pack failed")):
                with self.assertRaises(reactor.ReactorError):
                    reactor.publish(self._repo(), self.build, self.home)
            with self.assertRaisesRegex(reactor.ReactorError, "has not succeeded"):
                reactor.resolve(consumer, self.home)
            reactor.publish(self._repo(), self.build, self.home)
            self.assertEqual(1, len(reactor.resolve(consumer, self.home)))


class PlanSessionTest(unittest.TestCase):
    def test_only_scheduled_isolated_producers_must_be_built(self):
        producer = Repo("cedar-producer", RepoType.TYPESCRIPT, [], published_package_path="dist")
        server = Repo("cedar-server", RepoType.ANGULAR_JS, [], published_package_path="dist")
        plan = SimpleNamespace(tasks=[
            SimpleNamespace(repo=producer, tasks=[], parameters={"isolated_frontend_build": True}),
            SimpleNamespace(repo=server, tasks=[], parameters={"in_place_frontend_build": True}),
        ])
        with tempfile.TemporaryDirectory() as home:
            with reactor.session_for_plan(home, plan):
                self.assertEqual({"cedar-producer"}, reactor._state(home)[1])
            self.assertIsNone(reactor._session.get())

    def test_other_plans_do_not_read_the_reactor_store(self):
        plan = SimpleNamespace(tasks=[SimpleNamespace(tasks=[], parameters={"in_place_frontend_build": True})])
        with patch.object(reactor, "_available", side_effect=AssertionError("must not read store")):
            with reactor.session_for_plan("/unused", plan):
                self.assertIsNone(reactor._session.get())


class ExecutorFailureTest(unittest.TestCase):
    def test_publication_failure_makes_shell_task_fail(self):
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        task = SimpleNamespace(node_id=1, repo=SimpleNamespace(name="cedar-example", repo_type="TYPESCRIPT"),
                               command_list=["npm run build"], parameters={"isolated_frontend_build": True},
                               get_parameter=lambda key: key == "isolated_frontend_build")
        executor = ShellTaskExecutor()
        module = "org.metadatacenter.taskexecutor.ShellTaskExecutor"
        with patch(module + ".require_build_node"), patch(module + ".Util.get_wd", return_value="/tmp"), \
             patch(module + ".isolated_frontend_workspace", return_value=contextlib.nullcontext((Path('/tmp'), {}, []))), \
             patch.object(reactor, "resolve", return_value=[]), \
             patch.object(reactor, "publish", side_effect=reactor.ReactorError("disk full")), \
             patch.object(executor, "_execute_commands", return_value=0):
            self.assertEqual(1, executor.execute_shell_command_list(task, Mock(), False))


class ResolveTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "CEDAR"
        self.build = Path(self.temp.name) / "build" / "cedar-embeddable-designer"
        self.build.mkdir(parents=True)

    def _store(self, *names):
        for name in names:
            import hashlib
            digest = hashlib.sha256(name.encode()).hexdigest()
            stored = self.home / reactor.STORE
            (stored / "artifacts").mkdir(parents=True, exist_ok=True)
            (stored / "refs").mkdir(exist_ok=True)
            (stored / "artifacts" / f"{digest}.tgz").write_bytes(b"fixture")
            (stored / "refs" / f"{name}.json").write_text(json.dumps({"sha256": digest}))

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
        self.assertTrue(written.endswith(".tgz"), written)
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

class RuntimeSelectionTest(unittest.TestCase):
    def test_only_a_finished_selection_can_be_activated(self):
        with tempfile.TemporaryDirectory() as home:
            with reactor.session(home, ['cedar-embeddable-editor']):
                with self.assertRaises(reactor.ReactorError):
                    reactor.runtime_selection(home)
            selection = {'cedar-embeddable-editor': 'a' * 64}
            reactor.activate_runtime(home, selection)
            self.assertEqual({'packages': selection}, json.loads((Path(home) / '.reactor/runtime.json').read_text()))

    def test_selection_stays_frozen_when_another_build_updates_refs(self):
        with tempfile.TemporaryDirectory() as home:
            with reactor.session(home):
                root = Path(home) / '.reactor'
                (root / 'refs').mkdir(parents=True)
                (root / 'artifacts').mkdir()
                (root / 'artifacts' / ('a' * 64 + '.tgz')).write_bytes(b'another build')
                (root / 'refs/cedar-embeddable-editor.json').write_text(json.dumps({'sha256': 'a' * 64}))
                self.assertEqual({}, reactor.runtime_selection(home))

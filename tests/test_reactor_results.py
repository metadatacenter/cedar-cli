"""A passing reactor result is reused only for exactly the inputs it passed with."""
import contextlib
import hashlib
import io
import os
import subprocess
import tarfile
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from org.metadatacenter import reactor, reactor_results
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType

TOOLING = {"cli": "c" * 64, "development": "d" * 64, "node": "v24.22.0", "npm": "11.0.0",
           "platform": "macOS", "machine": "arm64"}


def producer():
    return Repo("cedar-embeddable-editor", RepoType.ANGULAR, [],
                published_package_path="dist-npm/cedar-embeddable-editor")


def application():
    return Repo("cedar-workspace", RepoType.ANGULAR, [])


class FingerprintTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "copy"
        self.root.mkdir()
        self.write({"dependencies": {"cedar-embeddable-editor": "file:/h/.reactor/artifacts/" + "a" * 64 + ".tgz",
                                     "@angular/core": "22.2.0"}})
        tooling = patch.object(reactor_results, "tooling", return_value=TOOLING)
        tooling.start()
        self.addCleanup(tooling.stop)

    def write(self, package, *parts):
        path = self.root.joinpath(*parts, "package.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(package))

    def key(self, source="s" * 64, commands=("npm ci", "npm test"), checks=()):
        return reactor_results.fingerprint(application(), self.root, {"sourceSha256": source},
                                           list(commands), None, list(checks), "/h")[0]

    def test_the_same_inputs_give_the_same_key(self):
        self.assertEqual(self.key(), self.key())

    def test_source_commands_resolved_packages_and_integration_bundles_each_change_it(self):
        baseline = self.key()
        self.assertNotEqual(baseline, self.key(source="t" * 64))
        self.assertNotEqual(baseline, self.key(commands=("npm ci", "npm run test:ci")))
        self.assertNotEqual(baseline, self.key(checks=[{"variable": "CEF_BUNDLE", "package": "cee",
                                                         "sha256": "b" * 64, "bundleSha256": "e" * 64}]))
        self.write({"dependencies": {"cedar-embeddable-editor": "file:/h/.reactor/artifacts/" + "f" * 64 + ".tgz"}})
        self.assertNotEqual(baseline, self.key())

    def test_tooling_changes_it(self):
        baseline = self.key()
        with patch.object(reactor_results, "tooling", return_value={**TOOLING, "node": "v24.20.0"}):
            self.assertNotEqual(baseline, self.key())

    def test_environment_changes_invalidate_but_scheduling_does_not(self):
        with patch.object(reactor_results, 'invocation_environment', return_value={'NODE_OPTIONS': '--a', 'CEDAR_TEST_WORKERS': '2'}):
            baseline = self.key()
        with patch.object(reactor_results, 'invocation_environment', return_value={'NODE_OPTIONS': '--a', 'CEDAR_TEST_WORKERS': '12'}):
            self.assertEqual(baseline, self.key())
        with patch.object(reactor_results, 'invocation_environment', return_value={'NODE_OPTIONS': '--b', 'CEDAR_TEST_WORKERS': '12'}):
            self.assertNotEqual(baseline, self.key())

    def test_launch_directory_and_interactive_node_metadata_do_not_invalidate(self):
        with patch.object(reactor_results, 'invocation_environment', return_value={}):
            baseline = self.key()
        with patch.object(reactor_results, 'invocation_environment', return_value={
                'CEDAR_CLI_CALLER_DIR': '/another/checkout', 'NODE_REPL_HISTORY': '/tmp/history',
                'NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S': 'interactive-tool-metadata'}):
            self.assertEqual(baseline, self.key())

    def test_nested_manifests_count_and_installed_ones_do_not(self):
        baseline = self.key()
        self.write({"devDependencies": {"cedar-design-tokens": "file:/h/.reactor/artifacts/" + "9" * 64 + ".tgz"}},
                   "node_modules", "x")
        self.assertEqual(baseline, self.key())
        self.write({"devDependencies": {"cedar-design-tokens": "file:/h/.reactor/artifacts/" + "9" * 64 + ".tgz"}},
                   "visual")
        self.assertNotEqual(baseline, self.key())

    def test_java_dependencies_change_the_key_without_a_frontend_change(self):
        with patch.object(reactor_results, "java_conformance_inputs", return_value={"classpath": ["old"]}):
            baseline = self.key()
        with patch.object(reactor_results, "java_conformance_inputs", return_value={"classpath": ["new"]}):
            self.assertNotEqual(baseline, self.key())

    def test_an_incomplete_identity_reuses_nothing(self):
        with self.assertRaises(reactor_results.ResultError):
            reactor_results.fingerprint(application(), self.root, {}, [], None, [], "/h")


class JavaConformanceInputsTest(unittest.TestCase):
    def test_exact_classpath_bytes_and_java_runtime_are_fingerprinted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "model.tgz"
            pom = b"<project>oracle dependencies</project>"
            with tarfile.open(package, "w:gz") as archive:
                member = tarfile.TarInfo("package/conformance/pom.xml")
                member.size = len(pom)
                archive.addfile(member, io.BytesIO(pom))
            (root / "package.json").write_text(json.dumps({"cedarJavaConformance": True,
                "dependencies": {"cedar-model-typescript-library": "file:" + str(package)}}))
            jars = [root / "validator.jar", root / "reader.jar", root / "transitive.jar"]
            for jar in jars:
                jar.write_bytes(jar.name.encode())
            java = 'openjdk version "17.0.1"'

            def run(command, **_kwargs):
                if command[-1] == "-version":
                    return subprocess.CompletedProcess(command, 0, "", java)
                self.assertIn("maven-dependency-plugin:3.8.1:build-classpath", " ".join(command))
                Path(command[-1].split("=", 1)[1]).write_text(os.pathsep.join(map(str, jars)))
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(reactor_results.subprocess, "run", side_effect=run):
                baseline = reactor_results.java_conformance_inputs(root)
                self.assertEqual(baseline["pom"], hashlib.sha256(pom).hexdigest())
                for jar in jars:
                    previous = reactor_results.java_conformance_inputs(root)
                    jar.write_bytes(b"updated " + jar.read_bytes())
                    self.assertNotEqual(previous, reactor_results.java_conformance_inputs(root))
                previous = reactor_results.java_conformance_inputs(root)
                java = 'openjdk version "17.0.2"'
                self.assertNotEqual(previous, reactor_results.java_conformance_inputs(root))
                jars[0].unlink()
                with self.assertRaises(OSError):
                    reactor_results.java_conformance_inputs(root)

    def test_undeclared_surfaces_need_no_java_and_unresolved_authoring_inputs_cannot_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text("{}")
            self.assertIsNone(reactor_results.java_conformance_inputs(root))
            (root / "package.json").write_text('{"cedarJavaConformance":true}')
            with self.assertRaises(reactor_results.ResultError):
                reactor_results.java_conformance_inputs(root)


class ToolingTest(unittest.TestCase):
    """The reactor's own part of every key, computed from real checkouts rather than a stand-in.

    Every other test here patches tooling() away, so without this one the key could stop covering
    cedar-cli or the cedar-development frontend inputs, and a full reactor would then certify checks
    the current tooling never ran.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        from org.metadatacenter.build import FRONTEND_DEVELOPMENT_INPUTS
        self.inputs = []
        for entry in FRONTEND_DEVELOPMENT_INPUTS:
            # A directory entry such as `bin` stands for the files under it.
            self.inputs.append(entry + "/profile.sh" if "." not in Path(entry).name else entry)
        self.cli = self.repository("cedar-cli", ["org/metadatacenter/build.py"])
        self.development = self.repository("cedar-development", self.inputs + ["ops/audit.py"])
        versions = patch.object(reactor_results, "_version", side_effect=lambda command: command[0] + " 1")
        versions.start()
        self.addCleanup(versions.stop)

    def repository(self, name, files):
        root = self.home / name
        for relative in files:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "-c", "user.email=t@example.org", "-c", "user.name=Test", "commit", "-q", "-m", "initial")
        return root

    @staticmethod
    def git(root, *arguments):
        import subprocess
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)

    def tooling(self):
        return reactor_results.tooling(self.home)

    def test_an_edit_to_cedar_cli_changes_it(self):
        baseline = self.tooling()
        (self.cli / "org/metadatacenter/build.py").write_text("edited")
        self.assertNotEqual(baseline["cli"], self.tooling()["cli"])

    def test_a_new_file_in_cedar_cli_changes_it(self):
        baseline = self.tooling()
        (self.cli / "org/metadatacenter/new_rule.py").write_text("new")
        self.assertNotEqual(baseline["cli"], self.tooling()["cli"])

    def test_cli_tests_docs_and_later_smoke_do_not_invalidate_component_results(self):
        baseline = self.tooling()
        for relative in ['tests/test_backend.py', 'README.md', 'docs/commands.md',
                         'org/metadatacenter/smoke_gate.py', 'org/metadatacenter/search_harness.py']:
            path = self.cli / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('changed')
            self.assertEqual(baseline, self.tooling(), relative)

    def test_runtime_scripts_and_dependencies_remain_inputs(self):
        for relative in ['scripts/javascript/new-rule.mjs', 'requirements.txt', 'CedarCliSettings.py']:
            baseline = self.tooling()
            path = self.cli / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('changed')
            self.assertNotEqual(baseline, self.tooling(), relative)

    def test_an_edit_to_every_frontend_input_changes_it(self):
        for relative in self.inputs:
            with self.subTest(relative):
                baseline = self.tooling()["development"]
                (self.development / relative).write_text("edited " + relative)
                self.assertNotEqual(baseline, self.tooling()["development"])

    def test_a_backend_edit_in_cedar_development_leaves_it(self):
        baseline = self.tooling()
        (self.development / "ops/audit.py").write_text("edited")
        self.assertEqual(baseline, self.tooling())

    def test_browser_image_bytes_are_inputs_even_when_its_tag_did_not_change(self):
        with patch.object(reactor_results, 'browser_images', return_value={'tag': 'first'}):
            before = self.tooling()
        with patch.object(reactor_results, 'browser_images', return_value={'tag': 'replacement'}):
            self.assertNotEqual(before, self.tooling())


class RecordTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        artifacts = self.home / reactor.STORE / "artifacts"
        artifacts.mkdir(parents=True)
        self.content = b"a packed package"
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.artifact = artifacts / f"{self.digest}.tgz"
        self.artifact.write_bytes(self.content)

    def save(self, repo, package, key="k" * 64):
        reactor_results.save(self.home, key, {"repository": repo.name}, package,
                             {"resolvedManifests": {"package-lock.json": {"lockfileVersion": 3}},
                              "dependencies": []})
        return key

    def test_a_saved_result_is_found_for_the_same_key_and_repository(self):
        key = self.save(producer(), self.digest)
        self.assertEqual(self.digest, reactor_results.lookup(self.home, key, producer())["package"])
        key = self.save(application(), None, key="m" * 64)
        self.assertIsNone(reactor_results.lookup(self.home, key, application())["package"])

    def test_a_result_for_another_repository_or_key_is_not_found(self):
        key = self.save(producer(), self.digest)
        self.assertIsNone(reactor_results.lookup(self.home, key, application()))
        self.assertIsNone(reactor_results.lookup(self.home, "n" * 64, producer()))
        renamed = reactor_results.results_root(self.home) / ("o" * 64 + ".json")
        (reactor_results.results_root(self.home) / f"{key}.json").rename(renamed)
        self.assertIsNone(reactor_results.lookup(self.home, "o" * 64, producer()))

    def test_a_package_that_is_gone_or_changed_is_not_a_result(self):
        key = self.save(producer(), self.digest)
        self.artifact.write_bytes(b"something else")
        self.assertIsNone(reactor_results.lookup(self.home, key, producer()))
        self.artifact.unlink()
        self.assertIsNone(reactor_results.lookup(self.home, key, producer()))

    def test_another_schema_is_not_a_result(self):
        key = self.save(producer(), self.digest)
        path = reactor_results.results_root(self.home) / f"{key}.json"
        record = json.loads(path.read_text())
        path.write_text(json.dumps({**record, "schemaVersion": reactor_results.SCHEMA + 1}))
        self.assertIsNone(reactor_results.lookup(self.home, key, producer()))

    def test_missing_or_malformed_pass_time_is_not_a_result(self):
        key = self.save(producer(), self.digest)
        path = reactor_results.results_root(self.home) / f'{key}.json'
        record = json.loads(path.read_text())
        for value in [None, 42, 'not a date']:
            path.write_text(json.dumps({**record, 'passedAt': value}))
            self.assertIsNone(reactor_results.lookup(self.home, key, producer()))


class ReuseTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        artifacts = self.home / reactor.STORE / "artifacts"
        artifacts.mkdir(parents=True)
        self.digest = hashlib.sha256(b"package").hexdigest()
        (artifacts / f"{self.digest}.tgz").write_bytes(b"package")

    def ref(self, name):
        return self.home / reactor.STORE / "refs" / f"{name}.json"

    def test_a_reused_package_joins_the_build_and_is_advertised(self):
        with reactor.session(self.home, [producer().name]):
            self.assertEqual(producer().name, reactor.reuse(producer(), self.home / "copy", self.home, self.digest))
            self.assertEqual(self.digest, reactor.selected_digest(producer(), self.home))
            self.assertIn(producer().name, reactor.runtime_selection(self.home))
        self.assertEqual({"sha256": self.digest}, json.loads(self.ref(producer().name).read_text()))

    def test_a_reused_package_whose_bytes_changed_is_refused(self):
        (self.home / reactor.STORE / "artifacts" / ("0" * 64 + ".tgz")).write_bytes(b"not that package")
        with reactor.session(self.home, [producer().name]):
            with self.assertRaisesRegex(reactor.ReactorError, "no longer has its digest"):
                reactor.reuse(producer(), self.home / "copy", self.home, "0" * 64)

    def test_an_application_has_no_package_to_reuse(self):
        self.assertIsNone(reactor.reuse(application(), self.home / "copy", self.home, self.digest))


class ExecutorReuseTest(unittest.TestCase):
    MODULE = "org.metadatacenter.taskexecutor.ShellTaskExecutor"

    def run_task(self, found, code=0, reuse=True):
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        parameters = {"isolated_frontend_build": True, "reuse_passing_result": reuse}
        repo = producer()
        task = SimpleNamespace(node_id=1, repo=repo, command_list=["npm ci", "npm run test:ci"],
                               parameters=parameters, get_parameter=parameters.get)
        executor = ShellTaskExecutor()
        m = self.MODULE
        with patch(m + ".require_build_node"), patch(m + ".Util.get_wd", return_value="/tmp"), \
                patch(m + ".Util.cedar_home", "/tmp/cedar-reuse-fixture"), \
                patch(m + ".isolated_frontend_workspace",
                      return_value=contextlib.nullcontext((Path("/tmp"), {}, []))), \
                patch(m + ".materialize_frontend_workspace") as materialize, \
                patch(m + ".reactor_tasks.configure", return_value=None), \
                patch(m + ".reactor_evidence.begin", return_value={"sourceSha256": "s" * 64}), \
                patch(m + ".reactor_evidence.record") as record, \
                patch.object(reactor, "resolve", return_value=[]), \
                patch.object(reactor, "prepare_checks", return_value=[]), \
                patch.object(reactor, "publish", return_value=repo.name) as publish, \
                patch.object(reactor, "reuse") as reused, \
                patch.object(reactor, "selected_digest", return_value="p" * 64), \
                patch.object(reactor_results, "fingerprint", return_value=("k" * 64, {"repository": repo.name})) as key, \
                patch.object(reactor_results, "lookup", return_value=found), \
                patch.object(reactor_results, "save") as save, \
                patch.object(executor, "_execute_packaged_commands", return_value=code) as commands:
            result = executor.execute_shell_command_list(task, Mock(), False)
        return SimpleNamespace(code=result, record=record, publish=publish, reused=reused,
                               key=key, save=save, commands=commands, materialize=materialize)

    def test_a_matching_result_stands_for_running_the_commands(self):
        run = self.run_task({"fingerprint": "k" * 64, "package": "p" * 64, "passedAt": "2026-10-14T00:00:00Z"})
        self.assertEqual(0, run.code)
        run.commands.assert_not_called()
        run.materialize.assert_not_called()
        run.publish.assert_not_called()
        run.save.assert_not_called()
        self.assertEqual("p" * 64, run.reused.call_args.args[3])
        self.assertEqual("k" * 64, run.record.call_args.kwargs["reused"]["fingerprint"])

    def test_without_one_the_commands_run_and_their_result_is_recorded(self):
        run = self.run_task(None)
        self.assertEqual(0, run.code)
        run.commands.assert_called_once()
        run.materialize.assert_called_once()
        run.publish.assert_called_once()
        run.save.assert_called_once()
        self.assertEqual("p" * 64, run.save.call_args.args[3])

    def test_a_failure_records_nothing(self):
        run = self.run_task(None, code=1)
        self.assertEqual(1, run.code)
        run.save.assert_not_called()

    def test_with_reuse_off_nothing_is_fingerprinted(self):
        run = self.run_task(None, reuse=False)
        run.key.assert_not_called()
        run.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()

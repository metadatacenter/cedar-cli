import json
import subprocess
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter.util.ComponentFreshness import (
    ComponentState,
    Surface,
    dependency_pins,
    evaluate_bundle,
    evaluate_element,
    evaluate_pin,
    pinned_commit,
    referenced_elements,
    release_tag,
)
from org.metadatacenter.util.ComponentFreshness import ComponentFinding
from org.metadatacenter.util.Util import Util
from org.metadatacenter.release_support import preflight as release_preflight_module
from org.metadatacenter.release_support.preflight import ReleasePreflight
from org.metadatacenter.train_support import preflight as train_preflight
from org.metadatacenter.worker.ComponentWorker import (
    COMPONENT_REMEDY, ComponentGateError, ComponentWorker,
)


def _release_preflight(environment):
    """A preflight carrying only what check_components reads."""
    return ReleasePreflight({}, environment=environment)


class DependencyPinsTest(unittest.TestCase):

    def test_alias_form_names_the_package_npm_installs_rather_than_the_local_key(self):
        package = {"dependencies": {
            "cedar-model-typescript-library":
                "npm:@org.metadatacenter/cedar-model-typescript-library@1.0.13-dev.20260915.757d636",
            "cedar-embeddable-editor": "2.0.15",
        }}

        pins = dependency_pins(package)

        self.assertEqual("1.0.13-dev.20260915.757d636",
                         pins["@org.metadatacenter/cedar-model-typescript-library"])
        self.assertEqual("2.0.15", pins["cedar-embeddable-editor"])

    def test_development_dependencies_count_and_foreign_packages_do_not(self):
        package = {
            "dependencies": {"rxjs": "7.8.1"},
            "devDependencies": {"@org.metadatacenter/cedar-design-tokens": "0.1.0-dev.20260915.4e0a0032"},
        }

        self.assertEqual({"@org.metadatacenter/cedar-design-tokens": "0.1.0-dev.20260915.4e0a0032"},
                         dependency_pins(package))


class VersionIdentityTest(unittest.TestCase):

    def test_development_version_names_the_commit_it_was_built_from(self):
        self.assertEqual("5652527d", pinned_commit("2.0.16-dev.20260915.5652527d"))

    def test_a_release_version_names_no_commit_but_does_name_a_tag(self):
        self.assertIsNone(pinned_commit("2.0.15"))
        self.assertEqual("release-2.0.15", release_tag("2.0.15"))

    def test_a_range_names_neither(self):
        self.assertIsNone(pinned_commit("^2.0.15"))
        self.assertIsNone(release_tag("^2.0.15"))


class ReferencedElementsTest(unittest.TestCase):

    def test_reads_the_element_names_a_host_creates(self):
        source = ("const elementName = route.kind === 'field' "
                  "? 'cedar-embeddable-field-designer' : 'cedar-embeddable-designer';")

        self.assertEqual({"cedar-embeddable-field-designer", "cedar-embeddable-designer"},
                         referenced_elements(source))


class EvaluatePinTest(unittest.TestCase):

    def test_a_pin_on_the_develop_head_is_current(self):
        finding = evaluate_pin("host", "component", "1.0.0-dev.20260915.abc1234",
                               "abc1234", "abc1234", (), True)

        self.assertEqual(ComponentState.CURRENT, finding.state)
        self.assertFalse(finding.is_strict_failure)

    def test_a_pin_behind_develop_reports_the_commits_the_host_cannot_see(self):
        finding = evaluate_pin("host", "component", "1.0.0-dev.20260915.abc1234",
                               "abc1234", "def5678", ("Add the field designer",), True)

        self.assertEqual(ComponentState.BEHIND, finding.state)
        self.assertEqual(("Add the field designer",), finding.unseen)

    def test_being_behind_fails_only_under_strict_so_an_ordinary_cycle_stays_green(self):
        finding = evaluate_pin("host", "component", "1.0.0-dev.20260915.abc1234",
                               "abc1234", "def5678", ("Add the field designer",), True)

        self.assertFalse(finding.is_failure)
        self.assertTrue(finding.is_strict_failure)

    def test_a_pin_the_component_history_does_not_hold_fails_outright(self):
        finding = evaluate_pin("host", "component", "1.0.0-dev.20260915.abc1234",
                               "abc1234", "def5678", (), False)

        self.assertEqual(ComponentState.DIVERGED, finding.state)
        self.assertTrue(finding.is_failure)

    def test_a_version_carrying_no_source_identity_is_unresolved(self):
        finding = evaluate_pin("host", "component", "^1.0.0", None, "def5678", (), True)

        self.assertEqual(ComponentState.UNRESOLVED, finding.state)
        self.assertFalse(finding.is_failure)


class EvaluateBundleTest(unittest.TestCase):

    def test_bytes_matching_the_locked_package_are_current(self):
        finding = evaluate_bundle("host", "component", "package", "aa", "1.0.0", "aa")

        self.assertEqual(ComponentState.CURRENT, finding.state)

    def test_bytes_that_are_not_the_locked_package_fail_outright(self):
        finding = evaluate_bundle("host", "component", "package", "aa", "1.0.0", "bb")

        self.assertEqual(ComponentState.MISMATCHED, finding.state)
        self.assertTrue(finding.is_failure)

    def test_a_local_override_is_reported_without_failing_an_ordinary_run(self):
        finding = evaluate_bundle("host", "component", "local-override", "aa", "1.0.0", "bb")

        self.assertEqual(ComponentState.OVERRIDDEN, finding.state)
        self.assertFalse(finding.is_failure)
        self.assertTrue(finding.is_strict_failure)


class EvaluateElementTest(unittest.TestCase):

    def test_an_element_no_locked_bundle_defines_fails_outright(self):
        finding = evaluate_element("host", "cedar-embeddable-field-designer", None)

        self.assertEqual(ComponentState.UNDEFINED, finding.state)
        self.assertEqual(Surface.ELEMENT, finding.surface)
        self.assertTrue(finding.is_failure)

    def test_an_element_a_locked_bundle_defines_is_current(self):
        finding = evaluate_element("host", "cedar-embeddable-designer", "cedar-embeddable-designer")

        self.assertEqual(ComponentState.CURRENT, finding.state)


class ComponentWorkerTest(unittest.TestCase):
    """The worker against a workspace built to hold the case the check was written for."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def _repo(self, name, package, files=None):
        directory = self.root / name
        directory.mkdir(parents=True)
        (directory / "package.json").write_text(json.dumps(package))
        for path, content in (files or {}).items():
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self._git(directory, "init", "--initial-branch", "develop")
        self._git(directory, "config", "user.email", "test@test.com")
        self._git(directory, "config", "user.name", "test")
        self._git(directory, "add", "-A")
        self._git(directory, "commit", "-m", "Publish the component")
        return directory

    def _git(self, directory, *arguments):
        subprocess.run(["git", "-C", str(directory)] + list(arguments),
                       capture_output=True, check=True)

    def _install(self, host, name, version, bundle):
        """Put a locked package under the host, as npm ci would leave it."""
        package_dir = host / "node_modules" / name
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(json.dumps({"name": name, "version": version}))
        (package_dir / f"{name}.js").write_text(bundle)
        return sha256(bundle.encode()).hexdigest()

    def test_a_host_creating_an_element_its_locked_bundle_lacks_fails(self):
        """The 2026-09-16 case: the element landed in the component, the pin never moved."""
        component = self._repo("cedar-embeddable-designer",
                               {"name": "cedar-embeddable-designer",
                                "version": "0.1.0-dev.20260916.aaaaaaa"})
        published = self._git_head(component)
        self._git(component, "commit", "--allow-empty", "-m", "Add embeddable field designer")

        host = self._repo("cedar-template-designer", {
            "name": "cedar-template-designer",
            "dependencies": {"cedar-embeddable-designer":
                             f"npm:@org.metadatacenter/cedar-embeddable-designer@0.1.0-dev.20260916.{published}"},
        }, files={"app/scripts/host.mjs":
                  "document.createElement('cedar-embeddable-field-designer');"})
        locked = self._install(host, "cedar-embeddable-designer",
                               f"0.1.0-dev.20260916.{published}",
                               "customElements.define('cedar-embeddable-designer', Designer);")
        (host / "app/components").mkdir(parents=True)
        (host / "app/components/manifest.json").write_text(json.dumps(
            {"cedar-embeddable-designer": {"source": "package", "sha256": locked}}))

        with patch.object(Util, "cedar_home", str(self.root)):
            with patch("org.metadatacenter.worker.ComponentWorker.console"):
                returncode = ComponentWorker.check_components()
                findings = ComponentWorker._evaluate_host(
                    "cedar-template-designer", host, ComponentWorker._component_index())

        self.assertEqual(1, returncode)
        undefined = [f for f in findings if f.state == ComponentState.UNDEFINED]
        self.assertEqual(["cedar-embeddable-field-designer"], [f.component for f in undefined])
        behind = [f for f in findings if f.surface == Surface.PIN and f.state == ComponentState.BEHIND]
        self.assertEqual(["Add embeddable field designer"], list(behind[0].unseen))

    def _train_configuration(self, configuration):
        ops = self.root / "cedar-development" / "ops"
        ops.mkdir(parents=True, exist_ok=True)
        (ops / "frontend-train.json").write_text(json.dumps(configuration), encoding="utf-8")

    def test_a_host_pinning_a_component_no_inventory_names_is_a_failure(self):
        """Publishing advances the pins an inventory names, so an omitted host rots in silence.

        It never reports behind either, because the component it pins does move on without it.
        """
        self._repo("cedar-model-typescript-library",
                   {"name": "@org.metadatacenter/cedar-model-typescript-library",
                    "version": "1.0.13-dev.20260915.7576363"})
        self._repo("cedar-model-typescript-library-demo", {
            "name": "cedar-model-typescript-library-demo",
            "dependencies": {"cedar-model-typescript-library": "1.0.2"},
        })
        self._train_configuration({
            "components": [{
                "id": "model",
                "repository": "cedar-model-typescript-library",
                "publishedName": "@org.metadatacenter/cedar-model-typescript-library",
                "consumers": [],
            }],
        })

        with patch.object(Util, "cedar_home", str(self.root)):
            findings = ComponentWorker.findings()

        undeclared = [f for f in findings if f.state == ComponentState.UNDECLARED]
        self.assertEqual(
            [("cedar-model-typescript-library-demo", "cedar-model-typescript-library")],
            [(f.host, f.component) for f in undeclared])
        self.assertTrue(undeclared[0].is_failure)

    def test_a_declared_consumer_and_the_reference_host_are_not_undeclared(self):
        self._repo("cedar-model-typescript-library",
                   {"name": "@org.metadatacenter/cedar-model-typescript-library",
                    "version": "1.0.13-dev.20260915.7576363"})
        self._repo("cedar-model-typescript-library-demo", {
            "name": "cedar-model-typescript-library-demo",
            "dependencies": {"cedar-model-typescript-library": "1.0.2"},
        })
        self._repo("cedar-embeddable-editor", {
            "name": "cedar-embeddable-editor",
            "dependencies": {"cedar-model-typescript-library": "1.0.12"},
        })
        self._train_configuration({
            "components": [{
                "id": "model",
                "repository": "cedar-model-typescript-library",
                "publishedName": "@org.metadatacenter/cedar-model-typescript-library",
                "reference": {"repository": "cedar-embeddable-editor"},
                "consumers": [{"repository": "cedar-model-typescript-library-demo"}],
            }],
        })

        with patch.object(Util, "cedar_home", str(self.root)):
            findings = ComponentWorker.findings()

        self.assertEqual([], [f for f in findings if f.state == ComponentState.UNDECLARED])

    def test_an_unreadable_inventory_reports_nothing_rather_than_everything(self):
        self._repo("cedar-model-typescript-library",
                   {"name": "@org.metadatacenter/cedar-model-typescript-library",
                    "version": "1.0.13-dev.20260915.7576363"})
        self._repo("cedar-model-typescript-library-demo", {
            "name": "cedar-model-typescript-library-demo",
            "dependencies": {"cedar-model-typescript-library": "1.0.2"},
        })

        with patch.object(Util, "cedar_home", str(self.root)):
            findings = ComponentWorker.findings()

        self.assertEqual([], [f for f in findings if f.state == ComponentState.UNDECLARED])

    def test_a_component_is_not_asked_about_the_elements_it_defines_itself(self):
        self._repo("cedar-embeddable-designer",
                   {"name": "cedar-embeddable-designer", "version": "0.1.0-dev.20260916.aaaaaaa"},
                   files={"src/custom-element.ts":
                          "customElements.define('cedar-embeddable-field-designer', Field);"})

        with patch.object(Util, "cedar_home", str(self.root)):
            with patch("org.metadatacenter.worker.ComponentWorker.console"):
                findings = ComponentWorker._evaluate_host(
                    "cedar-embeddable-designer", self.root / "cedar-embeddable-designer",
                    ComponentWorker._component_index())

        self.assertEqual([], [f for f in findings if f.surface == Surface.ELEMENT])

    def _git_head(self, directory):
        completed = subprocess.run(["git", "-C", str(directory), "rev-parse", "--short=8", "develop"],
                                   capture_output=True, text=True, check=True)
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()


class ComponentGateTest(unittest.TestCase):
    """The release and train preflights, against the verdicts the check produces."""

    FAILURE = ComponentFinding(
        "cedar-template-designer", "cedar-embeddable-field-designer",
        Surface.ELEMENT, ComponentState.UNDEFINED,
        "no locked component bundle defines this element")
    INFORMATIONAL = ComponentFinding(
        "cedar-embeddable-editor", "cedar-design-tokens",
        Surface.PIN, ComponentState.BEHIND,
        "0.1.0-dev.20260915.4e0a0032 predates 5 commits on develop")

    def test_the_release_gate_is_part_of_the_complete_gate_and_of_every_build_stage(self):
        from org.metadatacenter.release_support.preflight import ReleasePreflight
        self.assertIn("check_components", ReleasePreflight.CHECKS)
        source = Path(release_preflight_module.__file__).read_text()
        # The stages that build frontends are the ones where the question still applies.
        self.assertIn('"check_develop_is_green", "check_smoke_gate", "check_components",', source)

    def test_the_release_gate_refuses_a_failure_and_ignores_what_is_only_behind(self):
        preflight = _release_preflight({"CEDAR_HOME": "/workspace"})
        with patch.object(ComponentWorker, "findings",
                          return_value=[self.FAILURE, self.INFORMATIONAL]):
            findings = preflight.check_components()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("cedar-embeddable-field-designer", findings[0].message)
        self.assertEqual(COMPONENT_REMEDY, findings[0].remedy)

    def test_the_release_gate_asks_about_the_workspace_it_is_judging(self):
        preflight = _release_preflight({"CEDAR_HOME": "/workspace"})
        with patch.object(ComponentWorker, "findings", return_value=[]) as findings:
            self.assertEqual([], preflight.check_components())

        findings.assert_called_once_with("/workspace")

    def test_the_release_gate_reports_a_comparison_it_could_not_make(self):
        preflight = _release_preflight({"CEDAR_HOME": "/workspace"})
        with patch.object(ComponentWorker, "findings",
                          side_effect=ComponentGateError("CEDAR_HOME does not name a workspace")):
            findings = preflight.check_components()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)

    def test_the_train_gate_refuses_a_failure_and_ignores_what_is_only_behind(self):
        with patch.object(ComponentWorker, "findings",
                          return_value=[self.FAILURE, self.INFORMATIONAL]):
            with self.assertRaises(ValueError) as refused:
                train_preflight._component_preflight()

        message = str(refused.exception)
        self.assertIn("cedar-embeddable-field-designer", message)
        self.assertNotIn("cedar-design-tokens", message)
        self.assertIn(COMPONENT_REMEDY, message)

    def test_the_train_gate_passes_when_nothing_failed(self):
        with patch.object(ComponentWorker, "findings", return_value=[self.INFORMATIONAL]):
            self.assertIsNone(train_preflight._component_preflight())

    def test_the_train_gate_is_asked_on_every_dispatch(self):
        source = Path(train_preflight.__file__).read_text()
        self.assertIn("settle(_component_preflight)", source)

    def test_an_unresolvable_workspace_is_a_gate_error_rather_than_a_crash(self):
        with patch.object(Util, "cedar_home", None):
            with self.assertRaises(ComponentGateError):
                ComponentWorker.findings()

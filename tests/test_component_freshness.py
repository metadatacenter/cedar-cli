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
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.ComponentWorker import ComponentWorker


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

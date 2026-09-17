import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter import component_pins
from org.metadatacenter.component_pins import (
    ComponentPinError,
    _apply,
    _repoint,
    _require_clean,
    _stamp_lock,
    _version_of,
    declared,
    next_version,
    plan,
)

CONFIG = {
    "components": [
        {
            "id": "ced",
            "repository": "cedar-embeddable-designer",
            "publishedName": "@org.metadatacenter/cedar-embeddable-designer",
            "sourceManifest": "package.json",
            "sourceLock": "package-lock.json",
            "stagedPackage": "dist-npm/cedar-embeddable-designer",
            "distCommand": ["npm", "run", "dist"],
            "consumers": [{
                "repository": "cedar-template-designer",
                "dependency": "cedar-embeddable-designer",
                "manifest": "package.json",
                "lock": "package-lock.json",
                "restageCommand": ["npm", "run", "prepare:components"],
            }],
        }
    ]
}


class NextVersionTest(unittest.TestCase):

    def test_stamps_the_day_and_the_head_onto_the_base_the_component_carries(self):
        self.assertEqual("0.1.0-dev.20260917.cddaa0ce",
                         next_version("0.1.0-dev.20260916.2593d382", "cddaa0ce", "20260917"))

    def test_a_base_version_with_no_suffix_gains_one(self):
        self.assertEqual("2.0.16-dev.20260917.abcd1234",
                         next_version("2.0.16", "abcd1234", "20260917"))

    def test_a_version_already_naming_this_head_is_left_alone(self):
        """A component whose develop has not moved is not republished under a new day's stamp."""
        published = "0.1.0-dev.20260916.2593d382"

        self.assertEqual(published, next_version(published, "2593d382", "20260917"))

    def test_a_version_this_cannot_advance_is_refused(self):
        with self.assertRaises(ComponentPinError):
            next_version("^0.1.0", "abcd1234", "20260917")


class DeclaredDependencyTest(unittest.TestCase):

    def test_the_alias_form_yields_the_version_it_reaches(self):
        value = "npm:@org.metadatacenter/cedar-embeddable-designer@0.1.0-dev.20260916.2593d382"

        self.assertEqual("0.1.0-dev.20260916.2593d382",
                         _version_of(value, "@org.metadatacenter/cedar-embeddable-designer"))

    def test_a_plain_version_is_its_own_answer(self):
        self.assertEqual("2.0.15", _version_of("2.0.15", "cedar-embeddable-editor"))

    def test_an_undeclared_dependency_has_no_version(self):
        self.assertIsNone(_version_of(None, "anything"))


class RepointTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "package.json"

    def test_the_alias_form_is_kept(self):
        self.path.write_text(json.dumps({"dependencies": {
            "cedar-embeddable-designer":
                "npm:@org.metadatacenter/cedar-embeddable-designer@0.1.0-dev.20260916.2593d382"}}))

        _repoint(self.path, "cedar-embeddable-designer",
                 "@org.metadatacenter/cedar-embeddable-designer", "0.1.0-dev.20260917.cddaa0ce")

        written = json.loads(self.path.read_text())["dependencies"]["cedar-embeddable-designer"]
        self.assertEqual(
            "npm:@org.metadatacenter/cedar-embeddable-designer@0.1.0-dev.20260917.cddaa0ce",
            written)

    def test_a_plain_version_stays_plain(self):
        self.path.write_text(json.dumps({"dependencies": {"cedar-embeddable-editor": "2.0.15"}}))

        _repoint(self.path, "cedar-embeddable-editor", "cedar-embeddable-editor", "2.0.16")

        self.assertEqual("2.0.16",
                         json.loads(self.path.read_text())["dependencies"]["cedar-embeddable-editor"])

    def test_a_development_dependency_is_found_too(self):
        self.path.write_text(json.dumps({"devDependencies": {"@org.metadatacenter/x": "1.0.0"}}))

        _repoint(self.path, "@org.metadatacenter/x", "@org.metadatacenter/x", "1.0.1")

        self.assertEqual("1.0.1",
                         json.loads(self.path.read_text())["devDependencies"]["@org.metadatacenter/x"])

    def test_a_manifest_that_does_not_declare_it_is_refused(self):
        self.path.write_text(json.dumps({"dependencies": {}}))

        with self.assertRaises(ComponentPinError):
            _repoint(self.path, "missing", "missing", "1.0.0")


class StampLockTest(unittest.TestCase):

    def test_both_places_a_lockfile_carries_its_own_version_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package-lock.json"
            path.write_text(json.dumps({"version": "0.1.0", "packages": {"": {"version": "0.1.0"}}}))

            _stamp_lock(path, "0.1.0-dev.20260917.cddaa0ce")

            lock = json.loads(path.read_text())
            self.assertEqual("0.1.0-dev.20260917.cddaa0ce", lock["version"])
            self.assertEqual("0.1.0-dev.20260917.cddaa0ce", lock["packages"][""]["version"])

    def test_an_absent_lockfile_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            _stamp_lock(Path(directory) / "package-lock.json", "1.0.0")


class WorkspaceTest(unittest.TestCase):
    """Planning and applying against a workspace shaped like the real one."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = self.root / "cedar-development" / "ops"
        config.mkdir(parents=True)
        (config / "frontend-train.json").write_text(json.dumps(CONFIG))

        self.component = self._repo("cedar-embeddable-designer", {
            "name": "cedar-embeddable-designer", "version": "0.1.0-dev.20260916.aaaaaaaa"})
        self.head = self._commit(self.component, "Add the field designer")
        self.host = self._repo("cedar-template-designer", {
            "name": "cedar-template-designer",
            "dependencies": {"cedar-embeddable-designer":
                             "npm:@org.metadatacenter/cedar-embeddable-designer@0.1.0-dev.20260916.aaaaaaaa"}})

    def _repo(self, name, package):
        directory = self.root / name
        directory.mkdir(parents=True)
        (directory / "package.json").write_text(json.dumps(package))
        for command in (["init", "--initial-branch", "develop"],
                        ["config", "user.email", "t@t.com"], ["config", "user.name", "t"],
                        ["add", "-A"], ["commit", "-m", "Publish"]):
            subprocess.run(["git", "-C", str(directory)] + command, capture_output=True, check=True)
        return directory

    def _commit(self, directory, message):
        subprocess.run(["git", "-C", str(directory), "commit", "--allow-empty", "-m", message],
                       capture_output=True, check=True)
        return subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--short=8", "develop"],
            capture_output=True, text=True, check=True).stdout.strip()

    def test_the_plan_names_the_component_and_the_pin_that_follows_it(self):
        plans = plan(str(self.root))

        self.assertEqual(1, len(plans))
        item = plans[0]
        self.assertTrue(item.publishes)
        self.assertTrue(item.target.endswith(f".{self.head}"))
        self.assertEqual("0.1.0-dev.20260916.aaaaaaaa", item.consumers[0].current)
        self.assertEqual(item.target, item.consumers[0].target)
        self.assertTrue(item.consumers[0].moves)

    def test_selecting_one_component_by_id(self):
        self.assertEqual(1, len(declared(str(self.root), only="ced")))
        with self.assertRaises(ComponentPinError):
            declared(str(self.root), only="nope")

    def test_applying_stamps_publishes_repoints_and_restages_in_that_order(self):
        commands = []
        plans = plan(str(self.root))

        with patch.object(component_pins, "console"):
            _apply(str(self.root), plans, run=lambda d, c: commands.append((d.name, c)))

        target = plans[0].target
        self.assertEqual([
            ("cedar-embeddable-designer", ["npm", "run", "dist"]),
            ("cedar-embeddable-designer",
             ["npm", "publish", "./dist-npm/cedar-embeddable-designer", "--tag=dev"]),
            ("cedar-template-designer", ["npm", "install"]),
            ("cedar-template-designer", ["npm", "run", "prepare:components"]),
        ], commands)
        self.assertEqual(target, json.loads((self.component / "package.json").read_text())["version"])
        self.assertEqual(
            f"npm:@org.metadatacenter/cedar-embeddable-designer@{target}",
            json.loads((self.host / "package.json").read_text())
            ["dependencies"]["cedar-embeddable-designer"])

    def test_a_repository_with_uncommitted_tracked_changes_is_refused(self):
        """The diffs this leaves must be its own, so a review sees nothing else."""
        (self.host / "package.json").write_text(json.dumps({"name": "edited"}))

        with self.assertRaises(ComponentPinError) as refused:
            _require_clean(str(self.root), plan(str(self.root)))

        self.assertIn("cedar-template-designer", str(refused.exception))

    def test_a_workspace_with_no_declared_components_is_refused(self):
        (self.root / "cedar-development" / "ops" / "frontend-train.json").write_text("{}")

        with self.assertRaises(ComponentPinError):
            declared(str(self.root))

    def test_no_workspace_at_all_is_refused(self):
        with self.assertRaises(ComponentPinError):
            declared(None)


if __name__ == "__main__":
    unittest.main()


class PublishTargetTest(unittest.TestCase):
    """A component whose package is its checkout root, such as the design tokens."""

    def test_a_staged_subdirectory_is_published_from_there(self):
        self.assertEqual("./dist-npm/cedar-embeddable-designer",
                         component_pins._publish_target("dist-npm/cedar-embeddable-designer"))

    def test_a_root_published_component_is_published_in_place(self):
        for declared in (".", "", "./", " . "):
            self.assertEqual(".", component_pins._publish_target(declared))

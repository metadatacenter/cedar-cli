"""The release's proof that a public CED or CETP package is the one its train tested."""
import json
import tempfile
import unittest
from pathlib import Path

from org.metadatacenter.release_support.acceptance import ReleaseAcceptance
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.packages import (
    _model_runtime_fingerprints,
    compare_component_packages,
)
from org.metadatacenter.release_support.policy import PUBLIC_COMPONENTS
from org.metadatacenter.release_support.state import ReleaseState
from tests.test_release_integration_packages import model_tarball
from tests.test_release_train import (
    COMPONENT_CHANGELOG,
    COMPONENT_DEV_VERSION,
    COMPONENT_PUBLIC_VERSION,
    COMPONENT_RELEASED_CHANGELOG,
    component_bundle,
    component_tarball,
)

CED = next(component for component in PUBLIC_COMPONENTS if component.id == "ced")
CETP = next(component for component in PUBLIC_COMPONENTS if component.id == "cetp")
DEV_MODEL = "1.0.28-dev.20261108.g99988bcea5c1.t2032"
PUBLIC_MODEL = "1.0.28"


def runtimes(version, code=b"export const model = 1;"):
    raw, normalized = _model_runtime_fingerprints(model_tarball(version, code), version)
    return {
        "@angular/core": {"version": "22.2.4"},
        "cedar-model-typescript-library": {
            "version": version, "sha256": raw, "runtimeSha256": normalized,
        },
    }


class ComponentPromotionTest(unittest.TestCase):
    def pair(self, component=CED, *, dev_bundle=None, public_bundle=None, dev_kwargs=None,
             public_kwargs=None):
        reports = component is CED
        dev = component_tarball(
            component, COMPONENT_DEV_VERSION, development=True,
            bundle=dev_bundle or component_bundle(COMPONENT_DEV_VERSION if reports else None),
            provenance="train build", **(dev_kwargs or {}))
        public_kwargs = {"changelog": COMPONENT_RELEASED_CHANGELOG, **(public_kwargs or {})}
        public = component_tarball(
            component, COMPONENT_PUBLIC_VERSION, development=False,
            bundle=public_bundle or component_bundle(COMPONENT_PUBLIC_VERSION if reports else None),
            provenance="public build", **public_kwargs)
        return dev, public

    def compare(self, component, dev, public, models=None):
        return compare_component_packages(
            component, dev, COMPONENT_DEV_VERSION, public, COMPONENT_PUBLIC_VERSION,
            model_runtime_loader=(models or {}).__getitem__)

    def test_identical_bundles_promote_with_only_their_build_fingerprints_set_aside(self):
        proof = self.compare(CETP, *self.pair(CETP))

        bundle = proof["provenBundles"]["cedar-embeddable-term-picker.js"]
        self.assertEqual(bundle["bundleSha256"], bundle["publicBundleSha256"])
        self.assertEqual([
            "package.json:name",
            "package.json:version",
            "package.json:publishConfig",
            "CHANGELOG.md:0.1.0 release entry",
            "bundle-manifest.json:build input and output fingerprints",
        ], proof["allowedMetadataChanges"])
        self.assertEqual(0, proof["minifiedIdentifierRenames"])

    def test_the_one_reported_version_is_the_only_bundle_difference_allowed(self):
        # The bundle names 10.1.0 and 0.1.0-rc.1, so the public version occurs in it more than
        # once; only the quoted literal the train's bundle reports may change.
        dev, public = self.pair(CED, dev_kwargs={
            "host_fonts_bundle": component_bundle(COMPONENT_DEV_VERSION, "return b+a")},
            public_kwargs={
            "host_fonts_bundle": component_bundle(COMPONENT_PUBLIC_VERSION, "return b+a")})
        proof = self.compare(CED, dev, public)

        self.assertIn("cedar-embeddable-designer.js:CED version", proof["allowedMetadataChanges"])
        self.assertIn("cedar-embeddable-designer.host-fonts.js:CED version",
                      proof["allowedMetadataChanges"])
        self.assertIn("bundle-manifest.json:derived bundle bytes and sha256",
                      proof["allowedMetadataChanges"])
        for proven in proof["provenBundles"].values():
            self.assertEqual(proven["publicBundleSha256"], proven["normalizedBundleSha256"])

    def test_a_changed_executable_is_refused(self):
        dev, public = self.pair(
            CED, public_bundle=component_bundle(COMPONENT_PUBLIC_VERSION, "return a-b"))
        with self.assertRaisesRegex(ReleaseError, "CED promotion changes executable JavaScript"):
            self.compare(CED, dev, public)

    def test_a_bundle_reporting_its_version_twice_is_refused(self):
        twice = component_bundle(COMPONENT_DEV_VERSION) + f';var again="{COMPONENT_DEV_VERSION}";'.encode()
        dev, public = self.pair(CED, dev_bundle=twice)
        with self.assertRaisesRegex(ReleaseError, "must report its version once; found 2"):
            self.compare(CED, dev, public)

    def test_a_manifest_naming_other_build_inputs_is_refused(self):
        dev, public = self.pair(CETP, public_kwargs={"inputs": ("dist/browser/other.js",)})
        with self.assertRaisesRegex(ReleaseError, "outside allowed channel metadata: "
                                                  "bundle-manifest.json"):
            self.compare(CETP, dev, public)

    def test_a_changelog_changed_outside_the_release_entry_is_refused(self):
        dev, public = self.pair(CETP, public_kwargs={
            "changelog": COMPONENT_RELEASED_CHANGELOG.replace(b"- Pending.", b"- Moved.")})
        with self.assertRaisesRegex(ReleaseError, "CHANGELOG.md outside the one current-release"):
            self.compare(CETP, dev, public)

    def test_a_train_that_already_carries_the_release_entry_is_a_promotion(self):
        dev, public = self.pair(CETP, dev_kwargs={"changelog": COMPONENT_RELEASED_CHANGELOG})
        proof = self.compare(CETP, dev, public)
        self.assertNotIn("CHANGELOG.md:0.1.0 release entry", proof["allowedMetadataChanges"])

    def test_a_public_package_with_a_nexus_publish_config_is_refused(self):
        dev, _ = self.pair(CETP)
        public = component_tarball(
            CETP, COMPONENT_PUBLIC_VERSION, development=True, bundle=component_bundle(),
            changelog=COMPONENT_RELEASED_CHANGELOG)
        with self.assertRaisesRegex(ReleaseError, "identifies @org.metadatacenter/"
                                                  "cedar-embeddable-term-picker@0.1.0"):
            self.compare(CETP, dev, public)

    def test_a_file_only_one_package_ships_is_refused(self):
        dev, public = self.pair(CETP, public_kwargs={"extra_files": {"extra.js": b"1"}})
        with self.assertRaisesRegex(ReleaseError, "file set: development-only=\\[\\], "
                                                  "public-only=\\['extra.js'\\]"):
            self.compare(CETP, dev, public)

    def test_the_integration_model_runtime_must_be_the_one_the_train_built_against(self):
        module = b'export function integrate(){return 1}'
        models = {DEV_MODEL: model_tarball(DEV_MODEL), PUBLIC_MODEL: model_tarball(PUBLIC_MODEL)}
        dev, public = self.pair(
            CETP, dev_kwargs={"integration": (module, runtimes(DEV_MODEL))},
            public_kwargs={"integration": (module, runtimes(PUBLIC_MODEL))})
        proof = self.compare(CETP, dev, public, models)
        self.assertIn("integration-manifest.json:source and verified model package provenance",
                      proof["allowedMetadataChanges"])

        models[PUBLIC_MODEL] = model_tarball(PUBLIC_MODEL, b"export const model = 2;")
        dev, public = self.pair(
            CETP, dev_kwargs={"integration": (module, runtimes(DEV_MODEL))},
            public_kwargs={"integration": (
                module, runtimes(PUBLIC_MODEL, b"export const model = 2;"))})
        with self.assertRaisesRegex(ReleaseError, "CETP promotion changes integration runtime"):
            self.compare(CETP, dev, public, models)

    def test_the_integration_module_may_differ_only_in_its_reported_version(self):
        models = {DEV_MODEL: model_tarball(DEV_MODEL), PUBLIC_MODEL: model_tarball(PUBLIC_MODEL)}
        dev, public = self.pair(
            CED, dev_kwargs={"integration": (component_bundle(COMPONENT_DEV_VERSION),
                                             runtimes(DEV_MODEL))},
            public_kwargs={"integration": (component_bundle(COMPONENT_PUBLIC_VERSION),
                                           runtimes(PUBLIC_MODEL))})
        proof = self.compare(CED, dev, public, models)
        self.assertIn("integration.mjs:CED version", proof["allowedMetadataChanges"])
        self.assertIn("integration-manifest.json:derived bundle bytes and sha256",
                      proof["allowedMetadataChanges"])

    def test_a_public_changelog_without_its_release_entry_is_refused(self):
        dev, public = self.pair(CETP, public_kwargs={"changelog": COMPONENT_CHANGELOG})
        with self.assertRaisesRegex(ReleaseError, "exactly one dated CHANGELOG.md entry for 0.1.0"):
            self.compare(CETP, dev, public)


class ComponentPinAcceptanceTest(unittest.TestCase):
    PUBLIC = {
        "name": "cedar-embeddable-designer", "version": "0.1.0",
        "tarball": "https://registry.npmjs.org/cedar-embeddable-designer/-/"
                   "cedar-embeddable-designer-0.1.0.tgz",
        "integrity": "sha512-public", "registry": "https://registry.npmjs.org/",
    }

    def acceptance_checks(self, installed, integrated=("cedar-template-designer",)):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            lock = workspace / "cedar-template-designer" / "package-lock.json"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"packages": {
                "node_modules/cedar-embeddable-designer": installed}}))
            manifest = {
                "versionPreparation": {"release": {"workspace": str(workspace)}},
                "remoteIntegration": {"completedTasks": {
                    repository: {"repository": repository} for repository in integrated}},
                "publicComponents": {"ced": {"public": {**self.PUBLIC, "gitHead": "a" * 40}}},
                "componentWiring": [{
                    "repository": "cedar-template-designer", "manifest": "package.json",
                    "lock": "package-lock.json", "dependency": "cedar-embeddable-designer",
                    "package": self.PUBLIC,
                }],
            }
            acceptance = ReleaseAcceptance(
                ReleaseState(root=Path(directory) / "state"),
                remote_integrator=object(), publisher=object(), environment={})
            return acceptance._consumers_pin_the_proven_components(manifest)

    def test_a_released_consumer_installing_the_proven_package_is_accepted(self):
        [check] = self.acceptance_checks({
            "version": "0.1.0", "resolved": self.PUBLIC["tarball"],
            "integrity": self.PUBLIC["integrity"]})
        self.assertEqual("ced-pin", check["check"])
        self.assertIn("cedar-embeddable-designer 0.1.0", check["detail"])

    def test_a_released_consumer_installing_other_bytes_is_refused(self):
        with self.assertRaisesRegex(ReleaseError, "does not install the proven public "
                                                  "cedar-embeddable-designer@0.1.0"):
            self.acceptance_checks({
                "version": "0.1.0", "resolved": self.PUBLIC["tarball"],
                "integrity": "sha512-other"})

    def test_a_consumer_that_was_never_integrated_is_refused(self):
        with self.assertRaisesRegex(ReleaseError, "was never integrated"):
            self.acceptance_checks({}, integrated=())


if __name__ == "__main__":
    unittest.main()

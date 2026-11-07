import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from org.metadatacenter.release_support.acceptance import ReleaseAcceptance
from org.metadatacenter.release_support.distribution import ReleaseDistributionMaterializer
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.state import ReleaseState


class OpenViewRuntimeDistributionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "cedar-openview"
        self.bundle = "cedar-embeddable-editor.host-fonts.js"
        self.package_path = "node_modules/cedar-embeddable-editor"
        self.asset = {
            "glob": self.bundle, "input": self.package_path + "/",
            "output": self.package_path,
        }
        self.angular = self.root / "cedar-openview-src/angular.json"
        self.angular.parent.mkdir(parents=True)
        self.write_assets([self.asset])
        self.public = b"const endpoint='https://terminology.metadatacenter.orgx/';"
        self.served_bytes = self.public.replace(b".orgx/", b".org/")
        self.source = self.root / "cedar-openview-src" / self.package_path / self.bundle
        self.served = self.root / "cedar-openview-dist" / self.package_path / self.bundle
        self.source.parent.mkdir(parents=True)
        self.served.parent.mkdir(parents=True)
        self.source.write_bytes(self.public)
        self.served.write_bytes(self.served_bytes)
        # The installed standalone bundle remains present but is not the served asset.
        (self.source.parent / "cedar-embeddable-editor.js").write_bytes(b"standalone fonts")
        self.task = {
            "id": "openview", "repository": "cedar-openview", "directory": "cedar-openview-dist",
            "ceeRuntime": {
                "source": f"cedar-openview-src/{self.package_path}/cedar-embeddable-editor.js",
                "distribution": f"{self.package_path}/cedar-embeddable-editor.js",
                "replacements": [["https://terminology.metadatacenter.orgx/",
                                  "https://terminology.metadatacenter.org/"]],
            },
        }
        self.manifest = {
            "cee": {
                "public": {"version": "2.0.32"}, "consumers": [],
                "promotionProof": {
                    "publicBundleSha256": hashlib.sha256(b"standalone fonts").hexdigest(),
                    "provenBundles": {self.bundle: {
                        "publicBundleSha256": hashlib.sha256(self.public).hexdigest(),
                    }},
                },
            },
            "versionPreparation": {"release": {"workspace": str(self.base)}},
            "publicationPlan": {"npm": {"surfaces": [self.task]}},
        }

    def write_assets(self, assets):
        self.angular.write_text(json.dumps({"projects": {"cedar-openview": {
            "architect": {"build": {"options": {"assets": assets}}},
        }}}))

    def evidence(self):
        return ReleaseDistributionMaterializer._cee_evidence(
            self.manifest, self.root, self.root / "cedar-openview-dist", self.task)

    def test_legacy_plan_resumes_against_captured_host_fonts_asset(self):
        original = copy.deepcopy(self.manifest)
        evidence = self.evidence()
        self.assertEqual(f"{self.package_path}/{self.bundle}", evidence["distribution"])
        self.assertEqual(hashlib.sha256(self.served_bytes).hexdigest(), evidence["servedBundleSha256"])
        self.assertEqual(original, self.manifest)

    def test_new_plan_resolves_declared_asset_manifest(self):
        self.task["ceeRuntime"]["assetManifest"] = "cedar-openview-src/angular.json"
        self.assertEqual(f"{self.package_path}/{self.bundle}", self.evidence()["distribution"])

    def test_host_fonts_requires_its_own_promotion_proof(self):
        self.manifest["cee"]["promotionProof"]["provenBundles"] = {}
        self.manifest["cee"]["promotionProof"]["publicBundleSha256"] = hashlib.sha256(self.public).hexdigest()
        with self.assertRaisesRegex(ReleaseError, "does not match the proven public bundle"):
            self.evidence()

    def test_changed_installed_bundle_is_rejected(self):
        self.source.write_bytes(b"different executable")
        with self.assertRaisesRegex(ReleaseError, "does not match the proven public bundle"):
            self.evidence()

    def test_changed_served_bundle_is_rejected(self):
        self.served.write_bytes(b"different executable")
        with self.assertRaisesRegex(ReleaseError, "does not serve the proven public CEE"):
            self.evidence()

    def test_ambiguous_or_relocated_assets_are_rejected(self):
        for assets in ([self.asset, self.asset], [{**self.asset, "output": "elsewhere"}],
                       [{**self.asset, "glob": "*.js"}], []):
            with self.subTest(assets=assets):
                self.write_assets(assets)
                with self.assertRaisesRegex(ReleaseError, "exactly one supported CEE"):
                    self.evidence()

    def test_legacy_standalone_asset_still_works(self):
        standalone = "cedar-embeddable-editor.js"
        self.write_assets([{**self.asset, "glob": standalone}])
        (self.served.parent / standalone).write_bytes(b"standalone fonts")
        self.assertEqual(f"{self.package_path}/{standalone}", self.evidence()["distribution"])

    def acceptance(self):
        return ReleaseAcceptance(ReleaseState(root=self.base / "state"), environment={})

    def prepare_acceptance(self):
        evidence = self.evidence()
        self.manifest["distributionMaterialization"] = {"completedTasks": {
            "release:npm:openview:distribution": {"ceeRuntime": evidence},
        }}
        self.manifest["artifactPublication"] = {"completedTasks": {
            "npm:release:openview": {"runtimeFiles": {
                evidence["distribution"]: evidence["servedBundleSha256"],
            }},
        }}
        return evidence

    def test_acceptance_verifies_published_host_fonts_path(self):
        self.prepare_acceptance()
        checks = self.acceptance()._consumers_pin_the_proven_cee(self.manifest)
        self.assertEqual("openview-cee-runtime", checks[-1]["check"])

    def test_acceptance_rejects_changed_publication_or_runtime_evidence(self):
        evidence = self.prepare_acceptance()
        record = self.manifest["artifactPublication"]["completedTasks"]["npm:release:openview"]
        record["runtimeFiles"][evidence["distribution"]] = "changed"
        with self.assertRaisesRegex(ReleaseError, "does not contain its proven runtime CEE"):
            self.acceptance()._consumers_pin_the_proven_cee(self.manifest)
        evidence["distribution"] = "unproven.js"
        with self.assertRaisesRegex(ReleaseError, "evidence changed"):
            self.acceptance()._consumers_pin_the_proven_cee(self.manifest)


if __name__ == "__main__":
    unittest.main()

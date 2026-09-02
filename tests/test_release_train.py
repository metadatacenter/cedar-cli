import base64
import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import dataclasses
import inspect
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import typer
from typer.testing import CliRunner

from org.metadatacenter import release_train
from org.metadatacenter.release_train import (
    DEV_CEE_NAME,
    PUBLIC_CEE_NAME,
    ReleaseArtifactPublisher,
    ReleaseError,
    ReleasePlanner,
    ReleasePreflight,
    MAVEN_GENERATED_VERSION_FILES,
    NEXUS_AUTHENTICATED_ENDPOINT,
    ReleaseBuildValidator,
    ReleaseRefCreator,
    ReleaseRemoteIntegrator,
    ReleaseState,
    ReleaseAcceptance,
    RetryableReleaseError,
    accept_active_release,
    abandon_active_release,
    ReleaseVersionPreparer,
    ReleaseWorkspacePreparer,
    advance_active_release,
    compare_cee_packages,
    create_active_release_refs,
    integrate_active_release,
    publish_active_release,
    prepare_active_release,
    validate_active_release_builds,
)


DEV_VERSION = "2.0.3-dev.20260824.1847.g48283fbabcde"
PUBLIC_VERSION = "2.0.3"
TRAIN = "2.9.3-dev.20260826.1554"


def integrity(content):
    return "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode()


def package_tarball(
    name,
    version,
    *,
    development,
    bundle=b"tested CEE bundle",
    changelog=b"Changes\n",
):
    package = {
        "name": name,
        "version": version,
        "description": "CEE",
        "main": "cedar-embeddable-editor.js",
    }
    if development:
        package["publishConfig"] = {
            "registry": "https://nexus.bmir.stanford.edu/repository/npm-cedar/",
            "tag": "dev",
        }
    lock = {
        "name": name,
        "version": version,
        "lockfileVersion": 2,
        "packages": {"": {"name": name, "version": version}},
    }
    files = {
        "package.json": (json.dumps(package, indent=2) + "\n").encode(),
        "package-lock.json": (json.dumps(lock, indent=2) + "\n").encode(),
        "cedar-embeddable-editor.js": bundle,
        "cedar-embeddable-editor.d.ts": b"export interface CeeConfig {}\n",
        "bundle-manifest.json": (json.dumps({
            "bytes": len(bundle),
            "sha256": hashlib.sha256(bundle).hexdigest(),
        }, indent=2) + "\n").encode(),
        "README.md": b"CEE\n",
        "CHANGELOG.md": changelog,
        "license.txt": b"BSD\n",
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path, content in files.items():
            info = tarfile.TarInfo(f"package/{path}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def provenance_bundle(cee_version, model_identity, load_trace, suffix=""):
    return (
        f"code-before|{cee_version}|code-middle|{model_identity}|"
        f"code-after|{load_trace}|{suffix}"
    ).encode()


BASE_CHANGELOG = b"""# Changelog

## [Unreleased]

## [2.0.2] - 2026-08-20

Previous release.
"""


PUBLIC_CHANGELOG = b"""# Changelog

## [Unreleased]

## [2.0.3] - 2026-08-27

CEE embeds `cedar-model-typescript-library@1.0.4`.

## [2.0.2] - 2026-08-20

Previous release.
"""


def manifest_fixture():
    return {
        "schemaVersion": 1,
        "releaseVersion": "2.9.3",
        "nextDevelopmentVersion": "2.9.4-SNAPSHOT",
        "train": TRAIN,
        "sourceVersion": "2.9.3-SNAPSHOT",
        "createdAt": "2026-08-26T00:00:00+00:00",
        "phase": "validated",
        "sourceRepositories": {},
        "cee": {
            "development": {"version": DEV_VERSION},
            "public": {
                "version": PUBLIC_VERSION,
                "integrity": "sha512-public",
                "tarball": (
                    "https://registry.npmjs.org/cedar-embeddable-editor/"
                    f"-/cedar-embeddable-editor-{PUBLIC_VERSION}.tgz"
                ),
            },
            "promotionProof": {"normalizedPayloadSha256": "a" * 64},
            "consumers": [],
        },
    }


class FakeState:
    def __init__(self, values):
        self.values = values

    def read_json(self, path):
        return self.values[path]


class FakeHttp:
    def __init__(
        self,
        contents,
        public_metadata,
        frontend_config,
        build_config,
        cee_source_package=None,
    ):
        self.contents = contents
        self.public_metadata = public_metadata
        self.frontend_config = frontend_config
        self.build_config = build_config
        self.cee_source_package = cee_source_package or {}

    def read(self, url, *, missing_ok=False):
        return self.contents[url]

    def read_json(self, url, *, missing_ok=False):
        if url.endswith("/ops/frontend-train.json"):
            value = self.frontend_config
        elif url.endswith("/ops/build-train.json"):
            value = self.build_config
        elif "/cedar-embeddable-editor/" in url and url.endswith("/package.json"):
            value = self.cee_source_package
        else:
            value = self.public_metadata
        content = json.dumps(value, indent=2, sort_keys=True).encode()
        return value, content


class CeePromotionTest(unittest.TestCase):
    def setUp(self):
        self.dev = package_tarball(
            DEV_CEE_NAME, DEV_VERSION, development=True,
        )
        self.public = package_tarball(
            PUBLIC_CEE_NAME, PUBLIC_VERSION, development=False,
        )

    def test_metadata_only_promotion_has_one_normalized_payload_digest(self):
        proof = compare_cee_packages(
            self.dev, DEV_VERSION, self.public, PUBLIC_VERSION,
        )
        self.assertEqual(8, proof["fileCount"])
        self.assertRegex(proof["normalizedPayloadSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            hashlib.sha256(b"tested CEE bundle").hexdigest(),
            proof["bundleSha256"],
        )

    def test_changed_javascript_is_not_a_promotion(self):
        dev = package_tarball(
            DEV_CEE_NAME,
            DEV_VERSION,
            development=True,
            bundle=provenance_bundle(
                DEV_VERSION,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260827.2030.g9261381c1fb4",
                "2026-08-27 12:23 10212094",
            ),
            changelog=PUBLIC_CHANGELOG,
        )
        changed = package_tarball(
            PUBLIC_CEE_NAME,
            PUBLIC_VERSION,
            development=False,
            bundle=provenance_bundle(
                PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09", suffix="different",
            ),
            changelog=PUBLIC_CHANGELOG,
        )
        with self.assertRaisesRegex(ReleaseError, "outside declared release provenance"):
            compare_cee_packages(dev, DEV_VERSION, changed, PUBLIC_VERSION)

    def test_declared_release_provenance_is_normalized(self):
        dev_bundle = provenance_bundle(
            DEV_VERSION,
            "npm:@org.metadatacenter/cedar-model-typescript-library@"
            "1.0.5-dev.20260827.2030.g9261381c1fb4",
            "2026-08-27 12:23 10212094",
        )
        public_bundle = provenance_bundle(
            PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09",
        )
        dev = package_tarball(
            DEV_CEE_NAME, DEV_VERSION, development=True,
            bundle=dev_bundle, changelog=BASE_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME, PUBLIC_VERSION, development=False,
            bundle=public_bundle, changelog=PUBLIC_CHANGELOG,
        )

        proof = compare_cee_packages(dev, DEV_VERSION, public, PUBLIC_VERSION)

        self.assertNotEqual(proof["bundleSha256"], proof["publicBundleSha256"])
        self.assertRegex(proof["normalizedBundleSha256"], r"^[0-9a-f]{64}$")
        self.assertIn(
            "cedar-embeddable-editor.js:model package identity",
            proof["allowedMetadataChanges"],
        )

    def test_code_change_beside_release_provenance_is_rejected(self):
        dev = package_tarball(
            DEV_CEE_NAME, DEV_VERSION, development=True,
            bundle=provenance_bundle(
                DEV_VERSION,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260827.2030.g9261381c1fb4",
                "2026-08-27 12:23 10212094",
            ),
            changelog=BASE_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME, PUBLIC_VERSION, development=False,
            bundle=provenance_bundle(
                PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09", suffix="changed-code",
            ),
            changelog=PUBLIC_CHANGELOG,
        )
        with self.assertRaisesRegex(ReleaseError, "outside declared release provenance"):
            compare_cee_packages(dev, DEV_VERSION, public, PUBLIC_VERSION)

    def test_captured_allow_scripts_policy_is_normalized_out_of_bundle(self):
        allow_scripts = {
            "@parcel/watcher@2.6.0": True,
            "esbuild@0.28.1": True,
        }
        policy = (
            b",allowScripts:"
            + json.dumps(allow_scripts, separators=(",", ":")).encode()
        )
        dev = package_tarball(
            DEV_CEE_NAME,
            DEV_VERSION,
            development=True,
            bundle=provenance_bundle(
                DEV_VERSION,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260827.2030.g9261381c1fb4",
                "2026-08-27 12:23 10212094",
                suffix=policy.decode(),
            ),
            changelog=PUBLIC_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME,
            PUBLIC_VERSION,
            development=False,
            bundle=provenance_bundle(
                PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09"
            ),
            changelog=PUBLIC_CHANGELOG,
        )

        proof = compare_cee_packages(
            dev,
            DEV_VERSION,
            public,
            PUBLIC_VERSION,
            development_allow_scripts=allow_scripts,
        )

        self.assertIn(
            "cedar-embeddable-editor.js:embedded allowScripts install policy",
            proof["allowedMetadataChanges"],
        )

    def test_allow_scripts_normalization_still_rejects_adjacent_code_change(self):
        allow_scripts = {"esbuild@0.28.1": True}
        policy = (
            b",allowScripts:"
            + json.dumps(allow_scripts, separators=(",", ":")).encode()
        )
        dev = package_tarball(
            DEV_CEE_NAME,
            DEV_VERSION,
            development=True,
            bundle=provenance_bundle(
                DEV_VERSION,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260827.2030.g9261381c1fb4",
                "2026-08-27 12:23 10212094",
                suffix=policy.decode() + "changed-code",
            ),
            changelog=PUBLIC_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME,
            PUBLIC_VERSION,
            development=False,
            bundle=provenance_bundle(
                PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09"
            ),
            changelog=PUBLIC_CHANGELOG,
        )

        with self.assertRaisesRegex(ReleaseError, "outside declared release provenance"):
            compare_cee_packages(
                dev,
                DEV_VERSION,
                public,
                PUBLIC_VERSION,
                development_allow_scripts=allow_scripts,
            )

    def test_change_to_older_changelog_content_is_rejected(self):
        dev = package_tarball(
            DEV_CEE_NAME, DEV_VERSION, development=True,
            bundle=provenance_bundle(
                DEV_VERSION,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260827.2030.g9261381c1fb4",
                "2026-08-27 12:23 10212094",
            ),
            changelog=BASE_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME, PUBLIC_VERSION, development=False,
            bundle=provenance_bundle(PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09"),
            changelog=PUBLIC_CHANGELOG.replace(b"Previous release.", b"Rewritten history."),
        )
        with self.assertRaisesRegex(ReleaseError, "outside the one current-release entry"):
            compare_cee_packages(dev, DEV_VERSION, public, PUBLIC_VERSION)

    def test_duplicate_provenance_literal_is_rejected(self):
        dev = package_tarball(
            DEV_CEE_NAME, DEV_VERSION, development=True,
            bundle=provenance_bundle(
                DEV_VERSION,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260827.2030.g9261381c1fb4",
                "2026-08-27 12:23 10212094",
            ),
            changelog=BASE_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME, PUBLIC_VERSION, development=False,
            bundle=provenance_bundle(
                PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09", suffix="1.0.4",
            ),
            changelog=PUBLIC_CHANGELOG,
        )
        with self.assertRaisesRegex(ReleaseError, "exactly one model package identity"):
            compare_cee_packages(dev, DEV_VERSION, public, PUBLIC_VERSION)

    def test_public_version_need_not_match_the_dev_stable_base(self):
        dev_version = "2.0.4-dev.20260828.0224.g83014569a7fa"
        dev = package_tarball(
            DEV_CEE_NAME,
            dev_version,
            development=True,
            bundle=provenance_bundle(
                dev_version,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260828.0209.g9261381c1fb4",
                "2026-08-28 02:24 83014569a7fa",
            ),
            changelog=BASE_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME,
            PUBLIC_VERSION,
            development=False,
            bundle=provenance_bundle(PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09"),
            changelog=PUBLIC_CHANGELOG,
        )

        proof = compare_cee_packages(dev, dev_version, public, PUBLIC_VERSION)

        self.assertRegex(proof["normalizedPayloadSha256"], r"^[0-9a-f]{64}$")

    def test_identical_existing_release_changelog_does_not_block_proof(self):
        dev_version = "2.0.4-dev.20260828.0224.g83014569a7fa"
        dev = package_tarball(
            DEV_CEE_NAME,
            dev_version,
            development=True,
            bundle=provenance_bundle(
                dev_version,
                "npm:@org.metadatacenter/cedar-model-typescript-library@"
                "1.0.5-dev.20260828.0209.g9261381c1fb4",
                "2026-08-28 02:24 83014569a7fa",
            ),
            changelog=PUBLIC_CHANGELOG,
        )
        public = package_tarball(
            PUBLIC_CEE_NAME,
            PUBLIC_VERSION,
            development=False,
            bundle=provenance_bundle(PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09"),
            changelog=PUBLIC_CHANGELOG,
        )

        proof = compare_cee_packages(dev, dev_version, public, PUBLIC_VERSION)

        self.assertRegex(proof["normalizedPayloadSha256"], r"^[0-9a-f]{64}$")

    def test_unexpected_public_publish_config_is_rejected(self):
        incorrectly_scoped = package_tarball(
            PUBLIC_CEE_NAME, PUBLIC_VERSION, development=True,
        )
        with self.assertRaisesRegex(ReleaseError, "public package must not contain"):
            compare_cee_packages(
                self.dev, DEV_VERSION, incorrectly_scoped, PUBLIC_VERSION,
            )


class ReleasePlannerTest(unittest.TestCase):
    def make_planner(self, allow_scripts=None):
        if allow_scripts:
            policy = (
                b",allowScripts:"
                + json.dumps(allow_scripts, separators=(",", ":")).encode()
            )
            dev = package_tarball(
                DEV_CEE_NAME,
                DEV_VERSION,
                development=True,
                bundle=provenance_bundle(
                    DEV_VERSION,
                    "npm:@org.metadatacenter/cedar-model-typescript-library@"
                    "1.0.5-dev.20260827.2030.g9261381c1fb4",
                    "2026-08-27 12:23 10212094",
                    suffix=policy.decode(),
                ),
                changelog=PUBLIC_CHANGELOG,
            )
            public = package_tarball(
                PUBLIC_CEE_NAME,
                PUBLIC_VERSION,
                development=False,
                bundle=provenance_bundle(
                    PUBLIC_VERSION, "1.0.4", "2026-08-27 15:09"
                ),
                changelog=PUBLIC_CHANGELOG,
            )
        else:
            dev = package_tarball(DEV_CEE_NAME, DEV_VERSION, development=True)
            public = package_tarball(PUBLIC_CEE_NAME, PUBLIC_VERSION, development=False)
        dev_url = "https://nexus.example/cee-dev.tgz"
        public_url = "https://registry.npmjs.org/cee-public.tgz"
        source = {
            "version": TRAIN,
            "sourceVersion": "2.9.3-SNAPSHOT",
            "repositories": {
                "cedar-development": "a" * 40,
                "frontend-main": "1" * 40,
                "frontend-workspace": "2" * 40,
                "frontend-bridging": "3" * 40,
                "frontend-openview": "4" * 40,
                "frontend-demo": "5" * 40,
                "cedar-template-editor": "6" * 40,
                "cedar-openview": "7" * 40,
                "cedar-content-distribution": "8" * 40,
                "cedar-monitoring": "9" * 40,
                "cedar-bridging": "0" * 40,
                "cedar-component-demo": "c" * 40,
            },
        }
        source_content = (json.dumps(source, indent=2, sort_keys=True) + "\n").encode()
        frontend_config = {
            "frontends": [
                {
                    "id": label,
                    "repository": repository,
                    "ceeConsumer": {"manifest": manifest, "lock": lock},
                }
                for label, repository, manifest, lock in (
                    ("main", "frontend-main", "package.json", "package-lock.json"),
                    ("workspace", "frontend-workspace", "package.json", "package-lock.json"),
                    ("bridging", "frontend-bridging", "src/package.json", "src/package-lock.json"),
                    ("openview", "frontend-openview", "src/package.json", "src/package-lock.json"),
                )
            ],
            "additionalCeeConsumers": [
                {
                    "repository": "frontend-demo",
                    "manifest": f"{framework}/package.json",
                    "lock": f"{framework}/package-lock.json",
                }
                for framework in ("angular", "ember", "react")
            ],
        }
        build_config = {
            "repositories": list(source["repositories"]),
            "mavenRepositories": ["frontend-main"],
            "phases": [{"name": "main", "repository": "frontend-main"}],
            "requiredArtifacts": ["frontend-main"],
        }
        npm_plan = {
            "version": TRAIN,
            "sourceManifestSha256": hashlib.sha256(source_content).hexdigest(),
            "cee": {
                "name": DEV_CEE_NAME,
                "version": DEV_VERSION,
                "revision": "b" * 40,
            },
            "frontends": [
                {
                    "repository": repository,
                    "revision": source["repositories"][repository],
                    "ceeVersion": DEV_VERSION,
                }
                for repository in (
                    "frontend-main", "frontend-workspace", "frontend-bridging", "frontend-openview"
                )
            ],
            "additionalCeeConsumers": [
                {
                    "repository": "frontend-demo",
                    "revision": source["repositories"]["frontend-demo"],
                    "manifest": f"{framework}/package.json",
                }
                for framework in ("angular", "ember", "react")
            ],
        }
        npm_plan_content = (
            json.dumps(npm_plan, indent=2, sort_keys=True) + "\n"
        ).encode()
        npm_completion = {
            "version": TRAIN,
            "sourceManifestSha256": hashlib.sha256(source_content).hexdigest(),
            "planSha256": hashlib.sha256(npm_plan_content).hexdigest(),
            "packages": [{
                "name": DEV_CEE_NAME,
                "version": DEV_VERSION,
                "integrity": integrity(dev),
                "tarball": dev_url,
                "tarballSha256": hashlib.sha256(dev).hexdigest(),
            }],
        }
        docker_images = [
            {
                "image": f"cedar-image-{index:02d}",
                "reference": f"nexus.example/cedar-image-{index:02d}:{TRAIN}",
            }
            for index in range(31)
        ]
        docker_plan = {
            "version": TRAIN,
            "sourceManifestSha256": hashlib.sha256(source_content).hexdigest(),
            "npmPlanSha256": hashlib.sha256(npm_plan_content).hexdigest(),
            "images": docker_images,
        }
        docker_plan_content = (
            json.dumps(docker_plan, indent=2, sort_keys=True) + "\n"
        ).encode()
        docker_completion = {
            "version": TRAIN,
            "plan": f"docker/trains/{TRAIN}.json",
            "sourceManifestSha256": hashlib.sha256(source_content).hexdigest(),
            "npmPlanSha256": hashlib.sha256(npm_plan_content).hexdigest(),
            "images": [
                {**item, "digest": "sha256:" + f"{index:064x}"}
                for index, item in enumerate(docker_images, start=1)
            ],
        }
        state = FakeState({
            f"trains/{TRAIN}.json": (source, source_content),
            f"completed/{TRAIN}.json": ({"version": TRAIN}, b"{}\n"),
            f"npm/trains/{TRAIN}.json": (npm_plan, npm_plan_content),
            f"npm/completed/{TRAIN}.json": (npm_completion, b"{}\n"),
            f"docker/trains/{TRAIN}.json": (docker_plan, docker_plan_content),
            f"docker/completed/{TRAIN}.json": (docker_completion, b"{}\n"),
        })
        metadata = {
            "versions": {
                PUBLIC_VERSION: {
                    "gitHead": "c" * 40,
                    "dist": {"tarball": public_url, "integrity": integrity(public)},
                },
            },
        }
        http = FakeHttp(
            {dev_url: dev, public_url: public},
            metadata,
            frontend_config,
            build_config,
            cee_source_package={"allowScripts": allow_scripts} if allow_scripts else {},
        )
        return ReleasePlanner(http=http, state=state)

    def test_plan_binds_explicit_versions_train_state_and_cee_proof(self):
        manifest = self.make_planner().build(
            release_version="2.9.3",
            next_version="2.9.4-SNAPSHOT",
            train=TRAIN,
            cee_version=PUBLIC_VERSION,
        )
        self.assertEqual("2.9.3", manifest["releaseVersion"])
        self.assertEqual(TRAIN, manifest["train"])
        self.assertEqual(DEV_VERSION, manifest["cee"]["development"]["version"])
        self.assertEqual(PUBLIC_VERSION, manifest["cee"]["public"]["version"])
        self.assertEqual(
            "c" * 40,
            manifest["cee"]["public"]["gitHead"],
        )
        self.assertEqual(7, len(manifest["cee"]["consumers"]))
        self.assertEqual(
            "a" * 40,
            manifest["sourceRepositories"]["cedar-development"],
        )
        self.assertIn("cedar-development", manifest["releaseRepositories"])
        self.assertEqual(
            f"docker/completed/{TRAIN}.json",
            manifest["trainState"]["dockerCompletion"],
        )

    def test_plan_binds_embedded_allow_scripts_to_captured_cee_source(self):
        allow_scripts = {
            "@parcel/watcher@2.6.0": True,
            "esbuild@0.28.1": True,
        }

        manifest = self.make_planner(allow_scripts=allow_scripts).build(
            release_version="2.9.3",
            next_version="2.9.4-SNAPSHOT",
            train=TRAIN,
            cee_version=PUBLIC_VERSION,
        )

        self.assertIn(
            "cedar-embeddable-editor.js:embedded allowScripts install policy",
            manifest["cee"]["promotionProof"]["allowedMetadataChanges"],
        )

    def test_plan_refuses_a_train_without_complete_docker_evidence(self):
        planner = self.make_planner()
        planner.state.values[f"docker/completed/{TRAIN}.json"][0]["images"].pop()
        with self.assertRaisesRegex(ReleaseError, "31 planned images"):
            planner.build(
                release_version="2.9.3",
                next_version="2.9.4-SNAPSHOT",
                train=TRAIN,
                cee_version=PUBLIC_VERSION,
            )

    def test_independent_packages_are_not_platform_release_repositories(self):
        source = {"repositories": {
            "cedar-parent": "1" * 40,
            "cedar-workspace": "2" * 40,
            "cedar-template-designer": "3" * 40,
            "cedar-embeddable-editor": "4" * 40,
            "cedar-model-typescript-library": "5" * 40,
        }}
        release, maven = ReleasePlanner._release_repositories({
            "repositories": list(source["repositories"]),
            "mavenRepositories": ["cedar-parent"],
        }, source)
        self.assertEqual(["cedar-parent"], release)
        self.assertEqual(["cedar-parent"], maven)

    def test_release_version_is_verified_not_inferred_from_train(self):
        with self.assertRaisesRegex(ReleaseError, "explicit release"):
            self.make_planner().build(
                release_version="2.9.4",
                next_version="2.9.5-SNAPSHOT",
                train=TRAIN,
                cee_version=PUBLIC_VERSION,
            )

    def test_next_development_version_must_move_forward(self):
        with self.assertRaisesRegex(ReleaseError, "must be newer"):
            self.make_planner().build(
                release_version="2.9.3",
                next_version="2.9.3-SNAPSHOT",
                train=TRAIN,
                cee_version=PUBLIC_VERSION,
            )

    def test_recorded_dev_tarball_hash_is_enforced(self):
        planner = self.make_planner()
        npm_completion, content = planner.state.values[f"npm/completed/{TRAIN}.json"]
        npm_completion = copy.deepcopy(npm_completion)
        npm_completion["packages"][0]["tarballSha256"] = "0" * 64
        planner.state.values[f"npm/completed/{TRAIN}.json"] = (npm_completion, content)
        with self.assertRaisesRegex(ReleaseError, "recorded SHA-256"):
            planner.build(
                release_version="2.9.3",
                next_version="2.9.4-SNAPSHOT",
                train=TRAIN,
                cee_version=PUBLIC_VERSION,
            )


class ReleaseStateAndCliTest(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_release_offers_only_the_train_backed_commands(self):
        from org.metadatacenter import release

        group = typer.Typer()
        group.add_typer(release.app, name="release")
        release.app.add_typer(release_train.app)
        result = self.runner.invoke(group, ["release", "--help"])

        self.assertEqual(0, result.exit_code, result.output)
        for command in ("plan", "start", "resume", "status", "abandon"):
            self.assertIn(command, result.output)
        for retired in (
            "preflight", "conclude", "all-in-one", "prepare", "commit", "cleanup",
            "rollback", "check-tools",
        ):
            self.assertNotIn(retired, result.output)

    def test_transient_retry_is_default_not_a_flag(self):
        for command in ("start", "resume"):
            result = self.runner.invoke(release_train.app, [command, "--help"])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertNotIn("--unattended", result.output)
            self.assertNotIn("--check", result.output)
        result = self.runner.invoke(release_train.app, ["status", "--help"])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotIn("--json", result.output)

    def test_state_owns_the_manifest_and_refuses_a_second_active_release(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            path = state.start(manifest_fixture())
            manifest, current_path = state.read_current_manifest()
            self.assertEqual(path, current_path)
            self.assertEqual("started", manifest["phase"])
            with self.assertRaisesRegex(ReleaseError, "already active"):
                state.start(manifest_fixture())

    @patch.object(ReleasePreflight, "run", return_value=[])
    @patch.object(ReleasePlanner, "build", return_value=manifest_fixture())
    def test_plan_is_side_effect_free(self, build, _preflight):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CEDAR_RELEASE_STATE_DIR": directory}, clear=False,
        ):
            result = self.runner.invoke(release_train.app, [
                "plan",
                "--version", "2.9.3",
                "--next-version", "2.9.4-SNAPSHOT",
                "--from-train", TRAIN,
                "--cee-version", PUBLIC_VERSION,
            ])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn("No changes made.", result.output)
            self.assertEqual([], list(Path(directory).iterdir()))
        build.assert_called_once()

    @patch.object(ReleaseArtifactPublisher, "ensure_nexus_ready", return_value=None)
    @patch.object(ReleaseArtifactPublisher, "snapshot_tasks", return_value=[])
    @patch.object(ReleaseArtifactPublisher, "tasks", return_value=[])
    @patch.object(ReleaseRemoteIntegrator, "tasks", return_value=[])
    @patch.object(ReleaseRefCreator, "tasks", return_value=[])
    @patch.object(ReleaseBuildValidator, "tasks", return_value=[])
    @patch.object(ReleaseVersionPreparer, "prepare", return_value={"release": {}, "nextDevelopment": {}})
    @patch.object(ReleaseWorkspacePreparer, "prepare", return_value={"attempt": "001"})
    @patch.object(ReleasePreflight, "run", return_value=[])
    @patch.object(ReleasePlanner, "build", return_value=manifest_fixture())
    def test_start_persists_internal_state_and_status_finds_it(
        self, _build, _preflight, _workspace_prepare, _version_prepare, _build_tasks,
        _ref_tasks, _remote_tasks, _artifact_tasks, _snapshot_tasks, _nexus,
    ):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CEDAR_RELEASE_STATE_DIR": directory}, clear=False,
        ):
            start = self.runner.invoke(release_train.app, [
                "start",
                "--version", "2.9.3",
                "--next-version", "2.9.4-SNAPSHOT",
                "--from-train", TRAIN,
                "--cee-version", PUBLIC_VERSION,
            ])
            self.assertEqual(0, start.exit_code, start.output)
            self.assertIn("Phase:               accepted", start.output)
            status = self.runner.invoke(release_train.app, ["status"])
            self.assertEqual(0, status.exit_code, status.output)
            self.assertIn("Release 2.9.3 — COMPLETE", status.output)
            self.assertIn("local-refs", status.output)
            self.assertIn("artifacts", status.output)
            self.assertIn("acceptance", status.output)

    def test_status_separates_legacy_snapshots_from_release_artifacts(self):
        manifest = manifest_fixture()
        manifest.update({
            "phase": "artifacts-published",
            "artifactPublication": {"completedTasks": {
                "maven:nextDevelopment:verify": {
                    "id": "maven:nextDevelopment:verify"},
                "maven:release:verify": {"id": "maven:release:verify"},
            }},
        })

        release_publications, snapshot_publications = release_train._publication_progress(manifest)

        self.assertEqual(1, snapshot_publications)
        self.assertEqual(1, release_publications)
        self.assertEqual("acceptance", release_train._next_release_stage(manifest))

    def test_status_renders_a_frontend_preparation_failure_before_workspace_exists(self):
        manifest = manifest_fixture()
        manifest.update({
            "phase": "frontend-preparation-failed",
            "failure": "consumer preparation failed",
            "lastAttempt": "/tmp/release-attempt",
        })

        with release_train.console.capture() as capture:
            release_train._render_release_status(manifest, Path("/tmp/release.json"))

        output = capture.get()
        self.assertIn("INCOMPLETE", output)
        self.assertIn("consumer preparation failed", output)
        self.assertIn("Run:  cedarcli release resume", output)

    def test_abandon_command_records_the_operator_reason(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CEDAR_RELEASE_STATE_DIR": directory}, clear=False,
        ):
            state = ReleaseState(root=Path(directory))
            state.start(manifest_fixture())
            state.update_current_manifest({"phase": "local-ref-creation-failed"})

            result = self.runner.invoke(release_train.app, [
                "abandon",
                "--version", "2.9.3",
                "--reason", "superseded by a corrected train",
            ])

            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn("Abandoned release:   2.9.3", result.output)
            manifest, _ = state.read_current_manifest()
            self.assertEqual(
                "superseded by a corrected train",
                manifest["abandonment"]["reason"],
            )


class ReleaseWorkspaceTest(unittest.TestCase):
    REPOSITORY_CONSUMERS = {
        "frontend-main": [("main", "package.json", "package-lock.json")],
        "frontend-workspace": [("workspace", "package.json", "package-lock.json")],
        "frontend-bridging": [("bridging", "src/package.json", "src/package-lock.json")],
        "frontend-openview": [("openview", "src/package.json", "src/package-lock.json")],
        "frontend-demo": [
            ("angular", "angular/package.json", "angular/package-lock.json"),
            ("ember", "ember/package.json", "ember/package-lock.json"),
            ("react", "react/package.json", "react/package-lock.json"),
        ],
    }

    @staticmethod
    def _write_consumer(root, manifest_path, lock_path, version=DEV_VERSION):
        spec = f"npm:{DEV_CEE_NAME}@{version}"
        manifest = root / manifest_path
        lock = root / lock_path
        manifest.parent.mkdir(parents=True, exist_ok=True)
        lock.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "dependencies": {"cedar-embeddable-editor": spec},
        }, indent=2) + "\n", encoding="utf-8")
        lock.write_text(json.dumps({
            "packages": {
                "": {"dependencies": {"cedar-embeddable-editor": spec}},
                "node_modules/cedar-embeddable-editor": {
                    "version": version,
                    "resolved": "https://nexus.bmir.stanford.edu/repository/npm-cedar/dev.tgz",
                    "integrity": "sha512-dev",
                },
            },
        }, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _commit_repository(root):
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run([
            "git", "-C", str(root), "config", "user.email", "test@example.org",
        ], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(root), "commit", "--quiet", "-m", "fixture",
        ], check=True)
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()

    def make_workspace(self, directory):
        cedar_home = Path(directory) / "CEDAR"
        consumers = []
        revisions = {}
        for repository, entries in self.REPOSITORY_CONSUMERS.items():
            root = cedar_home / repository
            root.mkdir(parents=True)
            for label, manifest_path, lock_path in entries:
                self._write_consumer(root, manifest_path, lock_path)
                consumers.append({
                    "label": label,
                    "repository": repository,
                    "manifest": manifest_path,
                    "lock": lock_path,
                })
            revisions[repository] = self._commit_repository(root)
            for consumer in consumers:
                if consumer["repository"] == repository:
                    consumer["revision"] = revisions[repository]
        development = cedar_home / "cedar-development"
        helper = development / "ops" / "propagate-cee-release.mjs"
        helper.parent.mkdir(parents=True)
        helper.write_text("// exact train helper\n", encoding="utf-8")
        revisions["cedar-development"] = self._commit_repository(development)
        manifest = manifest_fixture()
        manifest["sourceRepositories"] = revisions
        manifest["cee"]["consumers"] = consumers
        return cedar_home, manifest

    @staticmethod
    def _successful_runner(consumers):
        def run(args, **kwargs):
            if args[0] != "node":
                return subprocess.run(args, **kwargs)
            if "--apply" in args:
                workspace = Path(kwargs["env"]["CEDAR_HOME"])
                for consumer in consumers:
                    manifest_path = workspace / consumer["repository"] / consumer["manifest"]
                    lock_path = workspace / consumer["repository"] / consumer["lock"]
                    package = json.loads(manifest_path.read_bytes())
                    lock = json.loads(lock_path.read_bytes())
                    package["dependencies"]["cedar-embeddable-editor"] = PUBLIC_VERSION
                    lock["packages"][""]["dependencies"]["cedar-embeddable-editor"] = PUBLIC_VERSION
                    installed = lock["packages"]["node_modules/cedar-embeddable-editor"]
                    installed.update({
                        "version": PUBLIC_VERSION,
                        "resolved": (
                            "https://registry.npmjs.org/cedar-embeddable-editor/"
                            f"-/cedar-embeddable-editor-{PUBLIC_VERSION}.tgz"
                        ),
                        "integrity": "sha512-public",
                    })
                    manifest_path.write_text(
                        json.dumps(package, indent=2) + "\n", encoding="utf-8",
                    )
                    lock_path.write_text(
                        json.dumps(lock, indent=2) + "\n", encoding="utf-8",
                    )
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")
        return run

    def test_preparation_clones_exact_commits_and_only_rewires_isolated_consumers(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, manifest = self.make_workspace(directory)
            state = ReleaseState(root=Path(directory) / "state")
            state.start(manifest)
            runner = self._successful_runner(manifest["cee"]["consumers"])
            preparer = ReleaseWorkspacePreparer(
                state, command_runner=runner, environment={"CEDAR_HOME": str(cedar_home)},
            )
            completed = prepare_active_release(state, preparer)
            self.assertEqual("frontends-prepared", completed["phase"])
            preparation = completed["frontendPreparation"]
            self.assertEqual(7, len(preparation["consumers"]))
            self.assertEqual("001", preparation["attempt"])
            original = json.loads((cedar_home / "frontend-main" / "package.json").read_bytes())
            self.assertIn("-dev.", original["dependencies"]["cedar-embeddable-editor"])
            isolated = Path(preparation["workspace"]) / "frontend-main" / "package.json"
            self.assertEqual(
                PUBLIC_VERSION,
                json.loads(isolated.read_bytes())["dependencies"]["cedar-embeddable-editor"],
            )

    def test_preparation_accepts_consumers_already_pinned_to_public_cee(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, manifest = self.make_workspace(directory)
            runner = self._successful_runner(manifest["cee"]["consumers"])
            runner(
                ["node", "propagate-cee-release.mjs", "--apply", PUBLIC_VERSION],
                env={"CEDAR_HOME": str(cedar_home)},
            )
            repositories = {
                consumer["repository"] for consumer in manifest["cee"]["consumers"]
            }
            for repository in repositories:
                manifest["sourceRepositories"][repository] = self._commit_repository(
                    cedar_home / repository
                )

            state = ReleaseState(root=Path(directory) / "state")
            state.start(manifest)
            preparer = ReleaseWorkspacePreparer(
                state, command_runner=runner, environment={"CEDAR_HOME": str(cedar_home)},
            )

            completed = prepare_active_release(state, preparer)

            self.assertEqual("frontends-prepared", completed["phase"])
            prepared = completed["frontendPreparation"]["repositories"]
            for repository in repositories:
                self.assertEqual([], prepared[repository]["changedFiles"])

    def test_failed_attempt_is_retained_and_resume_uses_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, manifest = self.make_workspace(directory)
            state = ReleaseState(root=Path(directory) / "state")
            state.start(manifest)

            def fail_node(args, **kwargs):
                if args[0] == "node":
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="npm failed")
                return subprocess.run(args, **kwargs)

            failed_preparer = ReleaseWorkspacePreparer(
                state, command_runner=fail_node, environment={"CEDAR_HOME": str(cedar_home)},
            )
            with self.assertRaisesRegex(ReleaseError, "npm failed"):
                prepare_active_release(state, failed_preparer)
            failed, _ = state.read_current_manifest()
            self.assertEqual("frontend-preparation-failed", failed["phase"])
            self.assertTrue(Path(failed["lastAttempt"]).is_dir())

            successful = ReleaseWorkspacePreparer(
                state,
                command_runner=self._successful_runner(manifest["cee"]["consumers"]),
                environment={"CEDAR_HOME": str(cedar_home)},
            )
            completed = prepare_active_release(state, successful)
            self.assertEqual("002", completed["frontendPreparation"]["attempt"])


class ReleaseVersionPreparationTest(unittest.TestCase):
    def make_sources(self, directory):
        cedar_home = Path(directory) / "CEDAR"
        source_version = "2.9.3-SNAPSHOT"
        files = {
            "maven-repo": {
                "pom.xml": (
                    "<project><version>2.9.3-SNAPSHOT</version>"
                    "<properties><cedar.version>2.9.3-SNAPSHOT</cedar.version></properties>"
                    "</project>\n"
                ),
                "module/pom.xml": (
                    "<project><parent><version>2.9.3-SNAPSHOT</version></parent></project>\n"
                ),
            },
            "cedar-terminology-server": {
                "pom.xml": (
                    "<project><version>2.9.3-SNAPSHOT</version></project>\n"
                ),
                (
                    "cedar-terminology-server-application/src/main/resources/"
                    "assets/swagger-api/swagger.json"
                ): '{"info":{"version" : "2.9.3-SNAPSHOT"}}\n',
                (
                    "cedar-terminology-server-application/src/main/resources/"
                    "assets/swagger-api/swagger.yaml"
                ): "info:\n  version: 2.9.3-SNAPSHOT\n",
            },
            "cedar-resource-server": {
                "pom.xml": (
                    "<project><version>2.9.3-SNAPSHOT</version></project>\n"
                ),
                (
                    "cedar-resource-server-application/src/main/resources/"
                    "assets/swagger-api/swagger.json"
                ): '{"info":{"version" : "2.9.3-SNAPSHOT"}}\n',
                (
                    "cedar-resource-server-application/src/main/resources/"
                    "assets/swagger-api/swagger.yaml"
                ): "info:\n  version: 2.9.3-SNAPSHOT\n",
            },
            "cedar-valuerecommender-server": {
                "pom.xml": (
                    "<project><version>2.9.3-SNAPSHOT</version></project>\n"
                ),
                (
                    "cedar-valuerecommender-server-application/src/main/resources/"
                    "assets/swagger-api/swagger.json"
                ): '{"info":{"version" : "2.9.3-SNAPSHOT"}}\n',
                (
                    "cedar-valuerecommender-server-application/src/main/resources/"
                    "assets/swagger-api/swagger.yaml"
                ): "info:\n  version: 2.9.3-SNAPSHOT\n",
            },
            "cedar-template-editor": {},
            "cedar-development": {
                "bin/util/set-env-generic.sh": f"export CEDAR_VERSION={source_version}\n",
            },
            "cedar-docker-build": {
                "frontend/Dockerfile": f"ENV CEDAR_VERSION={source_version}\n",
                "dynamic/Dockerfile": "ENV CEDAR_VERSION=${CEDAR_MAVEN_VERSION}\n",
                "bin/cedar-images-base.sh": (
                    f"export IMAGE_VERSION={source_version}\n"
                    f"export CEDAR_MAVEN_VERSION={source_version}\n"
                    f"export CEDAR_APPLICATION_VERSION={source_version}\n"
                ),
            },
            "cedar-docker-deploy": {
                "stack/.env": f"CEDAR_DOCKER_VERSION={source_version}\n",
            },
            "cedar-cli": {"README.md": "plain repository\n"},
        }
        package = {
            "name": "cedar-template-editor",
            "version": source_version,
            "dependencies": {},
        }
        lock = {
            "name": "cedar-template-editor",
            "version": source_version,
            "packages": {"": {"version": source_version, "dependencies": {}}},
        }
        files["cedar-template-editor"] = {
            "package.json": json.dumps(package, indent=2) + "\n",
            "package-lock.json": json.dumps(lock, indent=2) + "\n",
        }
        revisions = {}
        for repository, repository_files in files.items():
            root = cedar_home / repository
            for relative, content in repository_files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            revisions[repository] = ReleaseWorkspaceTest._commit_repository(root)
        return cedar_home, revisions

    def test_stamps_release_and_next_variants_from_the_same_exact_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, revisions = self.make_sources(directory)
            state = ReleaseState(root=Path(directory) / "state")
            workspace_preparer = ReleaseWorkspacePreparer(
                state, environment={"CEDAR_HOME": str(cedar_home)},
            )
            release_workspace = Path(directory) / "attempt" / "workspace"
            for repository, revision in revisions.items():
                workspace_preparer._clone(
                    repository, revision, release_workspace / repository,
                )
            release_manifest = release_workspace / "cedar-template-editor" / "package.json"
            release_lock = release_workspace / "cedar-template-editor" / "package-lock.json"
            package = json.loads(release_manifest.read_bytes())
            lock = json.loads(release_lock.read_bytes())
            package["dependencies"]["cedar-embeddable-editor"] = PUBLIC_VERSION
            lock["packages"][""]["dependencies"]["cedar-embeddable-editor"] = PUBLIC_VERSION
            release_manifest.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
            release_lock.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
            manifest = manifest_fixture()
            manifest.update({
                "sourceRepositories": revisions,
                "releaseRepositories": list(revisions),
                "mavenRepositories": [
                    "maven-repo",
                    "cedar-resource-server",
                    "cedar-terminology-server",
                    "cedar-valuerecommender-server",
                ],
                "frontendPreparation": {
                    "workspace": str(release_workspace),
                    "consumers": [{
                        "repository": "cedar-template-editor",
                        "manifest": "package.json",
                        "lock": "package-lock.json",
                        "manifestSha256": release_train._file_sha256(release_manifest),
                        "lockSha256": release_train._file_sha256(release_lock),
                    }],
                },
            })
            manifest["cee"]["consumers"] = [{
                "repository": "cedar-template-editor",
                "manifest": "package.json",
                "lock": "package-lock.json",
            }]
            result = ReleaseVersionPreparer(
                state, workspace_preparer=workspace_preparer,
            ).prepare(manifest)

            release_package = json.loads(
                (release_workspace / "cedar-template-editor" / "package.json").read_bytes()
            )
            next_workspace = Path(result["nextDevelopment"]["workspace"])
            next_package = json.loads(
                (next_workspace / "cedar-template-editor" / "package.json").read_bytes()
            )
            self.assertEqual("2.9.3", release_package["version"])
            self.assertEqual("2.9.4-SNAPSHOT", next_package["version"])
            self.assertEqual(
                PUBLIC_VERSION,
                release_package["dependencies"]["cedar-embeddable-editor"],
            )
            self.assertEqual(
                PUBLIC_VERSION,
                next_package["dependencies"]["cedar-embeddable-editor"],
            )
            self.assertIn(
                "<version>2.9.3</version>",
                (release_workspace / "maven-repo" / "pom.xml").read_text(),
            )
            self.assertIn(
                "<version>2.9.4-SNAPSHOT</version>",
                (next_workspace / "maven-repo" / "pom.xml").read_text(),
            )
            release_swagger = (
                release_workspace / "cedar-terminology-server"
                / "cedar-terminology-server-application/src/main/resources/assets/"
                "swagger-api/swagger.json"
            )
            next_swagger = (
                next_workspace / "cedar-terminology-server"
                / "cedar-terminology-server-application/src/main/resources/assets/"
                "swagger-api/swagger.yaml"
            )
            self.assertIn('"version" : "2.9.3"', release_swagger.read_text())
            self.assertIn("version: 2.9.4-SNAPSHOT", next_swagger.read_text())
            self.assertIn(
                release_swagger.relative_to(
                    release_workspace / "cedar-terminology-server"
                ).as_posix(),
                result["release"]["repositories"]["cedar-terminology-server"][
                    "changedFiles"
                ],
            )
            release_resource_swagger = (
                release_workspace / "cedar-resource-server"
                / "cedar-resource-server-application/src/main/resources/assets/"
                "swagger-api/swagger.yaml"
            )
            next_resource_swagger = (
                next_workspace / "cedar-resource-server"
                / "cedar-resource-server-application/src/main/resources/assets/"
                "swagger-api/swagger.json"
            )
            self.assertIn("version: 2.9.3", release_resource_swagger.read_text())
            self.assertIn(
                '"version" : "2.9.4-SNAPSHOT"',
                next_resource_swagger.read_text(),
            )
            self.assertIn(
                release_resource_swagger.relative_to(
                    release_workspace / "cedar-resource-server"
                ).as_posix(),
                result["release"]["repositories"]["cedar-resource-server"][
                    "changedFiles"
                ],
            )
            release_valuerecommender_swagger = (
                release_workspace / "cedar-valuerecommender-server"
                / "cedar-valuerecommender-server-application/src/main/resources/assets/"
                "swagger-api/swagger.yaml"
            )
            next_valuerecommender_swagger = (
                next_workspace / "cedar-valuerecommender-server"
                / "cedar-valuerecommender-server-application/src/main/resources/assets/"
                "swagger-api/swagger.json"
            )
            self.assertIn(
                "version: 2.9.3", release_valuerecommender_swagger.read_text()
            )
            self.assertIn(
                '"version" : "2.9.4-SNAPSHOT"',
                next_valuerecommender_swagger.read_text(),
            )
            self.assertIn(
                release_valuerecommender_swagger.relative_to(
                    release_workspace / "cedar-valuerecommender-server"
                ).as_posix(),
                result["release"]["repositories"]["cedar-valuerecommender-server"][
                    "changedFiles"
                ],
            )
            self.assertIn(
                "${CEDAR_MAVEN_VERSION}",
                (release_workspace / "cedar-docker-build" / "dynamic" / "Dockerfile").read_text(),
            )
            release_docker_versions = (
                release_workspace / "cedar-docker-build" / "bin" / "cedar-images-base.sh"
            ).read_text()
            next_docker_versions = (
                next_workspace / "cedar-docker-build" / "bin" / "cedar-images-base.sh"
            ).read_text()
            for variable in (
                "IMAGE_VERSION", "CEDAR_MAVEN_VERSION", "CEDAR_APPLICATION_VERSION",
            ):
                self.assertIn(f"export {variable}=2.9.3", release_docker_versions)
                self.assertIn(f"export {variable}=2.9.4-SNAPSHOT", next_docker_versions)
            original_package = json.loads(
                (cedar_home / "cedar-template-editor" / "package.json").read_bytes()
            )
            self.assertEqual("2.9.3-SNAPSHOT", original_package["version"])

    def test_partial_version_failure_is_recorded_for_a_fresh_resume_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            manifest = manifest_fixture()
            manifest["phase"] = "frontends-prepared"
            state.start(manifest)
            state.update_current_manifest({"phase": "frontends-prepared"})

            class FailingPreparer:
                def prepare(self, _manifest):
                    raise ReleaseError("version stamp failed")

            from org.metadatacenter.release_train import prepare_active_release_versions
            with self.assertRaisesRegex(ReleaseError, "version stamp failed"):
                prepare_active_release_versions(state, FailingPreparer())
            failed, _ = state.read_current_manifest()
            self.assertEqual("version-preparation-failed", failed["phase"])


class ReleaseBuildValidationTest(unittest.TestCase):
    def make_manifest(self, directory, include_frontend=False):
        attempt = Path(directory) / "attempt"
        variants = {}
        release_repositories = ["cedar-parent"]
        for variant, version in (
            ("release", "2.9.3"),
            ("nextDevelopment", "2.9.4-SNAPSHOT"),
        ):
            workspace = attempt / ("workspace" if variant == "release" else "next-workspace")
            parent = workspace / "cedar-parent"
            parent.mkdir(parents=True)
            wrapper = parent / "mvnw"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            if include_frontend:
                frontend = workspace / "cedar-template-editor"
                frontend.mkdir()
                (frontend / "package.json").write_text("{}\n", encoding="utf-8")
                (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
            variants[variant] = {"version": version, "workspace": str(workspace)}
        if include_frontend:
            release_repositories.append("cedar-template-editor")
        manifest = manifest_fixture()
        manifest.update({
            "phase": "versions-prepared",
            "releaseRepositories": release_repositories,
            "mavenPhases": [{"name": "parent", "repository": "cedar-parent"}],
            "frontendPreparation": {"workspace": variants["release"]["workspace"]},
            "versionPreparation": variants,
        })
        return manifest

    def test_build_plan_runs_release_tests_and_only_skips_next_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(directory, include_frontend=True)
            validator = ReleaseBuildValidator(ReleaseState(root=Path(directory) / "state"))
            tasks = validator.tasks(manifest)
            release_maven = next(task for task in tasks if task["id"] == "release:maven:parent")
            next_maven = next(
                task for task in tasks if task["id"] == "nextDevelopment:maven:parent"
            )
            self.assertNotIn("-DskipTests", release_maven["command"])
            self.assertIn("-DskipTests", next_maven["command"])
            self.assertTrue(release_maven["tests"])
            self.assertIn("release:npm:template-editor:install", {task["id"] for task in tasks})
            self.assertIn(
                "nextDevelopment:npm:template-editor:install", {task["id"] for task in tasks},
            )

    def test_angular_builds_do_not_forward_options_past_chained_package_scripts(self):
        angular_surfaces = {"openview", "bridging", "monitoring", "cee-demo-angular"}
        commands = {
            surface["id"]: surface["build"]
            for surface in release_train.FRONTEND_BUILD_SURFACES
            if surface["id"] in angular_surfaces
        }
        self.assertEqual(
            {surface: ["npm", "run", "build"] for surface in angular_surfaces},
            commands,
        )

    def test_frontend_install_modes_cover_committed_peer_dependency_graphs(self):
        install_options = {
            surface["id"]: surface["install"]
            for surface in release_train.FRONTEND_BUILD_SURFACES
        }
        self.assertEqual(["--legacy-peer-deps"], install_options["openview"])
        self.assertEqual(["--legacy-peer-deps"], install_options["monitoring"])
        self.assertEqual(["--legacy-peer-deps"], install_options["content"])
        self.assertEqual(["--legacy-peer-deps"], install_options["cee-demo-angular"])

    def test_failed_build_resume_keeps_logs_and_skips_completed_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(directory)
            state = ReleaseState(root=Path(directory) / "state")
            state.start(manifest)
            state.update_current_manifest({"phase": "versions-prepared"})
            first_calls = []

            def fail_next(task, _environment):
                first_calls.append(task["id"])
                if task["id"] == "nextDevelopment:maven:parent":
                    raise ReleaseError("next build failed")
                return f"completed {task['id']}\n"

            with self.assertRaisesRegex(ReleaseError, "next build failed"):
                validate_active_release_builds(
                    state, ReleaseBuildValidator(state, executor=fail_next),
                )
            failed, _ = state.read_current_manifest()
            self.assertEqual("build-validation-failed", failed["phase"])
            self.assertIn(
                "release:maven:parent",
                failed["buildValidation"]["completedTasks"],
            )
            completed_log = Path(
                failed["buildValidation"]["completedTasks"]["release:maven:parent"]["log"]
            )
            self.assertTrue(completed_log.is_file())

            resumed_calls = []

            def succeed(task, _environment):
                resumed_calls.append(task["id"])
                return f"completed {task['id']}\n"

            completed = validate_active_release_builds(
                state, ReleaseBuildValidator(state, executor=succeed),
            )
            self.assertEqual("builds-validated", completed["phase"])
            self.assertEqual(["nextDevelopment:maven:parent"], resumed_calls)
            self.assertEqual(
                2, len(completed["buildValidation"]["completedTasks"]),
            )

    def test_changed_completed_log_blocks_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(directory)
            state = ReleaseState(root=Path(directory) / "state")
            state.start(manifest)
            state.update_current_manifest({"phase": "versions-prepared"})
            completed = validate_active_release_builds(
                state,
                ReleaseBuildValidator(
                    state, executor=lambda task, _environment: f"{task['id']}\n",
                ),
            )
            record = next(iter(completed["buildValidation"]["completedTasks"].values()))
            Path(record["log"]).write_text("tampered\n", encoding="utf-8")
            state.update_current_manifest({"phase": "build-validation-failed"})
            with self.assertRaisesRegex(ReleaseError, "missing or changed"):
                validate_active_release_builds(
                    state, ReleaseBuildValidator(state, executor=lambda *_args: ""),
                )

    def test_changed_frontend_build_output_blocks_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "build.log"
            output = root / "dist"
            output.mkdir()
            bundle = output / "main.js"
            log.write_text("built\n", encoding="utf-8")
            bundle.write_text("release bundle\n", encoding="utf-8")
            record = {
                "id": "release:npm:frontend:build",
                "log": str(log),
                "logSha256": release_train._file_sha256(log),
                "buildOutput": str(output),
                "outputFiles": {"main.js": release_train._file_sha256(bundle)},
            }

            ReleaseBuildValidator.verify_completed_task(record)
            bundle.write_text("changed bundle\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseError, "build output changed"):
                ReleaseBuildValidator.verify_completed_task(record)

    def test_openview_runtime_must_be_the_normalized_proven_public_cee(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "node_modules" / "cee" / "cee.js"
            served = root / "dist" / "node_modules" / "cee" / "cee.js"
            source.parent.mkdir(parents=True)
            served.parent.mkdir(parents=True)
            public = (
                b"const terminology='https://terminology.metadatacenter.orgx/';"
                b"const bridge='https://bridge.metadatacenter.orgx/';"
            )
            normalized = public.replace(
                b"https://terminology.metadatacenter.orgx/",
                b"https://terminology.metadatacenter.org/",
            ).replace(
                b"https://bridge.metadatacenter.orgx/",
                b"https://bridge.metadatacenter.org/",
            )
            source.write_bytes(public)
            served.write_bytes(normalized)
            manifest = manifest_fixture()
            manifest["cee"]["promotionProof"]["publicBundleSha256"] = hashlib.sha256(
                public).hexdigest()
            task = {"ceeRuntime": {
                "source": "src/node_modules/cee/cee.js",
                "distribution": "node_modules/cee/cee.js",
                "replacements": [
                    ["https://terminology.metadatacenter.orgx/",
                     "https://terminology.metadatacenter.org/"],
                    ["https://bridge.metadatacenter.orgx/",
                     "https://bridge.metadatacenter.org/"],
                ],
            }}

            evidence = release_train.ReleaseDistributionMaterializer._cee_evidence(
                manifest, root, root / "dist", task)

            self.assertEqual(PUBLIC_VERSION, evidence["version"])
            self.assertEqual(hashlib.sha256(normalized).hexdigest(),
                             evidence["servedBundleSha256"])
            served.write_bytes(b"stale CEE")
            with self.assertRaisesRegex(ReleaseError, "does not serve the proven public CEE"):
                release_train.ReleaseDistributionMaterializer._cee_evidence(
                    manifest, root, root / "dist", task)


class ReleaseLocalRefsTest(unittest.TestCase):
    @staticmethod
    def _git(root, *arguments, check=True):
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=check,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def make_release(self, directory):
        base = Path(directory)
        cedar_home = base / "CEDAR"
        main_source = cedar_home / "repo-main"
        workspace_source = cedar_home / "cedar-workspace"
        main_source.mkdir(parents=True)
        workspace_source.mkdir(parents=True)
        (main_source / "version.txt").write_text("2.9.3-SNAPSHOT\n", encoding="utf-8")
        ReleaseWorkspaceTest._write_consumer(
            workspace_source, "package.json", "package-lock.json",
        )
        revisions = {
            "repo-main": ReleaseWorkspaceTest._commit_repository(main_source),
            "cedar-workspace": ReleaseWorkspaceTest._commit_repository(workspace_source),
        }

        state = ReleaseState(root=base / "state")
        cloner = ReleaseWorkspacePreparer(
            state, environment={"CEDAR_HOME": str(cedar_home)},
        )
        release_workspace = base / "attempt" / "workspace"
        next_workspace = base / "attempt" / "next-workspace"
        for repository, revision in revisions.items():
            cloner._clone(repository, revision, release_workspace / repository)
        cloner._clone("repo-main", revisions["repo-main"], next_workspace / "repo-main")

        release_main = release_workspace / "repo-main" / "version.txt"
        next_main = next_workspace / "repo-main" / "version.txt"
        release_main.write_text("2.9.3\n", encoding="utf-8")
        next_main.write_text("2.9.4-SNAPSHOT\n", encoding="utf-8")
        consumer = {
            "repository": "cedar-workspace",
            "manifest": "package.json",
            "lock": "package-lock.json",
        }
        ReleaseWorkspaceTest._successful_runner([consumer])(
            ["node", "helper", "--apply", PUBLIC_VERSION],
            env={"CEDAR_HOME": str(release_workspace)},
        )
        release_consumer = release_workspace / "cedar-workspace"

        manifest = manifest_fixture()
        manifest.update({
            "phase": "builds-validated",
            "sourceRepositories": revisions,
            "releaseRepositories": ["repo-main"],
            "frontendPreparation": {
                "workspace": str(release_workspace),
                "consumers": [{
                    **consumer,
                    "manifestSha256": release_train._file_sha256(
                        release_consumer / "package.json"
                    ),
                    "lockSha256": release_train._file_sha256(
                        release_consumer / "package-lock.json"
                    ),
                }],
            },
            "versionPreparation": {
                "release": {
                    "workspace": str(release_workspace),
                    "repositories": {
                        "repo-main": {
                            "fileSha256": {
                                "version.txt": release_train._file_sha256(release_main),
                            },
                        },
                    },
                },
                "nextDevelopment": {
                    "workspace": str(next_workspace),
                    "repositories": {
                        "repo-main": {
                            "fileSha256": {
                                "version.txt": release_train._file_sha256(next_main),
                            },
                        },
                    },
                },
            },
        })
        manifest["cee"]["consumers"] = [consumer]
        state.start(manifest)
        state.update_current_manifest({"phase": "builds-validated"})
        creator = ReleaseRefCreator(state, environment={
            "CEDAR_RELEASE_GIT_NAME": "CEDAR Release Test",
            "CEDAR_RELEASE_GIT_EMAIL": "release-test@example.org",
        })
        return state, creator, cedar_home, release_workspace, next_workspace

    def test_creates_verified_local_release_and_next_refs_without_touching_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            state, creator, cedar_home, release_workspace, next_workspace = self.make_release(
                directory
            )
            completed = create_active_release_refs(state, creator)
            self.assertEqual("local-refs-created", completed["phase"])
            self.assertFalse(completed["localRefs"]["pushed"])
            self.assertEqual(3, len(completed["localRefs"]["completedTasks"]))

            for repository in ("repo-main", "cedar-workspace"):
                root = release_workspace / repository
                branch = self._git(root, "rev-parse", "release/pre-2.9.3")
                tag = self._git(root, "rev-parse", "release-2.9.3")
                self.assertEqual(branch, tag)
                self.assertEqual(
                    completed["sourceRepositories"][repository],
                    self._git(root, "rev-parse", f"{branch}^"),
                )
            next_root = next_workspace / "repo-main"
            self.assertEqual(
                self._git(next_root, "rev-parse", "HEAD"),
                self._git(next_root, "rev-parse", "release/post-2.9.4-SNAPSHOT"),
            )
            self.assertEqual(
                completed["sourceRepositories"]["repo-main"],
                self._git(next_root, "rev-parse", "HEAD^"),
            )
            self.assertNotEqual(0, subprocess.run(
                ["git", "-C", str(next_root), "show-ref", "--verify", "--quiet",
                 "refs/tags/release-2.9.3"],
                check=False,
            ).returncode)

            for repository in ("repo-main", "cedar-workspace"):
                source = cedar_home / repository
                self.assertNotEqual(0, subprocess.run(
                    ["git", "-C", str(source), "show-ref", "--verify", "--quiet",
                     "refs/heads/release/pre-2.9.3"],
                    check=False,
                ).returncode)
                self.assertNotEqual(0, subprocess.run(
                    ["git", "-C", str(source), "show-ref", "--verify", "--quiet",
                     "refs/tags/release-2.9.3"],
                    check=False,
                ).returncode)

    def test_resume_verifies_completed_refs_and_does_not_recommit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, creator, _cedar_home, release_workspace, _next_workspace = self.make_release(
                directory
            )
            completed = create_active_release_refs(state, creator)
            original = completed["localRefs"]["completedTasks"]["release:repo-main"]["commit"]
            state.update_current_manifest({"phase": "local-ref-creation-failed"})
            resumed = create_active_release_refs(state, creator)
            self.assertEqual("local-refs-created", resumed["phase"])
            self.assertEqual(
                original,
                self._git(release_workspace / "repo-main", "rev-parse", "HEAD"),
            )

    def test_already_correct_cee_consumer_gets_verified_empty_release_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, creator, _cedar_home, release_workspace, _next_workspace = (
                self.make_release(directory)
            )
            root = release_workspace / "cedar-workspace"
            self._git(root, "restore", "package.json", "package-lock.json")
            manifest, _ = state.read_current_manifest()
            frontend = manifest["frontendPreparation"]
            frontend["repositories"] = {
                "cedar-workspace": {
                    "changedFiles": [],
                    "path": str(root),
                    "revision": manifest["sourceRepositories"]["cedar-workspace"],
                },
            }
            consumer = frontend["consumers"][0]
            consumer["manifestSha256"] = release_train._file_sha256(root / "package.json")
            consumer["lockSha256"] = release_train._file_sha256(root / "package-lock.json")
            state.update_current_manifest({"frontendPreparation": frontend})

            completed = create_active_release_refs(state, creator)

            record = completed["localRefs"]["completedTasks"]["release:cedar-workspace"]
            self.assertEqual([], record["changedFiles"])
            self.assertEqual(
                manifest["sourceRepositories"]["cedar-workspace"],
                self._git(root, "rev-parse", f"{record['commit']}^"),
            )

    def test_prepared_file_drift_blocks_local_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, creator, _cedar_home, release_workspace, _next_workspace = self.make_release(
                directory
            )
            (release_workspace / "repo-main" / "version.txt").write_text(
                "tampered\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(ReleaseError, "changed after validation"):
                create_active_release_refs(state, creator)
            failed, _ = state.read_current_manifest()
            self.assertEqual("local-ref-creation-failed", failed["phase"])

    def test_validated_distribution_replaces_stale_tracked_build_before_refs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cedar_home = base / "CEDAR"
            source = cedar_home / "frontend"
            source.mkdir(parents=True)
            (source / ".gitignore").write_text("/src/dist/\n", encoding="utf-8")
            dist = source / "dist"
            dist.mkdir()
            for name, content in {
                "package.json": '{"name":"frontend","version":"2.9.2"}\n',
                "package-lock.json": '{"name":"frontend","version":"2.9.2"}\n',
                "README.md": "frontend\n",
                "license.txt": "BSD\n",
                "main.old.js": "stale\n",
            }.items():
                (dist / name).write_text(content, encoding="utf-8")
            revision = ReleaseWorkspaceTest._commit_repository(source)
            state = ReleaseState(root=base / "state")
            cloner = ReleaseWorkspacePreparer(
                state, environment={"CEDAR_HOME": str(cedar_home)},
            )
            workspaces = {
                "release": base / "attempt" / "workspace",
                "nextDevelopment": base / "attempt" / "next-workspace",
            }
            versions = {"release": "2.9.3", "nextDevelopment": "2.9.4-SNAPSHOT"}
            version_preparation = {}
            build_records = {}
            for variant, workspace in workspaces.items():
                root = workspace / "frontend"
                cloner._clone("frontend", revision, root)
                for filename in ("package.json", "package-lock.json"):
                    path = root / "dist" / filename
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["version"] = versions[variant]
                    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
                output = root / "src" / "dist" / "app"
                output.mkdir(parents=True)
                (output / "index.html").write_text(
                    '<script src="main.new.js"></script>\n', encoding="utf-8")
                (output / "main.new.js").write_text(
                    f"built {variant}\n", encoding="utf-8")
                log = base / "attempt" / f"{variant}.log"
                log.write_text("built\n", encoding="utf-8")
                build_id = f"{variant}:npm:frontend:build"
                build_records[build_id] = {
                    "id": build_id,
                    "buildOutput": str(output),
                    "outputFiles": release_train._directory_file_hashes(output),
                    "log": str(log),
                    "logSha256": release_train._file_sha256(log),
                }
                version_preparation[variant] = {
                    "workspace": str(workspace),
                    "repositories": {"frontend": {"fileSha256": {
                        f"dist/{filename}": release_train._file_sha256(root / "dist" / filename)
                        for filename in ("package.json", "package-lock.json")
                    }}},
                }
            manifest = manifest_fixture()
            manifest.update({
                "phase": "builds-validated",
                "sourceRepositories": {"frontend": revision},
                "releaseRepositories": ["frontend"],
                "frontendPreparation": {"workspace": str(workspaces["release"]), "consumers": []},
                "versionPreparation": version_preparation,
                "buildValidation": {"completedTasks": build_records},
                "publicationPlan": {"npm": {"surfaces": [{
                    "id": "frontend",
                    "repository": "frontend",
                    "directory": "dist",
                    "buildOutput": "src/dist/app",
                    "preserveFiles": [
                        "README.md", "license.txt", "package-lock.json", "package.json",
                    ],
                }]}},
            })
            manifest["cee"]["consumers"] = []
            state.start(manifest)
            state.update_current_manifest({"phase": "builds-validated"})
            creator = ReleaseRefCreator(state, environment={
                "CEDAR_RELEASE_GIT_NAME": "CEDAR Release Test",
                "CEDAR_RELEASE_GIT_EMAIL": "release-test@example.org",
            })

            completed = create_active_release_refs(state, creator)

            self.assertEqual("local-refs-created", completed["phase"])
            for variant, workspace in workspaces.items():
                root = workspace / "frontend"
                self.assertFalse((root / "dist" / "main.old.js").exists())
                self.assertEqual(
                    f"built {variant}\n",
                    (root / "dist" / "main.new.js").read_text(encoding="utf-8"),
                )
                expected = completed["versionPreparation"][variant][
                    "repositories"]["frontend"]["fileSha256"]
                self.assertIsNone(expected["dist/main.old.js"])
                self.assertEqual(
                    release_train._file_sha256(root / "dist" / "main.new.js"),
                    expected["dist/main.new.js"],
                )
                commit = completed["localRefs"]["completedTasks"][
                    f"{variant}:frontend"]["commit"]
                changed = set(self._git(
                    root, "diff", "--name-only", revision, commit, "--",
                ).splitlines())
                self.assertIn("dist/main.old.js", changed)
                self.assertIn("dist/main.new.js", changed)

    def test_materialization_carries_repository_license_into_legacy_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "dist"
            destination = root / "dist"
            source.mkdir(parents=True)
            destination.mkdir()
            (source / "index.html").write_text("built\n", encoding="utf-8")
            for name in ("README.md", "package-lock.json", "package.json"):
                (destination / name).write_text(f"{name}\n", encoding="utf-8")
            (root / "license.txt").write_text("BSD\n", encoding="utf-8")

            release_train.ReleaseDistributionMaterializer._copy_exact_build(
                source,
                destination,
                ["README.md", "license.txt", "package-lock.json", "package.json"],
            )

            self.assertEqual("BSD\n", (destination / "license.txt").read_text())
            self.assertEqual("built\n", (destination / "index.html").read_text())

    def test_local_ref_verification_keeps_both_sides_of_detected_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "frontend"
            root.mkdir()
            self._git(root, "init", "--quiet")
            old = root / "main.old.js"
            old.write_text("const payload = 'same bytes';\n" * 20, encoding="utf-8")
            self._git(root, "add", "main.old.js")
            self._git(
                root, "-c", "user.name=Release Test",
                "-c", "user.email=release@example.org",
                "commit", "--quiet", "-m", "source",
            )
            source = self._git(root, "rev-parse", "HEAD")
            new = root / "main.new.js"
            old.rename(new)
            new.write_text(new.read_text() + "// release\n", encoding="utf-8")
            self._git(root, "add", "--all")
            self._git(
                root, "-c", "user.name=Release Test",
                "-c", "user.email=release@example.org",
                "commit", "--quiet", "-m", "release",
            )
            commit = self._git(root, "rev-parse", "HEAD")
            creator = ReleaseRefCreator(ReleaseState(root=Path(directory) / "state"))
            task = {
                "id": "release:frontend",
                "variant": "release",
                "repository": "frontend",
                "workspace": directory,
                "branch": "release/pre-2.9.5",
                "tag": "release-2.9.5",
                "sourceRevision": source,
                "expectedFiles": {
                    "main.old.js": None,
                    "main.new.js": release_train._file_sha256(new),
                },
            }

            record = creator._verify_commit(root, task, commit)

            self.assertEqual(["main.new.js", "main.old.js"], record["changedFiles"])


class ReleaseRemoteIntegrationTest(unittest.TestCase):
    @staticmethod
    def _bare_remote(base, repository, source, source_revision):
        remote = base / "remotes" / f"{repository}.git"
        remote.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
        subprocess.run([
            "git", "-C", str(source), "push", "--quiet", str(remote),
            f"{source_revision}:refs/heads/main",
            f"{source_revision}:refs/heads/develop",
        ], check=True)
        return remote

    def make_integrated_release(self, directory):
        state, creator, cedar_home, _release_workspace, _next_workspace = (
            ReleaseLocalRefsTest().make_release(directory)
        )
        create_active_release_refs(state, creator)
        # Snapshot publication now sits between the local refs and the remotes; this test
        # is about what integration writes, so it stands in for that stage rather than
        # deploying to a registry.
        state.update_current_manifest({"phase": "snapshots-published"})
        manifest, _ = state.read_current_manifest()
        remotes = {
            repository: self._bare_remote(
                Path(directory), repository, cedar_home / repository, revision,
            )
            for repository, revision in manifest["sourceRepositories"].items()
        }
        integrator = ReleaseRemoteIntegrator(
            state,
            remote_resolver=lambda repository: str(remotes[repository]),
            environment={
                "CEDAR_HOME": str(cedar_home),
                "CEDAR_RELEASE_GIT_NAME": "CEDAR Release Test",
                "CEDAR_RELEASE_GIT_EMAIL": "release-test@metadatacenter.org",
            },
        )
        completed = integrate_active_release(state, integrator)
        return state, integrator, remotes, completed

    def test_integrates_exact_release_and_next_trees_into_main_and_develop(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _integrator, remotes, completed = self.make_integrated_release(directory)

            self.assertEqual("remote-integrated", completed["phase"])
            records = completed["remoteIntegration"]["completedTasks"]
            self.assertEqual({"repo-main", "cedar-workspace"}, set(records))
            for repository, record in records.items():
                main = ReleaseLocalRefsTest._git(
                    remotes[repository], "rev-parse", "refs/heads/main",
                )
                develop = ReleaseLocalRefsTest._git(
                    remotes[repository], "rev-parse", "refs/heads/develop",
                )
                self.assertEqual(record["main"]["commit"], main)
                self.assertEqual(record["develop"]["commit"], develop)
                main_tree = ReleaseLocalRefsTest._git(
                    remotes[repository], "rev-parse", f"{main}^{{tree}}",
                )
                develop_tree = ReleaseLocalRefsTest._git(
                    remotes[repository], "rev-parse", f"{develop}^{{tree}}",
                )
                self.assertEqual(record["main"]["tree"], main_tree)
                self.assertEqual(record["develop"]["tree"], develop_tree)

            resumed = integrate_active_release(state, _integrator)
            self.assertEqual(records, resumed["remoteIntegration"]["completedTasks"])

    @staticmethod
    def _commit_on_remote_main(remote, relative, content, message, branch="main"):
        with tempfile.TemporaryDirectory() as work:
            clone = Path(work) / "clone"
            subprocess.run(["git", "clone", "--quiet", str(remote), str(clone)], check=True)
            subprocess.run(["git", "-C", str(clone), "switch", "--quiet", branch], check=True)
            target = clone / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "-C", str(clone), "add", relative], check=True)
            subprocess.run([
                "git", "-c", "user.name=Main Only",
                "-c", "user.email=main-only@metadatacenter.org",
                "-C", str(clone), "commit", "--quiet", "-m", message,
            ], check=True)
            subprocess.run([
                "git", "-C", str(clone), "push", "--quiet", "origin", branch,
            ], check=True)

    def test_main_only_content_does_not_survive_into_the_released_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            state, creator, cedar_home, _release_workspace, _next_workspace = (
                ReleaseLocalRefsTest().make_release(directory)
            )
            create_active_release_refs(state, creator)
            state.update_current_manifest({"phase": "snapshots-published"})
            manifest, _ = state.read_current_manifest()
            remotes = {
                repository: self._bare_remote(
                    Path(directory), repository, cedar_home / repository, revision,
                )
                for repository, revision in manifest["sourceRepositories"].items()
            }
            for remote in remotes.values():
                self._commit_on_remote_main(
                    remote,
                    "license.txt",
                    "Copyright (c) 2026, The Board of Trustees\n",
                    "Update copyright year to 2026",
                )
            integrator = ReleaseRemoteIntegrator(
                state,
                remote_resolver=lambda repository: str(remotes[repository]),
                environment={
                    "CEDAR_HOME": str(cedar_home),
                    "CEDAR_RELEASE_GIT_NAME": "CEDAR Release Test",
                    "CEDAR_RELEASE_GIT_EMAIL": "release-test@metadatacenter.org",
                },
            )
            completed = integrate_active_release(state, integrator)

            self.assertEqual("remote-integrated", completed["phase"])
            records = completed["remoteIntegration"]["completedTasks"]
            self.assertEqual({"repo-main", "cedar-workspace"}, set(records))
            for repository, record in records.items():
                main = ReleaseLocalRefsTest._git(
                    remotes[repository], "rev-parse", "refs/heads/main",
                )
                main_tree = ReleaseLocalRefsTest._git(
                    remotes[repository], "rev-parse", f"{main}^{{tree}}",
                )
                self.assertEqual(record["main"]["tree"], main_tree)
                listing = ReleaseLocalRefsTest._git(
                    remotes[repository], "ls-tree", "--name-only", main,
                ).split()
                self.assertNotIn("license.txt", listing)

    def _surveyor(self, directory):
        state, _creator, cedar_home, _rw, _nw = (
            ReleaseLocalRefsTest().make_release(directory)
        )
        manifest, _ = state.read_current_manifest()
        remotes = {
            repository: self._bare_remote(
                Path(directory), repository, cedar_home / repository, revision,
            )
            for repository, revision in manifest["sourceRepositories"].items()
        }
        integrator = ReleaseRemoteIntegrator(
            state,
            remote_resolver=lambda repository: str(remotes[repository]),
            environment={
                "CEDAR_HOME": str(cedar_home),
                "CEDAR_RELEASE_GIT_NAME": "CEDAR Release Test",
                "CEDAR_RELEASE_GIT_EMAIL": "release-test@metadatacenter.org",
            },
        )
        return manifest, remotes, integrator

    def test_survey_reports_main_only_content_before_any_build(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, remotes, integrator = self._surveyor(directory)
            self.assertEqual({}, integrator.survey(manifest))
            for repository in manifest["releaseRepositories"]:
                self._commit_on_remote_main(
                    remotes[repository],
                    "license.txt",
                    "Copyright (c) 2026, The Board of Trustees\n",
                    "Update copyright year to 2026",
                )
            findings = integrator.survey(manifest)
            self.assertEqual(set(manifest["releaseRepositories"]), set(findings))
            for paths in findings.values():
                self.assertEqual(["license.txt"], paths)

    def test_survey_rejects_develop_that_left_the_train_source(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, remotes, integrator = self._surveyor(directory)
            repository = manifest["releaseRepositories"][0]
            self._commit_on_remote_main(
                remotes[repository],
                "drift.txt",
                "moved on\n",
                "Advance develop past the train source",
                branch="develop",
            )
            with self.assertRaises(ReleaseError) as raised:
                integrator.survey(manifest)
            self.assertIn("develop advanced beyond train source", str(raised.exception))

    def test_integration_leaves_the_release_workspace_on_the_main_commit(self):
        """Publication packs from the workspace's checked-out commit and refuses any other."""
        with tempfile.TemporaryDirectory() as directory:
            _state, _integrator, _remotes, completed = self.make_integrated_release(directory)
            release_workspace = Path(completed["frontendPreparation"]["workspace"])
            # repo-main carries a separate next-development workspace, so its release
            # workspace keeps the main integration checkout. cedar-workspace integrates both
            # variants in one directory and publishes through its own path, so it is excluded.
            record = completed["remoteIntegration"]["completedTasks"]["repo-main"]
            root = release_workspace / "repo-main"
            self.assertEqual(
                record["main"]["commit"],
                ReleaseLocalRefsTest._git(root, "rev-parse", "HEAD"),
            )
            self.assertEqual(
                record["main"]["tree"],
                ReleaseLocalRefsTest._git(root, "rev-parse", "HEAD^{tree}"),
            )

    def test_changed_remote_ref_blocks_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            state, integrator, remotes, completed = self.make_integrated_release(directory)
            record = completed["remoteIntegration"]["completedTasks"]["repo-main"]
            subprocess.run([
                "git", "--git-dir", str(remotes["repo-main"]), "update-ref",
                "refs/heads/develop", record["sourceRevision"],
            ], check=True)
            state.update_current_manifest({"phase": "remote-integration-failed"})
            with self.assertRaisesRegex(ReleaseError, "remote refs changed"):
                integrate_active_release(state, integrator)


class ReleaseNexusInventorySearchTest(unittest.TestCase):
    """Nexus indexes snapshots under a timestamped version, releases under the version."""

    class _Http:
        def __init__(self):
            self.urls = []

        def read_json(self, url, missing_ok=False):
            self.urls.append(url)
            return ({"items": [{"name": "cedar-core-library"}]}, b"{}")

    def _publisher(self, http):
        return ReleaseArtifactPublisher(
            ReleaseState(root=Path(tempfile.gettempdir()) / "unused-state"),
            http=http, sleeper=lambda _seconds: None,
        )

    def test_snapshot_inventory_searches_the_base_version(self):
        http = self._Http()
        found = self._publisher(http)._nexus_artifacts(
            "https://nexus.example/repository/snapshots/", "2.9.4-SNAPSHOT",
        )
        self.assertEqual({"cedar-core-library"}, found)
        self.assertIn("maven.baseVersion=2.9.4-SNAPSHOT", http.urls[0])
        self.assertNotIn("&version=", http.urls[0])

    def test_release_inventory_searches_the_version(self):
        http = self._Http()
        found = self._publisher(http)._nexus_artifacts(
            "https://nexus.example/repository/releases/", "2.9.3",
        )
        self.assertEqual({"cedar-core-library"}, found)
        self.assertIn("version=2.9.3", http.urls[0])
        self.assertNotIn("maven.baseVersion", http.urls[0])


class ReleaseMavenSettingsCredentialsTest(unittest.TestCase):
    @staticmethod
    def _write_settings(directory, username="settings-user", password="settings-secret"):
        settings = Path(directory) / ".m2" / "settings.xml"
        settings.parent.mkdir()
        settings.write_text(
            """<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <servers>
    <server>
      <id>unrelated</id><username>wrong</username><password>wrong</password>
    </server>
    <server>
      <id>bmir-nexus-releases</id>
      <username>{username}</username><password>{password}</password>
    </server>
  </servers>
</settings>
""".format(username=username, password=password),
            encoding="utf-8",
        )

    def test_namespaced_maven_settings_supply_nexus_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_settings(directory)
            environment = release_train._environment_with_nexus_credentials({
                "HOME": directory,
            })

        self.assertEqual("settings-user", environment["BMIR_NEXUS_USERNAME"])
        self.assertEqual("settings-secret", environment["BMIR_NEXUS_PASSWORD"])

    def test_explicit_environment_credentials_take_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_settings(directory)
            environment = release_train._environment_with_nexus_credentials({
                "HOME": directory,
                "BMIR_NEXUS_USERNAME": "environment-user",
                "BMIR_NEXUS_PASSWORD": "environment-secret",
            })

        self.assertEqual("environment-user", environment["BMIR_NEXUS_USERNAME"])
        self.assertEqual("environment-secret", environment["BMIR_NEXUS_PASSWORD"])

    def test_http_authentication_uses_maven_settings_without_exposing_the_password(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_settings(directory)
            client = release_train.HttpClient(environment={"HOME": directory})
            headers = client._headers(
                "https://nexus.bmir.stanford.edu/repository/releases/artifact.jar")

        expected = base64.b64encode(b"settings-user:settings-secret").decode()
        self.assertEqual({"Authorization": f"Basic {expected}"}, headers)


class ReleaseArtifactPublicationTest(unittest.TestCase):
    @staticmethod
    def _publication_plan():
        return {
            "maven": {
                "releaseRepository": "https://nexus.example/repository/releases/",
                "nextDevelopmentRepository": "https://nexus.example/repository/snapshots/",
                "requiredArtifacts": ["repo-main"],
            },
            "npm": {
                "registry": "https://nexus.example/repository/npm/",
                "surfaces": [{
                    "id": "main", "repository": "repo-main", "directory": ".",
                }],
            },
        }

    def make_release(self, directory):
        state, integrator, remotes, completed = (
            ReleaseRemoteIntegrationTest().make_integrated_release(directory)
        )
        state.update_current_manifest({
            "publicationPlan": self._publication_plan(),
            "mavenPhases": [{"name": "main", "repository": "repo-main"}],
        })
        manifest, _ = state.read_current_manifest()
        return state, integrator, remotes, manifest

    def test_publication_plan_uses_release_git_provenance_and_only_stable_npm(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _integrator, _remotes, manifest = self.make_release(directory)
            publisher = ReleaseArtifactPublisher(state, executor=lambda *_args: {})
            tasks = publisher.tasks(manifest)
            identifiers = [task["id"] for task in tasks]
            self.assertEqual([
                "maven:release:publish",
                "maven:release:verify",
                "npm:release:main",
            ], identifiers)
            # The snapshots are their own plan because they are deployed before the remotes
            # are integrated, so they cannot be bound to the integration record.
            self.assertEqual([
                "maven:nextDevelopment:main",
                "maven:nextDevelopment:verify",
            ], [task["id"] for task in publisher.snapshot_tasks(manifest)])
            npm_task = tasks[2]
            integration = manifest["remoteIntegration"]["completedTasks"]["repo-main"]
            self.assertEqual(integration["main"]["commit"], npm_task["expectedCommit"])
            self.assertNotIn("npm:nextDevelopment", " ".join(identifiers))

    def test_failed_publication_resumes_after_verified_completed_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            state, integrator, _remotes, manifest = self.make_release(directory)
            calls = []

            class FakePublisher:
                @staticmethod
                def ensure_nexus_ready(_purpose):
                    return None

                @staticmethod
                def tasks(_manifest):
                    return [
                        {"id": "one", "kind": "fake"},
                        {"id": "two", "kind": "fake"},
                        {"id": "three", "kind": "fake"},
                    ]

                @staticmethod
                def snapshot_tasks(_manifest):
                    return []

                def run_task(self, _manifest, task):
                    calls.append(task["id"])
                    if task["id"] == "two" and calls.count("two") == 1:
                        raise ReleaseError("registry unavailable")
                    return {**task, "proof": task["id"]}

                @staticmethod
                def verify_record(_manifest, record, _tasks=None):
                    if record.get("proof") != record.get("id"):
                        raise ReleaseError("publication proof changed")

            publisher = FakePublisher()
            with self.assertRaisesRegex(ReleaseError, "registry unavailable"):
                publish_active_release(
                    state, publisher, remote_integrator=integrator,
                )
            failed, _ = state.read_current_manifest()
            self.assertEqual("artifact-publication-failed", failed["phase"])
            self.assertEqual({"one"}, set(failed["artifactPublication"]["completedTasks"]))

            completed = publish_active_release(
                state, publisher, remote_integrator=integrator,
            )
            self.assertEqual("artifacts-published", completed["phase"])
            self.assertEqual(["one", "two", "two", "three"], calls)

    def test_old_ledger_snapshot_records_are_classified_by_task_identity(self):
        class FakePublisher:
            @staticmethod
            def tasks(_manifest):
                return [{"id": "stable", "kind": "fake"}]

            @staticmethod
            def snapshot_tasks(_manifest):
                return [{"id": "maven:nextDevelopment:verify", "kind": "fake"}]

        manifest = {
            "artifactPublication": {
                "completedTasks": {
                    "stable": {"id": "stable", "kind": "fake"},
                    "maven:nextDevelopment:verify": {
                        "id": "maven:nextDevelopment:verify", "kind": "fake"},
                },
            },
        }

        _release_tasks, releases, _snapshot_tasks, snapshots = (
            release_train._publication_evidence_by_plan(
                manifest, FakePublisher(), require_complete=True))

        self.assertEqual(["stable"], [record["id"] for record in releases])
        self.assertEqual(
            ["maven:nextDevelopment:verify"],
            [record["id"] for record in snapshots],
        )

    def test_nexus_guard_opens_before_publication_changes_the_ledger(self):
        class OpenCircuit:
            @staticmethod
            def ensure_nexus_ready(_purpose):
                raise ReleaseError("Nexus circuit breaker is open")

        with tempfile.TemporaryDirectory() as directory:
            state, integrator, _remotes, _manifest = self.make_release(directory)
            before, path = state.read_current_manifest()
            before_bytes = path.read_bytes()

            with self.assertRaisesRegex(ReleaseError, "circuit breaker is open"):
                publish_active_release(
                    state, OpenCircuit(), remote_integrator=integrator)

            after, _ = state.read_current_manifest()
            self.assertEqual(before, after)
            self.assertEqual(before_bytes, path.read_bytes())

    def test_maven_release_upload_accepts_only_identical_existing_bytes(self):
        class ExistingHttp:
            def __init__(self, content):
                self.content = content

            def read(self, _url, missing_ok=False):
                return self.content

        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "repository"
            artifact = local / "org/metadatacenter/repo-main/2.9.3/repo-main-2.9.3.jar"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"release bytes")
            task = {
                "id": "maven:release:publish",
                "kind": "maven-release-upload",
                "variant": "release",
                "version": "2.9.3",
                "repository": "https://nexus.example/repository/releases/",
                "localRepository": str(local),
            }
            publisher = ReleaseArtifactPublisher(
                ReleaseState(root=Path(directory) / "state"),
                http=ExistingHttp(b"release bytes"),
            )
            record = publisher._publish_maven_release(task)
            self.assertEqual(
                hashlib.sha256(b"release bytes").hexdigest(),
                record["files"]["org/metadatacenter/repo-main/2.9.3/repo-main-2.9.3.jar"],
            )
            publisher.http = ExistingHttp(b"different")
            with self.assertRaisesRegex(ReleaseError, "different bytes"):
                publisher._publish_maven_release(task)

    def test_maven_release_upload_reports_every_file_and_its_disposition(self):
        class MixedHttp:
            @staticmethod
            def read(url, missing_ok=False):
                return b"first" if url.endswith("repo-main-2.9.3.jar") else None

        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "repository"
            version = local / "org/metadatacenter/repo-main/2.9.3"
            version.mkdir(parents=True)
            (version / "repo-main-2.9.3.jar").write_bytes(b"first")
            (version / "repo-main-2.9.3.pom").write_bytes(b"second")
            task = {
                "id": "maven:release:publish",
                "kind": "maven-release-upload",
                "variant": "release",
                "version": "2.9.3",
                "repository": "https://nexus.example/repository/releases/",
                "localRepository": str(local),
            }
            progress = []
            uploads = []
            publisher = ReleaseArtifactPublisher(
                ReleaseState(root=Path(directory) / "state"),
                http=MixedHttp(), progress_reporter=progress.append,
            )
            publisher._upload = lambda destination, content: uploads.append(
                (destination, content))

            with release_train.console.capture() as capture:
                record = publisher._publish_maven_release(task)

        self.assertEqual([0, 1, 2], [item["completedFiles"] for item in progress])
        self.assertEqual(2, progress[-1]["totalFiles"])
        self.assertEqual(1, progress[-1]["uploadedFiles"])
        self.assertEqual(1, progress[-1]["existingFiles"])
        self.assertEqual(1, record["uploadedFiles"])
        self.assertEqual(1, record["existingFiles"])
        self.assertEqual(1, len(uploads))
        self.assertIn("2/2 files", capture.get())

    def test_in_progress_maven_file_count_is_checkpointed_and_rendered(self):
        class ProgressThenFailure:
            progress_reporter = None

            @staticmethod
            def ensure_nexus_ready(_purpose):
                return None

            @staticmethod
            def tasks(_manifest):
                return [{
                    "id": "maven:release:publish",
                    "kind": "maven-release-upload",
                }]

            @staticmethod
            def snapshot_tasks(_manifest):
                return []

            @staticmethod
            def verify_record(_manifest, _record, _tasks=None):
                return None

            def run_task(self, _manifest, _task):
                self.progress_reporter({
                    "id": "maven:release:publish",
                    "kind": "maven-release-upload",
                    "completedFiles": 47,
                    "totalFiles": 126,
                    "uploadedFiles": 40,
                    "existingFiles": 7,
                    "currentFile": "org/metadatacenter/cedar-parent/2.9.3/cedar-parent.pom",
                    "updatedAt": "2026-09-01T12:00:00+00:00",
                })
                raise ReleaseError("connection reset")

        with tempfile.TemporaryDirectory() as directory:
            state, integrator, _remotes, _manifest = self.make_release(directory)
            external_progress = []
            publisher = ProgressThenFailure()
            publisher.progress_reporter = external_progress.append
            for _attempt in range(2):
                with self.assertRaisesRegex(ReleaseError, "connection reset"):
                    publish_active_release(
                        state, publisher, remote_integrator=integrator,
                    )
            manifest, path = state.read_current_manifest()
            with release_train.console.capture() as capture:
                release_train._render_release_status(manifest, path)

        self.assertEqual(2, len(external_progress))
        progress = manifest["artifactPublication"]["inProgressTask"]
        self.assertEqual(47, progress["completedFiles"])
        self.assertIn("Maven files 47/126", capture.get())
        self.assertIn("Current Maven file", capture.get())
        self.assertIn("uploaded 40, already present 7", capture.get())

    def test_npm_registry_verification_requires_exact_tarball_and_git_commit(self):
        package = {
            "name": "cedar-frontend",
            "version": "2.9.3",
            "gitHead": "a" * 40,
        }
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            content = json.dumps(package).encode()
            info = tarfile.TarInfo("package/package.json")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        tarball = stream.getvalue()
        tarball_url = "https://nexus.example/cedar-frontend.tgz"
        package_url = "https://nexus.example/repository/npm/cedar-frontend"
        package_integrity = integrity(tarball)

        class RegistryHttp:
            def read_json(self, url, missing_ok=False):
                self.last_url = url
                return ({"versions": {"2.9.3": {
                    "dist": {"tarball": tarball_url, "integrity": package_integrity},
                }}}, b"{}")

            def read(self, url, missing_ok=False):
                self.last_url = url
                return tarball

        task = {
            "id": "npm:release:main",
            "kind": "npm-release",
            "version": "2.9.3",
            "registry": "https://nexus.example/repository/npm/",
            "expectedCommit": "a" * 40,
        }
        evidence = {
            "name": "cedar-frontend",
            "integrity": package_integrity,
            "tarballSha256": hashlib.sha256(tarball).hexdigest(),
        }
        publisher = ReleaseArtifactPublisher(
            ReleaseState(root=Path(tempfile.gettempdir()) / "unused-state"),
            http=RegistryHttp(), sleeper=lambda _seconds: None,
        )
        verified = publisher._verify_npm_package(task, evidence, wait=False)
        self.assertEqual(tarball_url, verified["tarball"])
        bad = {**evidence, "tarballSha256": "0" * 64}
        with self.assertRaisesRegex(ReleaseError, "tarball differs"):
            publisher._verify_npm_package(task, bad, wait=False)

    def test_npm_pack_is_archived_from_the_integrated_commit_with_git_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            root = workspace / "frontend"
            root.mkdir(parents=True)
            (root / "package.json").write_text(json.dumps({
                "name": "cedar-frontend",
                "version": "2.9.3",
                "publishConfig": {"registry": "https://nexus.example/repository/npm/"},
            }) + "\n", encoding="utf-8")
            (root / "README.md").write_text("frontend\n", encoding="utf-8")
            runtime = root / "node_modules" / "cedar-runtime" / "runtime.js"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("runtime bytes\n", encoding="utf-8")
            commit = ReleaseWorkspaceTest._commit_repository(root)
            tree = ReleaseLocalRefsTest._git(root, "rev-parse", "HEAD^{tree}")
            task = {
                "id": "npm:release:frontend",
                "kind": "npm-release",
                "repository": "frontend",
                "directory": ".",
                "version": "2.9.3",
                "registry": "https://nexus.example/repository/npm/",
                "workspace": str(workspace),
                "expectedCommit": commit,
                "expectedTree": tree,
                "packedRuntimeDirectories": ["node_modules"],
            }
            publisher = ReleaseArtifactPublisher(
                ReleaseState(root=Path(directory) / "state"),
            )
            tarball, evidence = publisher._pack_npm(task)
            package = publisher._npm_tarball_package("packed frontend", tarball.read_bytes())
            self.assertEqual("cedar-frontend", evidence["name"])
            self.assertEqual(commit, package["gitHead"])
            self.assertEqual("2.9.3", package["version"])
            self.assertEqual(
                release_train._file_sha256(runtime),
                evidence["runtimeFiles"]["node_modules/cedar-runtime/runtime.js"],
            )
            publisher._verify_npm_tarball_files(
                "packed frontend", tarball.read_bytes(), evidence["runtimeFiles"],
            )


class FakeNexus:
    """Stand in for HttpClient, failing only the URLs a test names."""

    def __init__(self, failing=frozenset(), status=500):
        self.failing = set(failing)
        self.status = status
        self.reads = []

    def read(self, url, *, missing_ok=False):
        self.reads.append(url)
        if url in self.failing:
            raise ReleaseError(f"cannot read {url}: HTTP {self.status}")
        return b"{}"

    def read_json(self, url, *, missing_ok=False):
        return {}, b"{}"


class NexusCircuitBreakerTest(unittest.TestCase):
    def test_healthy_gate_uses_only_status_and_one_real_repository_read(self):
        http = FakeNexus()
        guard = release_train.NexusCircuitBreaker(http, PREFLIGHT_ENVIRONMENT)

        guard.require("artifact publication")

        self.assertEqual([
            release_train.NEXUS_WRITABLE_ENDPOINT,
            release_train.NEXUS_REPOSITORY_PROBE,
        ], http.reads)

    def test_repository_http_failure_opens_without_becoming_retryable(self):
        http = FakeNexus(
            failing={release_train.NEXUS_REPOSITORY_PROBE}, status=500)
        guard = release_train.NexusCircuitBreaker(http, PREFLIGHT_ENVIRONMENT)

        with self.assertRaisesRegex(ReleaseError, "daily request budget") as raised:
            guard.require("release acceptance")

        self.assertNotIsInstance(raised.exception, RetryableReleaseError)
        self.assertEqual(2, len(http.reads))

    def test_direct_connection_failure_remains_bounded_retry_material(self):
        class OfflineNexus(FakeNexus):
            def read(self, url, *, missing_ok=False):
                self.reads.append(url)
                raise RetryableReleaseError("connection reset")

        with self.assertRaises(RetryableReleaseError):
            release_train.NexusCircuitBreaker(
                OfflineNexus(), PREFLIGHT_ENVIRONMENT).require("snapshot publication")


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeCommands:
    """Answer commands by the first matching prefix, so a check drives real argument lists."""

    def __init__(self, answers=None, default=FakeCompletedProcess()):
        self.answers = answers or {}
        self.default = default
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        for prefix, answer in self.answers.items():
            if list(args)[:len(prefix)] == list(prefix):
                return answer
        return self.default


PREFLIGHT_ENVIRONMENT = {
    "PATH": os.environ.get("PATH", ""),
    "CEDAR_HOME": "/tmp/cedar-preflight",
    "CEDAR_HOST": "metadatacenter.orgx",
    "CEDAR_DEVELOP_HOME": "/tmp/cedar-preflight/cedar-development",
    "CEDAR_NET_GATEWAY": "10.0.0.1",
    "CEDAR_FRONTEND_TARGET": "native",
    "BMIR_NEXUS_USERNAME": "releaser",
    "BMIR_NEXUS_PASSWORD": "secret",
}


class ReleasePreflightTest(unittest.TestCase):
    """Every check answers a question that once cost a release its build phase."""

    def _preflight(self, *, environment=None, commands=None, http=None,
                   manifest=None, root=None, accepted=None):
        values = dict(PREFLIGHT_ENVIRONMENT)
        if root is not None:
            values["CEDAR_HOME"] = str(root)
        values.update(environment or {})
        payload = manifest or manifest_fixture()
        return ReleasePreflight(
            payload,
            state=ReleaseState(root=Path(tempfile.gettempdir()) / "preflight-state"),
            command_runner=commands or FakeCommands(),
            http=http or FakeNexus(),
            environment=values,
            accepted_red_develop=accepted,
        )

    @staticmethod
    def _checked_out(directory, repository, *, files=None):
        root = Path(directory) / repository
        root.mkdir(parents=True, exist_ok=True)
        (root / "license.txt").write_text(
            "Copyright (c) 2026, The Board of Trustees\n", encoding="utf-8")
        for relative, content in (files or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    def test_java_other_than_seventeen_is_refused(self):
        commands = FakeCommands({
            ("java", "-version"): FakeCompletedProcess(
                stderr='openjdk version "25.0.1" 2025-10-21'),
        })
        findings = self._preflight(commands=commands).check_toolchain()

        self.assertTrue(findings)
        self.assertIn("Java 25", findings[0].message)
        self.assertIn("java_home -v 17", findings[0].remedy)

    def test_java_seventeen_passes(self):
        commands = FakeCommands({
            ("java", "-version"): FakeCompletedProcess(
                stderr='openjdk version "17.0.13" 2024-10-15'),
            ("node", "--version"): FakeCompletedProcess(stdout="v24.19.0"),
        })
        self.assertEqual([], self._preflight(commands=commands).check_toolchain())

    def test_node_other_than_the_release_pin_is_refused(self):
        commands = FakeCommands({
            ("java", "-version"): FakeCompletedProcess(
                stderr='openjdk version "17.0.13" 2024-10-15'),
            ("node", "--version"): FakeCompletedProcess(stdout="v22.22.0"),
        })
        findings = self._preflight(commands=commands).check_toolchain()

        self.assertEqual(1, len(findings))
        self.assertIn("v24.19.0", findings[0].message)

    def test_an_unsourced_profile_is_named_variable_by_variable(self):
        findings = self._preflight(
            environment={"CEDAR_DEVELOP_HOME": "", "CEDAR_NET_GATEWAY": ""}).check_profile()

        self.assertEqual(1, len(findings))
        self.assertIn("CEDAR_DEVELOP_HOME", findings[0].message)
        self.assertIn("CEDAR_NET_GATEWAY", findings[0].message)
        self.assertIn("cedar-profile-native.sh", findings[0].remedy)

    def test_preflight_covers_independent_cee_consumers_that_receive_refs(self):
        manifest = self._release_of("repo-one")
        manifest["cee"]["consumers"] = [{
            "repository": "repo-two", "manifest": "package.json", "lock": "package-lock.json",
        }]

        self.assertEqual(
            ["repo-one", "repo-two"],
            self._preflight(manifest=manifest).repositories,
        )

    def test_missing_git_author_identity_blocks_before_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = self._release_of("repo-one")
            findings = self._preflight(
                manifest=manifest, root=directory,
                commands=FakeCommands(),
            ).check_git_identity()

        self.assertEqual(1, len(findings))
        self.assertIn("Git author", findings[0].message)

    def test_absent_nexus_credentials_fail_before_any_build(self):
        findings = self._preflight(
            environment={"BMIR_NEXUS_PASSWORD": ""}).check_nexus_authorization()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("anonymous", findings[0].message)

    def test_maven_settings_credentials_satisfy_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            ReleaseMavenSettingsCredentialsTest._write_settings(directory)
            preflight = self._preflight(environment={
                "HOME": directory,
                "BMIR_NEXUS_USERNAME": "",
                "BMIR_NEXUS_PASSWORD": "",
            })
            findings = preflight.check_nexus_authorization()

        self.assertEqual([], findings)
        self.assertEqual("settings-user", preflight.environment["BMIR_NEXUS_USERNAME"])
        self.assertEqual("settings-secret", preflight.environment["BMIR_NEXUS_PASSWORD"])

    def test_nexus_credentials_that_do_not_authenticate_fail(self):
        findings = self._preflight(
            http=FakeNexus(failing={NEXUS_AUTHENTICATED_ENDPOINT}, status=401),
        ).check_nexus_authorization()

        self.assertEqual(1, len(findings))
        self.assertIn("does not authenticate", findings[0].message)

    def test_authenticated_writable_nexus_passes(self):
        self.assertEqual([], self._preflight().check_nexus_authorization())

    def test_a_non_writable_nexus_blocks_even_when_repository_reads_work(self):
        findings = self._preflight(http=FakeNexus(
            failing={release_train.NEXUS_WRITABLE_ENDPOINT}, status=503,
        )).check_nexus_authorization()

        self.assertEqual(1, len(findings))
        self.assertIn("not writable", findings[0].message)

    def test_a_healthy_nexus_passes(self):
        self.assertEqual([], self._preflight().check_nexus_authorization())

    def test_repositories_failing_while_status_holds_is_named_as_the_request_budget(self):
        """This is the failure that gets worse the harder a release tries, so it is named."""
        findings = self._preflight(
            http=FakeNexus(failing={release_train.NEXUS_REPOSITORY_PROBE}),
        ).check_nexus_authorization()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("daily request budget", findings[0].message)
        self.assertIn("Usage Center", findings[0].remedy)

    def test_a_repository_that_cannot_be_read_blocks_even_when_status_also_fails(self):
        findings = self._preflight(http=FakeNexus(failing={
            release_train.NEXUS_REPOSITORY_PROBE,
            release_train.NEXUS_WRITABLE_ENDPOINT,
            release_train.NEXUS_AUTHENTICATED_ENDPOINT,
        })).check_nexus_authorization()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("cannot serve a repository read", findings[0].message)

    def test_status_endpoints_alone_no_longer_decide_the_check(self):
        """They stayed green through a total outage, so they cannot be the whole answer."""
        source = inspect.getsource(ReleasePreflight.check_nexus_authorization)
        self.assertIn("NEXUS_REPOSITORY_PROBE", source)

    def test_npm_without_a_registry_identity_fails(self):
        commands = FakeCommands({
            ("npm", "whoami"): FakeCompletedProcess(returncode=1, stderr="ENEEDAUTH"),
        })
        findings = self._preflight(commands=commands).check_npm_authorization()

        self.assertEqual(1, len(findings))
        self.assertIn("npm login", findings[0].remedy)

    def test_a_remote_that_refuses_main_is_found_before_the_build(self):
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["repo-one"]
            manifest["sourceRepositories"] = {"repo-one": "a" * 40}
            commands = FakeCommands({
                ("git",): FakeCompletedProcess(
                    returncode=1,
                    stderr="remote: error: GH006: Protected branch update failed"),
            })
            findings = self._preflight(
                commands=commands, manifest=manifest, root=directory).check_push_permission()

        self.assertEqual(1, len(findings))
        self.assertIn("Protected branch", findings[0].message)
        self.assertIn("branch protection", findings[0].remedy)

    def test_push_permission_asks_about_main_and_the_release_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["repo-one"]
            manifest["sourceRepositories"] = {"repo-one": "a" * 40}
            commands = FakeCommands()
            self._preflight(
                commands=commands, manifest=manifest, root=directory).check_push_permission()

        self.assertEqual(1, len(commands.calls))
        pushed = commands.calls[0]
        self.assertIn("--dry-run", pushed)
        self.assertIn("a" * 40 + ":refs/heads/main", pushed)
        self.assertIn("a" * 40 + ":refs/heads/develop", pushed)
        self.assertIn("a" * 40 + ":refs/heads/release/pre-2.9.3", pushed)
        self.assertIn("a" * 40 + ":refs/heads/release/post-2.9.4-SNAPSHOT", pushed)
        self.assertIn("a" * 40 + ":refs/tags/release-2.9.3", pushed)

    def test_an_already_used_release_tag_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["repo-one"]
            commands = FakeCommands({
                ("git", "-C"): FakeCompletedProcess(
                    stdout="abc123\trefs/tags/release-2.9.3"),
            })
            findings = self._preflight(
                commands=commands, manifest=manifest,
                root=directory).check_target_version_unused()

        self.assertEqual(1, len(findings))
        self.assertIn("already carries release-2.9.3", findings[0].message)

    def test_existing_maven_and_npm_release_versions_are_refused(self):
        class OccupiedRegistry(FakeNexus):
            def read_json(self, url, *, missing_ok=False):
                if "/service/rest/v1/search?" in url:
                    return {"items": [{"name": "cedar-parent"}]}, b"{}"
                return {"versions": {"2.9.3": {"name": "cedar-ui"}}}, b"{}"

        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = self._release_of("repo-one")
            manifest["publicationPlan"] = {"npm": {
                "registry": release_train.NEXUS_NPM_REGISTRY,
                "surfaces": [{"repository": "repo-one", "directory": "."}],
            }}
            commands = FakeCommands({
                ("git", "-C"): FakeCompletedProcess(
                    stdout=json.dumps({"name": "cedar-ui"})),
            })
            findings = self._preflight(
                manifest=manifest, root=directory, commands=commands,
                http=OccupiedRegistry(),
            ).check_target_artifacts_unused()

        self.assertEqual(2, len(findings))
        self.assertIn("Maven releases already contains", findings[0].message)
        self.assertIn("cedar-ui@2.9.3", findings[1].message)

    def test_missing_preserved_distribution_input_fails_source_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = self._release_of("repo-one")
            manifest["publicationPlan"] = {"npm": {
                "registry": release_train.NEXUS_NPM_REGISTRY,
                "surfaces": [{
                    "repository": "repo-one", "directory": "dist",
                    "preserveFiles": ["README.md", "package.json", "package-lock.json"],
                }],
            }}
            commands = FakeCommands({
                ("git", "-C", str(Path(directory) / "repo-one"), "ls-tree"):
                    FakeCompletedProcess(stdout="dist/package.json\ndist/package-lock.json\n"),
                ("git", "-C", str(Path(directory) / "repo-one"), "show"):
                    FakeCompletedProcess(stdout=json.dumps({
                        "name": "repo-one", "publishConfig": {
                            "registry": release_train.NEXUS_NPM_REGISTRY,
                        },
                    })),
            })
            findings = self._preflight(
                manifest=manifest, root=directory, commands=commands,
            ).check_source_contract()

        self.assertTrue(any("dist/README.md" in finding.message for finding in findings))

    @staticmethod
    def _release_of(*repositories):
        manifest = manifest_fixture()
        manifest["releaseRepositories"] = list(repositories)
        manifest["sourceRepositories"] = {name: "a" * 40 for name in repositories}
        return manifest

    def test_ci_is_asked_about_the_train_source_rather_than_the_current_develop(self):
        """A release advances develop everywhere, so the latest run answers the wrong question."""
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(
                stdout="success\tcompleted\t1\tCI"),
        })
        self._preflight(
            commands=commands, manifest=self._release_of("repo-one")).check_develop_is_green()

        self.assertIn(f"head_sha={'a' * 40}", commands.calls[0][2])

    def test_a_source_commit_with_no_ci_run_blocks(self):
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(stdout=""),
            ("git", "-C"): FakeCompletedProcess(stdout=".github/workflows/ci.yml"),
        })
        findings = self._preflight(
            commands=commands, manifest=self._release_of("repo-one")).check_develop_is_green()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("no CI run", findings[0].message)

    def test_a_repository_without_a_ci_workflow_is_advisory(self):
        commands = FakeCommands({("gh", "api"): FakeCompletedProcess(stdout="")})
        findings = self._preflight(
            commands=commands, manifest=self._release_of("repo-one")).check_develop_is_green()

        self.assertEqual(1, len(findings))
        self.assertFalse(findings[0].fatal)
        self.assertIn("no CI workflow contract", findings[0].message)

    def test_a_still_running_workflow_blocks(self):
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(stdout="pending\tin_progress\t7\tCI"),
        })
        findings = self._preflight(
            commands=commands, manifest=self._release_of("repo-one")).check_develop_is_green()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("still in_progress", findings[0].message)

    def test_a_cancelled_run_is_advisory_rather_than_blocking(self):
        """Cancelling is something done to a workflow, not something learned about the code."""
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(
                stdout="cancelled\tcompleted\t33226052977\tBuild train"),
        })
        findings = self._preflight(
            commands=commands, manifest=self._release_of("repo-one")).check_develop_is_green()

        self.assertEqual(1, len(findings))
        self.assertFalse(findings[0].fatal)
        self.assertIn("was cancelled", findings[0].message)

    def test_a_red_develop_blocks_the_release(self):
        manifest = self._release_of("repo-one")
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(
                stdout="failure\tcompleted\t33211149320\tCI"),
        })
        findings = self._preflight(
            commands=commands, manifest=manifest).check_develop_is_green()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("--accept-red-develop repo-one=33211149320", findings[0].remedy)

    def test_a_red_develop_accepted_by_run_becomes_advisory(self):
        manifest = self._release_of("repo-one")
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(
                stdout="failure\tcompleted\t33211149320\tCI"),
        })
        findings = self._preflight(
            commands=commands, manifest=manifest,
            accepted={"repo-one": "33211149320"}).check_develop_is_green()

        self.assertEqual(1, len(findings))
        self.assertFalse(findings[0].fatal)
        self.assertIn("accepted explicitly", findings[0].message)

    def test_accepting_a_different_run_does_not_clear_the_current_one(self):
        manifest = self._release_of("repo-one")
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(
                stdout="failure\tcompleted\t33211149320\tCI"),
        })
        findings = self._preflight(
            commands=commands, manifest=manifest,
            accepted={"repo-one": "1"}).check_develop_is_green()

        self.assertTrue(findings[0].fatal)

    def test_a_green_develop_passes(self):
        manifest = self._release_of("repo-one")
        commands = FakeCommands({
            ("gh", "api"): FakeCompletedProcess(
                stdout="success\tcompleted\t33211149320\tCI"),
        })
        self.assertEqual(
            [], self._preflight(commands=commands, manifest=manifest).check_develop_is_green())

    def test_an_undeclared_generated_swagger_file_is_found_before_the_build(self):
        """This is the valuerecommender failure, reached without spending a build."""
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "cedar-undeclared-server", files={
                "cedar-undeclared-server-application/src/main/resources/assets/"
                "swagger-api/swagger.json": '{"version" : "2.9.3-SNAPSHOT"}',
            })
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["cedar-undeclared-server"]
            findings = self._preflight(
                manifest=manifest, root=directory).check_generated_version_files()

        self.assertEqual(1, len(findings))
        self.assertIn("swagger.json", findings[0].message)
        self.assertIn("MAVEN_GENERATED_VERSION_FILES", findings[0].remedy)

    def test_a_declared_generated_file_passes(self):
        repository = "cedar-resource-server"
        declared = next(iter(MAVEN_GENERATED_VERSION_FILES[repository]))
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, repository, files={declared: "{}"})
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = [repository]
            findings = self._preflight(
                manifest=manifest, root=directory).check_generated_version_files()

        self.assertEqual([], findings)

    def test_a_license_without_a_copyright_year_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._checked_out(directory, "repo-one")
            (root / "license.txt").write_text("All rights reserved.\n", encoding="utf-8")
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["repo-one"]
            findings = self._preflight(
                manifest=manifest, root=directory).check_license_files()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)

    def test_a_missing_license_is_advisory_rather_than_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._checked_out(directory, "repo-one")
            (root / "license.txt").unlink()
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["repo-one"]
            findings = self._preflight(
                manifest=manifest, root=directory).check_license_files()

        self.assertEqual(1, len(findings))
        self.assertFalse(findings[0].fatal)

    def test_a_dirty_working_tree_blocks_the_release(self):
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["repo-one"]
            commands = FakeCommands({
                ("git", "-C", str(Path(directory) / "repo-one"), "rev-parse"):
                    FakeCompletedProcess(stdout="develop"),
                ("git", "-C", str(Path(directory) / "repo-one"), "status"):
                    FakeCompletedProcess(stdout=" M pom.xml"),
            })
            findings = self._preflight(
                commands=commands, manifest=manifest, root=directory).check_working_trees()

        self.assertEqual(1, len(findings))
        self.assertIn("1 uncommitted change", findings[0].message)

    def test_untracked_files_do_not_block_a_release(self):
        """Build output is ordinary in a development tree, and the release builds from commits."""
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            root = str(Path(directory) / "repo-one")
            commands = FakeCommands({
                ("git", "-C", root, "rev-parse"): FakeCompletedProcess(stdout="develop"),
                ("git", "-C", root, "status"): FakeCompletedProcess(stdout=""),
                ("git", "-C", root, "rev-list"): FakeCompletedProcess(stdout="0"),
            })
            findings = self._preflight(
                commands=commands, manifest=self._release_of("repo-one"),
                root=directory).check_working_trees()

        self.assertEqual([], findings)
        status = next(call for call in commands.calls if "status" in call)
        self.assertIn("--untracked-files=no", status)

    def test_unpushed_develop_commits_block_the_release(self):
        with tempfile.TemporaryDirectory() as directory:
            self._checked_out(directory, "repo-one")
            manifest = manifest_fixture()
            manifest["releaseRepositories"] = ["repo-one"]
            root = str(Path(directory) / "repo-one")
            commands = FakeCommands({
                ("git", "-C", root, "rev-parse"): FakeCompletedProcess(stdout="develop"),
                ("git", "-C", root, "status"): FakeCompletedProcess(stdout=""),
                ("git", "-C", root, "rev-list"): FakeCompletedProcess(stdout="2"),
            })
            findings = self._preflight(
                commands=commands, manifest=manifest, root=directory).check_working_trees()

        self.assertEqual(1, len(findings))
        self.assertIn("2 unpushed commit", findings[0].message)

    def test_start_and_plan_settle_the_same_preconditions(self):
        """A release must not be startable from a state that plan would have refused."""
        for command in (release_train.plan, release_train.start):
            self.assertIn(
                "_release_gate_or_exit", inspect.getsource(command),
                f"{command.__name__} does not run the complete release gate")

    def test_resume_rechecks_conditions_for_its_recorded_stage(self):
        manifest = manifest_fixture()
        manifest["phase"] = "snapshot-publication-failed"
        preflight = self._preflight(manifest=manifest)
        called = []

        def check(name):
            def run():
                called.append(name)
                return []
            return run

        names = {
            "check_toolchain", "check_profile", "check_disk_space",
            "check_nexus_authorization", "check_npm_authorization",
            "check_push_permission", "check_target_version_unused",
            "check_target_artifacts_unused", "check_remote_survey",
        }
        with patch.multiple(preflight, **{name: check(name) for name in names}):
            self.assertEqual([], preflight.run_resume())

        self.assertEqual(names, set(called))


class ReleaseLicenseStampingTest(unittest.TestCase):
    """A release, rather than the turn of a year, is what keeps the copyright current."""

    LICENSE = (
        "Copyright (c) 2025, The Board of Trustees of Leland Stanford Junior University\n"
        "All rights reserved.\n"
        "\n"
        "Redistribution and use in source and binary forms are permitted provided that\n"
        "the above copyright notice is retained.\n"
    )

    def _repository(self, directory, text=None):
        root = Path(directory) / "repo-one"
        root.mkdir(parents=True, exist_ok=True)
        if text is not None:
            (root / "license.txt").write_text(text, encoding="utf-8")
        return root

    def test_a_stale_year_is_advanced_and_reported_as_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory, self.LICENSE)
            changed = ReleaseVersionPreparer._stamp_license(root, "2026")
            updated = (root / "license.txt").read_text(encoding="utf-8")

        self.assertEqual({"license.txt"}, changed)
        self.assertTrue(updated.startswith("Copyright (c) 2026, The Board of Trustees"))

    def test_nothing_but_the_year_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory, self.LICENSE)
            ReleaseVersionPreparer._stamp_license(root, "2026")
            updated = (root / "license.txt").read_text(encoding="utf-8")

        self.assertEqual(
            self.LICENSE.replace("2025", "2026", 1), updated,
            "the stamp rewrote something other than the copyright year")

    def test_a_year_already_current_is_left_untouched(self):
        """Reporting a change that did not happen would trip the byte-inventory guard."""
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory, self.LICENSE.replace("2025", "2026"))
            before = (root / "license.txt").read_bytes()
            changed = ReleaseVersionPreparer._stamp_license(root, "2026")

            self.assertEqual(set(), changed)
            self.assertEqual(before, (root / "license.txt").read_bytes())

    def test_an_unrecognised_licence_is_left_alone_rather_than_guessed_at(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory, "All rights reserved.\n")
            changed = ReleaseVersionPreparer._stamp_license(root, "2026")

        self.assertEqual(set(), changed)

    def test_a_repository_without_a_licence_stamps_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            self.assertEqual(set(), ReleaseVersionPreparer._stamp_license(root, "2026"))

    def test_the_licence_is_stamped_alongside_the_version_surfaces(self):
        """Every repository gets the year, whatever kind of version surface it carries."""
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory, self.LICENSE)
            (root / "pom.xml").write_text(
                "<project><version>2.9.3-SNAPSHOT</version></project>\n", encoding="utf-8")
            changed = ReleaseVersionPreparer._stamp_repository(
                "repo-one", root, "2.9.3-SNAPSHOT", "2.9.3", {"repo-one"}, "2026")

        self.assertIn("license.txt", changed)
        self.assertIn("pom.xml", changed)


class ReleaseAcceptanceTest(unittest.TestCase):
    """The release proves itself, rather than leaving an operator to prove it by hand."""

    def _published(self, directory):
        state = ReleaseState(root=Path(directory))
        manifest = manifest_fixture()
        manifest["releaseRepositories"] = ["repo-one", "repo-two"]
        state.start(manifest)
        state.update_current_manifest({
            "phase": "artifacts-published",
            "remoteIntegration": {
                "completedTasks": {
                    repository: {"id": repository, "repository": repository}
                    for repository in manifest["releaseRepositories"]
                },
            },
        })
        return state

    @staticmethod
    def _acceptance(state, *, tagged):
        class FakeRemoteIntegrator:
            def __init__(self):
                self.verified = []

            @staticmethod
            def tasks(manifest):
                return [
                    {"id": repository, "repository": repository}
                    for repository in manifest.get("releaseRepositories", [])
                ]

            def verify_record(self, manifest, record):
                self.verified.append(record["repository"])
                if record["repository"] not in tagged:
                    raise ReleaseError(
                        f"release-{manifest['releaseVersion']} is absent from "
                        f"{record['repository']}")

        class FakePublisher:
            def __init__(self):
                self.guard_calls = []
                self.verified = []

            def ensure_nexus_ready(self, purpose):
                self.guard_calls.append(purpose)

            @staticmethod
            def tasks(_manifest):
                return []

            @staticmethod
            def snapshot_tasks(_manifest):
                return []

            def verify_record(self, _manifest, record, _tasks=None):
                self.verified.append(record["id"])

        return ReleaseAcceptance(
            state,
            remote_integrator=FakeRemoteIntegrator(),
            publisher=FakePublisher(),
        )

    def test_a_repository_without_the_release_tag_fails_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._published(directory)
            acceptance = self._acceptance(state, tagged={"repo-one"})

            with self.assertRaisesRegex(ReleaseError, "release-2.9.3 is absent from repo-two"):
                accept_active_release(state, acceptance)

            manifest, _ = state.read_current_manifest()
            self.assertEqual("acceptance-failed", manifest["phase"])
            self.assertIn("repo-two", manifest["failure"])

    def test_a_release_that_holds_everywhere_is_recorded_as_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._published(directory)
            acceptance = self._acceptance(state, tagged={"repo-one", "repo-two"})

            manifest = accept_active_release(state, acceptance)

        self.assertEqual("accepted", manifest["phase"])
        details = [check["detail"] for check in manifest["acceptance"]["checks"]]
        self.assertTrue(any(
            "release-2.9.3 present at the recorded commit" in detail
            for detail in details))
        self.assertIsNotNone(manifest["acceptance"]["acceptedAt"])
        self.assertEqual(["repo-one", "repo-two"], acceptance.remote_integrator.verified)
        self.assertEqual(["release acceptance"], acceptance.publisher.guard_calls)

    def test_acceptance_is_reached_from_a_published_release(self):
        """Publication is no longer the end of the route."""
        with tempfile.TemporaryDirectory() as directory:
            state = self._published(directory)
            acceptance = self._acceptance(state, tagged={"repo-one", "repo-two"})

            manifest = advance_active_release(state, acceptance=acceptance)

        self.assertEqual("accepted", manifest["phase"])

    def test_an_accepted_release_is_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._published(directory)
            state.update_current_manifest({"phase": "accepted"})

            manifest = advance_active_release(state, acceptance=None)

        self.assertEqual("accepted", manifest["phase"])


class TransientRetryTest(unittest.TestCase):
    """A network blip must not end a release; a guard must still end it at once."""

    def _state(self, directory):
        state = ReleaseState(root=Path(directory))
        state.start(manifest_fixture())
        return state

    def test_a_transport_fault_is_retried_with_backoff(self):
        slept = []
        outcomes = [
            RetryableReleaseError("connection reset"),
            RetryableReleaseError("connection reset"),
            {"phase": "accepted"},
        ]

        def advance(_state):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(release_train, "advance_active_release", advance):
            manifest = release_train._drive_release(
                self._state(directory), sleeper=slept.append)

        self.assertEqual("accepted", manifest["phase"])
        self.assertEqual([30, 60], slept)

    def test_a_guard_failure_stops_immediately(self):
        slept = []

        def advance(_state):
            raise ReleaseError("integration commit changed the prepared tree")

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(release_train, "advance_active_release", advance):
            with self.assertRaisesRegex(ReleaseError, "changed the prepared tree"):
                release_train._drive_release(
                    self._state(directory), sleeper=slept.append)

        self.assertEqual([], slept, "a guard failure must not be retried")

    def test_retries_are_bounded(self):
        slept = []

        def advance(_state):
            raise RetryableReleaseError("connection reset")

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(release_train, "advance_active_release", advance):
            with self.assertRaises(RetryableReleaseError):
                release_train._drive_release(
                    self._state(directory), sleeper=slept.append)

        self.assertEqual(release_train.TRANSIENT_RETRY_ATTEMPTS - 1, len(slept))

    def test_subprocess_transport_classification_is_narrow(self):
        self.assertTrue(release_train._command_failure_is_retryable(
            ["mvn", "deploy"], "server returned HTTP 503"))
        self.assertTrue(release_train._command_failure_is_retryable(
            ["git", "push"], "RPC failed; curl 92 HTTP/2 stream was not closed"))
        self.assertFalse(release_train._command_failure_is_retryable(
            ["mvn", "deploy"], "server returned HTTP 500"))
        self.assertFalse(release_train._command_failure_is_retryable(
            ["git", "push"], "protected branch update failed"))


class ReleaseStageOrderTest(unittest.TestCase):
    """The stage list is the release's control flow, so its shape is worth asserting."""

    def test_each_stage_hands_its_done_phase_to_the_next(self):
        """A gap here would strand a release at the phase the previous stage recorded."""
        for earlier, later in zip(release_train.RELEASE_STAGES,
                                  release_train.RELEASE_STAGES[1:]):
            self.assertIn(
                earlier.done_phase, later.entry_phases,
                f"{later.name} cannot continue a release that {earlier.name} finished")

    def test_no_phase_belongs_to_two_stages(self):
        seen = {}
        for stage in release_train.RELEASE_STAGES:
            for phase in stage.entry_phases:
                self.assertNotIn(
                    phase, seen, f"{phase} starts both {seen.get(phase)} and {stage.name}")
                seen[phase] = stage.name

    def test_every_phase_a_stage_records_can_be_resumed_from(self):
        """A phase no stage accepts is a release that resume cannot move."""
        recorded = set()
        for stage in release_train.RELEASE_STAGES:
            recorded.add(stage.done_phase)
        recorded.update(release_train.REWIND_TO_FRONTENDS)
        source = inspect.getsource(release_train)
        for match in re.finditer(r'"phase": "([a-z-]+)"', source):
            recorded.add(match.group(1))
        # The planner stamps "validated" on a manifest that is not yet an active release;
        # ReleaseState.start replaces it with "started" before any stage sees it.
        recorded.discard("validated")
        accepted = set(release_train.REWIND_TO_FRONTENDS)
        accepted.update(release_train.RELEASE_FINAL_PHASES)
        for stage in release_train.RELEASE_STAGES:
            accepted |= stage.entry_phases
        self.assertEqual(
            set(), recorded - accepted,
            "these phases can be recorded but no stage or rewind accepts them")


class ReleaseResumptionTest(unittest.TestCase):
    def _state(self, directory, phase):
        state = ReleaseState(root=Path(directory))
        state.start(manifest_fixture())
        state.update_current_manifest({"phase": phase})
        return state

    def _run(self, phase):
        """Advance from one phase with every stage stubbed, reporting which ones ran."""
        ran = []

        def stub(stage):
            def run(state, _deps):
                ran.append(stage.name)
                manifest, _ = state.update_current_manifest({"phase": stage.done_phase})
                return manifest
            return dataclasses.replace(stage, run=run)

        stages = tuple(stub(stage) for stage in release_train.RELEASE_STAGES)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(release_train, "RELEASE_STAGES", stages), \
                patch.object(release_train, "RELEASE_TERMINAL_PHASE", stages[-1].done_phase):
            manifest = advance_active_release(self._state(directory, phase))
        return ran, manifest

    def test_a_failed_stage_reruns_only_itself_and_what_follows(self):
        ran, manifest = self._run("remote-integration-failed")

        self.assertEqual(["remotes", "artifacts", "acceptance"], ran)
        self.assertEqual("accepted", manifest["phase"])

    def test_a_finished_stage_hands_over_to_the_next(self):
        ran, _ = self._run("builds-validated")

        self.assertEqual(
            ["local-refs", "snapshots", "remotes", "artifacts", "acceptance"], ran)

    def test_a_fresh_release_runs_every_stage(self):
        ran, _ = self._run("started")

        self.assertEqual([stage.name for stage in release_train.RELEASE_STAGES], ran)

    def test_an_accepted_release_runs_nothing(self):
        ran, manifest = self._run("accepted")

        self.assertEqual([], ran)
        self.assertEqual("accepted", manifest["phase"])

    def test_a_release_stopped_while_stamping_versions_can_still_resume(self):
        """Nothing accepted this phase before, so such a release could not be moved at all."""
        ran, manifest = self._run("preparing-versions")

        self.assertEqual([stage.name for stage in release_train.RELEASE_STAGES], ran)
        self.assertEqual("accepted", manifest["phase"])

    def test_an_unrecognised_phase_says_so_rather_than_running_the_wrong_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._state(directory, "not-a-phase")
            with self.assertRaisesRegex(ReleaseError, "no stage that can continue it"):
                advance_active_release(state)


class ReleaseSnapshotOrderingTest(unittest.TestCase):
    """Snapshots must reach Nexus before any develop push tells CI to look for them."""

    @staticmethod
    def _snapshot_record(directory, task):
        log = Path(directory) / "snapshot-publication.log"
        log.write_text("snapshot deployed\n", encoding="utf-8")
        return {
            **task,
            "log": str(log),
            "logSha256": release_train._file_sha256(log),
        }

    def test_snapshots_are_deployed_before_the_remotes_are_integrated(self):
        names = [stage.name for stage in release_train.RELEASE_STAGES]

        self.assertLess(
            names.index("snapshots"), names.index("remotes"),
            "a develop push whose snapshots are not yet published sends CI looking for them")

    def test_the_snapshot_plan_does_not_need_the_remotes_to_have_been_integrated(self):
        """This is what lets the stage run first: it binds to the verified local ref."""
        with tempfile.TemporaryDirectory() as directory:
            state, _integrator, _remotes, manifest = (
                ReleaseArtifactPublicationTest().make_release(directory)
            )
            without_integration = copy.deepcopy(manifest)
            without_integration.pop("remoteIntegration")
            publisher = ReleaseArtifactPublisher(state, executor=lambda *_args: {})

            snapshots = publisher.snapshot_tasks(without_integration)

            self.assertTrue(snapshots)
            with self.assertRaisesRegex(ReleaseError, "no verified remote integration"):
                publisher.tasks(without_integration)

    def test_a_snapshot_task_carries_the_prepared_tree_the_integration_will_push(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _integrator, _remotes, manifest = (
                ReleaseArtifactPublicationTest().make_release(directory)
            )
            publisher = ReleaseArtifactPublisher(state, executor=lambda *_args: {})
            deploy = next(task for task in publisher.snapshot_tasks(manifest)
                          if task["kind"] == "maven-snapshot-deploy")
            integration = manifest["remoteIntegration"]["completedTasks"][deploy["repository"]]

            self.assertEqual(integration["develop"]["tree"], deploy["expectedTree"])

    def test_snapshot_evidence_accepts_the_recorded_develop_integration_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _integrator, _remotes, manifest = (
                ReleaseArtifactPublicationTest().make_release(directory)
            )
            publisher = ReleaseArtifactPublisher(state)
            tasks = publisher.snapshot_tasks(manifest)
            deploy = next(task for task in tasks
                          if task["kind"] == "maven-snapshot-deploy")
            integration = manifest["remoteIntegration"]["completedTasks"][deploy["repository"]]
            root = Path(deploy["workspace"]) / deploy["repository"]

            self.assertEqual(
                integration["develop"]["commit"],
                ReleaseLocalRefsTest._git(root, "rev-parse", "HEAD"),
            )
            self.assertNotEqual(deploy["expectedCommit"], integration["develop"]["commit"])

            publisher.verify_record(
                manifest, self._snapshot_record(directory, deploy), tasks,
            )

    def test_snapshot_evidence_rejects_an_unrecorded_same_tree_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _integrator, _remotes, manifest = (
                ReleaseArtifactPublicationTest().make_release(directory)
            )
            publisher = ReleaseArtifactPublisher(state)
            tasks = publisher.snapshot_tasks(manifest)
            deploy = next(task for task in tasks
                          if task["kind"] == "maven-snapshot-deploy")
            integration = manifest["remoteIntegration"]["completedTasks"][deploy["repository"]]
            root = Path(deploy["workspace"]) / deploy["repository"]
            unrecorded = subprocess.run([
                "git", "-c", "user.name=Release Test",
                "-c", "user.email=release-test@metadatacenter.org",
                "-C", str(root), "commit-tree", deploy["expectedTree"],
                "-p", integration["develop"]["commit"], "-m", "Unrecorded commit",
            ], check=True, text=True, capture_output=True).stdout.strip()
            subprocess.run([
                "git", "-C", str(root), "switch", "--quiet", "--detach", unrecorded,
            ], check=True)

            with self.assertRaisesRegex(ReleaseError, "publication workspace changed"):
                publisher.verify_record(
                    manifest, self._snapshot_record(directory, deploy), tasks,
                )

    def test_integration_refuses_to_run_before_the_snapshots_are_published(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            state.start(manifest_fixture())
            state.update_current_manifest({"phase": "local-refs-created"})

            with self.assertRaisesRegex(ReleaseError, "cannot integrate remotes"):
                integrate_active_release(state, None)

    def test_a_failed_snapshot_deploy_is_resumable_without_touching_a_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            state.start(manifest_fixture())
            state.update_current_manifest({"phase": "snapshot-publication-failed"})
            names = [stage.name for stage in release_train.RELEASE_STAGES]
            phase = "snapshot-publication-failed"
            stage = next(s for s in release_train.RELEASE_STAGES if phase in s.entry_phases)

            self.assertEqual("snapshots", stage.name)
            self.assertLess(names.index(stage.name), names.index("remotes"))


class ReleaseCompletionTest(unittest.TestCase):
    """A finished release must stop being the active one, or it blocks the next."""

    def _finished(self, directory, phase):
        state = ReleaseState(root=Path(directory))
        state.start(manifest_fixture())
        state.update_current_manifest({"phase": phase})
        return state

    def test_acceptance_releases_the_active_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "artifacts-published")
            acceptance = ReleaseAcceptanceTest._acceptance(state, tagged=set())
            accept_active_release(state, acceptance)

            self.assertTrue(state.read_current().get("concludedAt"),
                            "a finished release must be marked concluded")
            self.assertTrue(state.manifest_path("2.9.3").exists(),
                            "the record must survive concluding")

    def test_a_second_release_can_start_once_the_first_concluded(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "artifacts-published")
            acceptance = ReleaseAcceptanceTest._acceptance(state, tagged=set())
            accept_active_release(state, acceptance)

            later = manifest_fixture()
            later["releaseVersion"] = "2.9.4"
            state.start(later)

            self.assertEqual("2.9.4", state.read_current()["releaseVersion"])

    def test_resume_repairs_an_accepted_manifest_whose_pointer_was_not_concluded(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "accepted")

            advance_active_release(state)

            self.assertEqual("accepted", state.read_current()["conclusion"])
            self.assertTrue(state.read_current()["concludedAt"])

    def test_acceptance_repairs_an_accepted_manifest_whose_pointer_was_not_concluded(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "accepted")

            accept_active_release(state)

            self.assertEqual("accepted", state.read_current()["conclusion"])
            self.assertTrue(state.read_current()["concludedAt"])

    def test_abandon_retains_the_failed_attempt_and_allows_the_same_version_again(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "local-ref-creation-failed")
            original_path = state.read_current_manifest()[1]
            state.update_current_manifest({
                "failure": "generated file changed",
                "localRefs": {"pushed": False, "completedTasks": {}},
            })

            abandoned, retained_path = abandon_active_release(
                "2.9.3", "superseded by a corrected train", state,
            )

            self.assertEqual(original_path, retained_path)
            self.assertTrue(retained_path.is_file())
            self.assertEqual("abandoned", abandoned["phase"])
            self.assertEqual(
                "local-ref-creation-failed",
                abandoned["abandonment"]["previousPhase"],
            )
            self.assertEqual("abandoned", state.read_current()["conclusion"])

            with self.assertRaisesRegex(ReleaseError, "was abandoned and cannot be resumed"):
                advance_active_release(state)

            replacement_path = state.start(manifest_fixture())
            self.assertNotEqual(retained_path, replacement_path)
            self.assertTrue(retained_path.is_file())
            self.assertTrue(replacement_path.is_file())

    def test_abandon_refuses_once_snapshot_publication_may_have_started(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "snapshot-publication-failed")
            state.update_current_manifest({
                "snapshotPublication": {
                    "startedAt": "2026-09-01T00:00:00+00:00",
                    "completedTasks": {},
                },
            })

            with self.assertRaisesRegex(ReleaseError, "may already have changed external state"):
                abandon_active_release("2.9.3", "use another train", state)

            self.assertFalse(state.read_current().get("concludedAt"))

    def test_abandon_requires_the_exact_active_version(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "local-ref-creation-failed")

            with self.assertRaisesRegex(ReleaseError, "active release is 2.9.3"):
                abandon_active_release("2.9.4", "wrong release", state)

    def test_abandoned_status_names_the_reason_without_calling_it_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "local-ref-creation-failed")
            _manifest, path = abandon_active_release(
                "2.9.3", "superseded by a corrected train", state,
            )
            manifest, _ = state.read_current_manifest()

            with release_train.console.capture() as capture:
                release_train._render_release_status(manifest, path)

            output = capture.get()
            self.assertIn("ABANDONED", output)
            self.assertIn("superseded by a corrected train", output)
            self.assertNotIn("COMPLETE", output)

    def test_preflight_reports_an_unfinished_release_rather_than_letting_start_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self._finished(directory, "integrating-remotes")
            preflight = ReleasePreflight(
                {"releaseVersion": "2.9.4", "releaseRepositories": []},
                state=state, environment=dict(PREFLIGHT_ENVIRONMENT),
            )
            findings = preflight.check_no_release_in_progress()

        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].fatal)
        self.assertIn("2.9.3", findings[0].message)

    def test_preflight_passes_when_no_release_holds_the_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            preflight = ReleasePreflight(
                {"releaseVersion": "2.9.4", "releaseRepositories": []},
                state=ReleaseState(root=Path(directory)),
                environment=dict(PREFLIGHT_ENVIRONMENT),
            )
            self.assertEqual([], preflight.check_no_release_in_progress())


if __name__ == "__main__":
    unittest.main()

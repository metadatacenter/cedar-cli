import base64
import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import release_train
from org.metadatacenter.release_train import (
    DEV_CEE_NAME,
    PUBLIC_CEE_NAME,
    ReleaseArtifactPublisher,
    ReleaseError,
    ReleasePlanner,
    ReleaseBuildValidator,
    ReleaseRefCreator,
    ReleaseRemoteIntegrator,
    ReleaseState,
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
    def __init__(self, contents, public_metadata, frontend_config, build_config):
        self.contents = contents
        self.public_metadata = public_metadata
        self.frontend_config = frontend_config
        self.build_config = build_config

    def read(self, url, *, missing_ok=False):
        return self.contents[url]

    def read_json(self, url, *, missing_ok=False):
        if url.endswith("/ops/frontend-train.json"):
            value = self.frontend_config
        elif url.endswith("/ops/build-train.json"):
            value = self.build_config
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
        changed = package_tarball(
            PUBLIC_CEE_NAME,
            PUBLIC_VERSION,
            development=False,
            bundle=b"different CEE bundle",
        )
        with self.assertRaisesRegex(ReleaseError, "incomplete release-provenance set"):
            compare_cee_packages(self.dev, DEV_VERSION, changed, PUBLIC_VERSION)

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

    def test_explicit_public_version_must_match_the_dev_stable_base(self):
        with self.assertRaisesRegex(ReleaseError, "cannot be promoted"):
            compare_cee_packages(self.dev, DEV_VERSION, self.public, "2.0.4")

    def test_unexpected_public_publish_config_is_rejected(self):
        incorrectly_scoped = package_tarball(
            PUBLIC_CEE_NAME, PUBLIC_VERSION, development=True,
        )
        with self.assertRaisesRegex(ReleaseError, "public package must not contain"):
            compare_cee_packages(
                self.dev, DEV_VERSION, incorrectly_scoped, PUBLIC_VERSION,
            )


class ReleasePlannerTest(unittest.TestCase):
    def make_planner(self):
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
        state = FakeState({
            f"trains/{TRAIN}.json": (source, source_content),
            f"completed/{TRAIN}.json": ({"version": TRAIN}, b"{}\n"),
            f"npm/trains/{TRAIN}.json": (npm_plan, npm_plan_content),
            f"npm/completed/{TRAIN}.json": (npm_completion, b"{}\n"),
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
            {dev_url: dev, public_url: public}, metadata, frontend_config, build_config,
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

    def test_state_owns_the_manifest_and_refuses_a_second_active_release(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            path = state.start(manifest_fixture())
            manifest, current_path = state.read_current_manifest()
            self.assertEqual(path, current_path)
            self.assertEqual("started", manifest["phase"])
            with self.assertRaisesRegex(ReleaseError, "already active"):
                state.start(manifest_fixture())

    @patch.object(ReleasePlanner, "build", return_value=manifest_fixture())
    def test_plan_is_side_effect_free(self, build):
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

    @patch.object(ReleaseArtifactPublisher, "tasks", return_value=[])
    @patch.object(ReleaseRemoteIntegrator, "tasks", return_value=[])
    @patch.object(ReleaseRefCreator, "tasks", return_value=[])
    @patch.object(ReleaseBuildValidator, "tasks", return_value=[])
    @patch.object(ReleaseVersionPreparer, "prepare", return_value={"release": {}, "nextDevelopment": {}})
    @patch.object(ReleaseWorkspacePreparer, "prepare", return_value={"attempt": "001"})
    @patch.object(ReleasePlanner, "build", return_value=manifest_fixture())
    def test_start_persists_internal_state_and_status_finds_it(
        self, _build, _workspace_prepare, _version_prepare, _build_tasks, _ref_tasks,
        _remote_tasks, _artifact_tasks,
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
            self.assertIn("Phase:               artifacts-published", start.output)
            status = self.runner.invoke(release_train.app, ["status"])
            self.assertEqual(0, status.exit_code, status.output)
            self.assertIn("Phase:               artifacts-published", status.output)
            self.assertIn("Local refs:          0 prepared refs", status.output)
            self.assertIn("Remote integration:  0 repositories", status.output)
            self.assertIn("Artifact publication:  0 verified tasks", status.output)


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
            "cedar-template-editor": {},
            "cedar-development": {
                "bin/util/set-env-generic.sh": f"export CEDAR_VERSION={source_version}\n",
            },
            "cedar-docker-build": {
                "frontend/Dockerfile": f"ENV CEDAR_VERSION={source_version}\n",
                "dynamic/Dockerfile": "ENV CEDAR_VERSION=${CEDAR_MAVEN_VERSION}\n",
                "bin/cedar-images-base.sh": f"export IMAGE_VERSION={source_version}\n",
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
                "mavenRepositories": ["maven-repo"],
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
            self.assertIn(
                "${CEDAR_MAVEN_VERSION}",
                (release_workspace / "cedar-docker-build" / "dynamic" / "Dockerfile").read_text(),
            )
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
            environment={"CEDAR_HOME": str(cedar_home)},
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
            tasks = ReleaseArtifactPublisher(state, executor=lambda *_args: {}).tasks(manifest)
            identifiers = [task["id"] for task in tasks]
            self.assertEqual([
                "maven:release:publish",
                "maven:release:verify",
                "npm:release:main",
                "maven:nextDevelopment:main",
                "maven:nextDevelopment:verify",
            ], identifiers)
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
                def tasks(_manifest):
                    return [
                        {"id": "one", "kind": "fake"},
                        {"id": "two", "kind": "fake"},
                        {"id": "three", "kind": "fake"},
                    ]

                def run_task(self, _manifest, task):
                    calls.append(task["id"])
                    if task["id"] == "two" and calls.count("two") == 1:
                        raise ReleaseError("registry unavailable")
                    return {**task, "proof": task["id"]}

                @staticmethod
                def verify_record(_manifest, record):
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
            }
            publisher = ReleaseArtifactPublisher(
                ReleaseState(root=Path(directory) / "state"),
            )
            tarball, evidence = publisher._pack_npm(task)
            package = publisher._npm_tarball_package("packed frontend", tarball.read_bytes())
            self.assertEqual("cedar-frontend", evidence["name"])
            self.assertEqual(commit, package["gitHead"])
            self.assertEqual("2.9.3", package["version"])


if __name__ == "__main__":
    unittest.main()

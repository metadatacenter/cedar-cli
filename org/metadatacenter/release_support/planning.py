"""CEDAR release planning."""
from __future__ import annotations
from org.metadatacenter.util.BuildTrain import BuildTrain
from pathlib import Path, PurePosixPath
import copy
import datetime as dt
import re
import urllib.request
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _sha256,
)
from org.metadatacenter.release_support.packages import (
    _verify_integrity,
    compare_cee_packages,
)
from org.metadatacenter.release_support.policy import (
    DEV_CEE_NAME,
    GIT_SHA_RE,
    INDEPENDENT_RELEASE_REPOSITORIES,
    MAVEN_RELEASE_REPOSITORY,
    MAVEN_SNAPSHOT_REPOSITORY,
    NEXT_VERSION_RE,
    NPM_RELEASE_SURFACES,
    PUBLIC_CEE_NAME,
    PUBLIC_NPM_REGISTRY,
    SHA256_RE,
    _stable_version_key,
    _validate_stable_version,
)
from org.metadatacenter.release_support.transport import (
    HttpClient,
    TrainState,
)


class ReleasePlanner:
    def __init__(self, http: HttpClient | None = None, state: TrainState | None = None):
        self.http = http or HttpClient()
        self.state = state or TrainState(self.http)

    def _public_record(self, version: str) -> dict:
        package_url = (
            PUBLIC_NPM_REGISTRY.rstrip("/") + "/"
            + urllib.parse.quote(PUBLIC_CEE_NAME, safe="")
        )
        result = self.http.read_json(package_url)
        assert result is not None
        metadata, _ = result
        record = metadata.get("versions", {}).get(version)
        if not isinstance(record, dict):
            raise ReleaseError(f"npmjs does not contain {PUBLIC_CEE_NAME}@{version}")
        return record

    def _development_config(self, source: dict, filename: str) -> tuple[dict, str, str]:
        revision = source.get("repositories", {}).get("cedar-development")
        if not isinstance(revision, str) or not GIT_SHA_RE.fullmatch(revision):
            raise ReleaseError("train source manifest has no cedar-development revision")
        url = (
            "https://raw.githubusercontent.com/metadatacenter/cedar-development/"
            f"{revision}/ops/{filename}"
        )
        result = self.http.read_json(url)
        assert result is not None
        config, content = result
        return config, url, _sha256(content)

    def _repository_json(
        self,
        repository: str,
        revision: str,
        relative: str,
    ) -> tuple[dict, str, str]:
        if not GIT_SHA_RE.fullmatch(revision):
            raise ReleaseError(f"{repository} has no valid captured revision")
        url = (
            f"https://raw.githubusercontent.com/metadatacenter/{repository}/"
            f"{revision}/{relative}"
        )
        result = self.http.read_json(url)
        assert result is not None
        value, content = result
        return value, url, _sha256(content)

    @staticmethod
    def _release_repositories(build_config: dict, source: dict) -> tuple[list[str], list[str]]:
        configured = build_config.get("repositories")
        maven = build_config.get("mavenRepositories")
        if not isinstance(configured, list) or not configured:
            raise ReleaseError("train build configuration has no repositories")
        if not isinstance(maven, list) or not maven:
            raise ReleaseError("train build configuration has no Maven repositories")
        source_repositories = source.get("repositories", {})
        if set(configured) != set(source_repositories):
            missing = sorted(set(configured) - set(source_repositories))
            extra = sorted(set(source_repositories) - set(configured))
            raise ReleaseError(
                "train source and build configuration repository sets differ: "
                f"missing={missing}, extra={extra}"
            )
        if not set(maven).issubset(configured):
            raise ReleaseError("train Maven repository set is not part of the source repository set")
        release = [
            repository for repository in configured
            if repository not in INDEPENDENT_RELEASE_REPOSITORIES
        ]
        return release, maven

    @staticmethod
    def _maven_phases(build_config: dict, maven_repositories: list[str]) -> list[dict]:
        phases = build_config.get("phases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("train build configuration has no Maven phases")
        result = []
        for phase in phases:
            if not isinstance(phase, dict):
                raise ReleaseError("train build configuration contains an invalid Maven phase")
            name = phase.get("name")
            repository = phase.get("repository")
            if not isinstance(name, str) or not name or repository not in maven_repositories:
                raise ReleaseError(f"invalid train Maven phase {phase!r}")
            result.append({"name": name, "repository": repository})
        return result

    @staticmethod
    def _publication_plan(build_config: dict, release_repositories: list[str]) -> dict:
        required = build_config.get("requiredArtifacts")
        if not isinstance(required, list) or not required or not all(
            isinstance(item, str) and item for item in required
        ):
            raise ReleaseError("train build configuration has no required Maven artifacts")
        npm_surfaces = copy.deepcopy(NPM_RELEASE_SURFACES)
        missing = sorted(
            {surface["repository"] for surface in npm_surfaces} - set(release_repositories)
        )
        if missing:
            raise ReleaseError(
                "release repository set is missing npm publication repositories: "
                + ", ".join(missing)
            )
        return {
            "maven": {
                "releaseRepository": MAVEN_RELEASE_REPOSITORY,
                "nextDevelopmentRepository": MAVEN_SNAPSHOT_REPOSITORY,
                "requiredArtifacts": list(required),
            },
            "npm": {
                "registry": "https://nexus.bmir.stanford.edu/repository/npm-cedar/",
                "surfaces": npm_surfaces,
            },
        }

    @staticmethod
    def _docker_frontend_defaults(
        frontend_config: dict,
        npm_completion: dict,
        publication_plan: dict,
        release_version: str,
        cee_version: str,
    ) -> dict:
        """The frontend package each stamped tree's Docker build defaults should name.

        The train records under dockerInputs the exact package behind every frontend build
        argument it verified. The next-development tree takes those as they are: they exist the
        moment the release pushes, so the Docker build's CI at the post-release commit resolves
        them, and they are the newest development packages there are. The release tree takes what
        outlives them. Nexus keeps only the last couple of trains' development packages and never
        removes a release, so the released frontends are named at the release version and
        OpenView's Editor at the public CEE version.
        """
        inputs = npm_completion.get("dockerInputs")
        if inputs is None:
            return {}
        if not isinstance(inputs, dict) or not inputs or not all(
            isinstance(name, str) and name and isinstance(value, str) and value
            for name, value in inputs.items()
        ):
            raise ReleaseError("npm completion dockerInputs must map variable names to versions")
        released = {
            surface["repository"] for surface in publication_plan["npm"]["surfaces"]
        }
        release_values = dict(inputs)
        for frontend in frontend_config.get("frontends", []):
            variable = frontend.get("npmVersionVariable")
            if variable in inputs and frontend.get("repository") in released:
                release_values[variable] = release_version
        cee_variable = frontend_config.get("dockerCeeVersionVariable")
        if isinstance(cee_variable, str) and cee_variable in inputs:
            release_values[cee_variable] = cee_version
        return {"release": release_values, "nextDevelopment": dict(inputs)}

    @staticmethod
    def _cee_consumers(config: dict, npm_plan: dict, source: dict) -> list[dict]:
        repositories = source.get("repositories", {})
        if not isinstance(repositories, dict):
            raise ReleaseError("train source manifest has no repository inventory")
        planned_frontends = {
            item.get("repository"): item
            for item in npm_plan.get("frontends", [])
            if item.get("ceeVersion") is not None
        }
        planned_additional = {
            (item.get("repository"), item.get("manifest")): item
            for item in npm_plan.get("additionalCeeConsumers", [])
        }
        consumers = []
        for frontend in config.get("frontends", []):
            consumer = frontend.get("ceeConsumer")
            if not isinstance(consumer, dict):
                continue
            repository = frontend.get("repository")
            planned = planned_frontends.pop(repository, None)
            if not isinstance(planned, dict):
                raise ReleaseError(f"npm plan has no CEE-wired frontend record for {repository}")
            revision = repositories.get(repository)
            if planned.get("revision") != revision:
                raise ReleaseError(f"npm plan and source manifest disagree for {repository}")
            consumers.append({
                "label": frontend.get("id", repository),
                "repository": repository,
                "revision": revision,
                "manifest": consumer.get("manifest"),
                "lock": consumer.get("lock"),
            })
        for consumer in config.get("additionalCeeConsumers", []):
            repository = consumer.get("repository")
            manifest = consumer.get("manifest")
            planned = planned_additional.pop((repository, manifest), None)
            if not isinstance(planned, dict):
                raise ReleaseError(
                    f"npm plan has no additional CEE consumer record for {repository}/{manifest}"
                )
            revision = repositories.get(repository)
            if planned.get("revision") != revision:
                raise ReleaseError(f"npm plan and source manifest disagree for {repository}")
            consumers.append({
                "label": manifest,
                "repository": repository,
                "revision": revision,
                "manifest": manifest,
                "lock": consumer.get("lock"),
            })
        if planned_frontends or planned_additional:
            raise ReleaseError("frontend configuration does not cover every CEE consumer in npm plan")
        for consumer in consumers:
            if not GIT_SHA_RE.fullmatch(consumer.get("revision") or ""):
                raise ReleaseError(
                    f"train source manifest has no valid revision for {consumer.get('repository')}"
                )
            for field in ("manifest", "lock"):
                value = consumer.get(field)
                if not isinstance(value, str) or not value or PurePosixPath(value).is_absolute() \
                        or ".." in PurePosixPath(value).parts:
                    raise ReleaseError(
                        f"CEE consumer {consumer.get('repository')} has invalid {field} path"
                    )
        if len(consumers) != 7:
            raise ReleaseError(f"expected 7 CEE consumers, found {len(consumers)}")
        return consumers

    @staticmethod
    def _distribution(identity: str, record: dict) -> tuple[str, str]:
        distribution = record.get("dist", {})
        tarball_url = distribution.get("tarball")
        integrity = distribution.get("integrity")
        if not isinstance(tarball_url, str) or not isinstance(integrity, str):
            raise ReleaseError(f"{identity} has no tarball and integrity metadata")
        return tarball_url, integrity

    @staticmethod
    def _validate_docker_completion(
        train: str,
        source_sha256: str,
        npm_plan_sha256: str,
        plan: dict,
        completion: dict,
    ) -> None:
        for label, value in (("Docker plan", plan), ("Docker completion", completion)):
            if value.get("version") != train:
                raise ReleaseError(f"{label} does not describe train {train}")
        if plan.get("sourceManifestSha256") != source_sha256 \
                or completion.get("sourceManifestSha256") != source_sha256:
            raise ReleaseError("Docker train does not match the source manifest")
        if plan.get("npmPlanSha256") != npm_plan_sha256 \
                or completion.get("npmPlanSha256") != npm_plan_sha256:
            raise ReleaseError("Docker train does not match the npm plan")
        if completion.get("plan") != f"docker/trains/{train}.json":
            raise ReleaseError("Docker completion does not name its immutable plan")
        planned = plan.get("images")
        verified = completion.get("images")
        if not isinstance(planned, list) or not isinstance(verified, list):
            raise ReleaseError("Docker train has no image inventory")
        planned_names = [item.get("image") for item in planned if isinstance(item, dict)]
        verified_names = [item.get("image") for item in verified if isinstance(item, dict)]
        if (
            len(planned_names) != 31 or len(set(planned_names)) != 31
            or verified_names != planned_names
        ):
            raise ReleaseError("Docker train is not complete for all 31 planned images")
        for item in verified:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest", "")):
                raise ReleaseError(
                    f"Docker completion has no immutable digest for {item.get('image')}")

    def build(
        self,
        *,
        release_version: str,
        next_version: str,
        train: str,
        cee_version: str,
    ) -> dict:
        _validate_stable_version(release_version, "release version")
        _validate_stable_version(cee_version, "CEE version")
        if not NEXT_VERSION_RE.fullmatch(next_version or ""):
            raise ReleaseError(
                f"invalid next development version {next_version!r}; expected MAJOR.MINOR.PATCH-SNAPSHOT"
            )
        next_stable = next_version.removesuffix("-SNAPSHOT")
        if _stable_version_key(next_stable) <= _stable_version_key(release_version):
            raise ReleaseError(
                f"next development version {next_version} must be newer than release {release_version}"
            )
        try:
            BuildTrain.validate(train)
        except ValueError as error:
            raise ReleaseError(str(error)) from error
        if train.split("-dev.", 1)[0] != release_version:
            raise ReleaseError(
                f"train {train} is not a development train for explicit release {release_version}"
            )

        source, source_content = self.state.read_json(f"trains/{train}.json")
        source_version = source.get("sourceVersion")
        if source_version != f"{release_version}-SNAPSHOT":
            raise ReleaseError(
                f"train source version is {source_version!r}, expected {release_version}-SNAPSHOT"
            )
        completion, _ = self.state.read_json(f"completed/{train}.json")
        npm_plan, npm_plan_content = self.state.read_json(f"npm/trains/{train}.json")
        npm_completion, _ = self.state.read_json(f"npm/completed/{train}.json")
        docker_plan, docker_plan_content = self.state.read_json(f"docker/trains/{train}.json")
        docker_completion, _ = self.state.read_json(f"docker/completed/{train}.json")
        for label, value in (
            ("source manifest", source),
            ("completion record", completion),
            ("npm plan", npm_plan),
            ("npm completion", npm_completion),
        ):
            if value.get("version") != train:
                raise ReleaseError(f"{label} does not describe train {train}")
        source_sha256 = _sha256(source_content)
        if npm_plan.get("sourceManifestSha256") != source_sha256:
            raise ReleaseError("npm plan does not match the train source manifest")
        if npm_completion.get("sourceManifestSha256") != source_sha256:
            raise ReleaseError("npm completion does not match the train source manifest")
        npm_plan_sha256 = _sha256(npm_plan_content)
        if npm_completion.get("planSha256") != npm_plan_sha256:
            raise ReleaseError("npm completion does not match the npm plan")
        self._validate_docker_completion(
            train, source_sha256, npm_plan_sha256, docker_plan, docker_completion,
        )
        frontend_config, frontend_config_url, frontend_config_sha256 = self._development_config(
            source, "frontend-train.json"
        )
        build_config, build_config_url, build_config_sha256 = self._development_config(
            source, "build-train.json"
        )
        release_repositories, maven_repositories = self._release_repositories(
            build_config, source
        )
        maven_phases = self._maven_phases(build_config, maven_repositories)
        publication_plan = self._publication_plan(build_config, release_repositories)
        consumers = self._cee_consumers(frontend_config, npm_plan, source)
        docker_frontend_defaults = self._docker_frontend_defaults(
            frontend_config, npm_completion, publication_plan, release_version, cee_version,
        )

        planned_cee = npm_plan.get("cee", {})
        dev_version = planned_cee.get("version")
        if planned_cee.get("name") != DEV_CEE_NAME or not isinstance(dev_version, str):
            raise ReleaseError(f"npm plan has no {DEV_CEE_NAME} development package")
        dev_record = next(
            (
                package for package in npm_completion.get("packages", [])
                if package.get("name") == DEV_CEE_NAME and package.get("version") == dev_version
            ),
            None,
        )
        if not isinstance(dev_record, dict):
            raise ReleaseError(f"npm completion has no verified {DEV_CEE_NAME}@{dev_version}")
        dev_revision = planned_cee.get("revision")
        if not isinstance(dev_revision, str) or not GIT_SHA_RE.fullmatch(dev_revision):
            raise ReleaseError("npm plan CEE record has no valid captured revision")
        source_cee_revision = source.get("repositories", {}).get("cedar-embeddable-editor")
        if source_cee_revision is not None and source_cee_revision != dev_revision:
            raise ReleaseError("npm plan and source manifest disagree for cedar-embeddable-editor")
        cee_source_package, _, _ = self._repository_json(
            "cedar-embeddable-editor", dev_revision, "package.json"
        )
        development_allow_scripts = cee_source_package.get("allowScripts")
        if development_allow_scripts is not None and not isinstance(
            development_allow_scripts, dict
        ):
            raise ReleaseError("captured CEE package.json has an invalid allowScripts policy")
        dev_url = dev_record.get("tarball")
        dev_integrity = dev_record.get("integrity")
        dev_tarball_sha256 = dev_record.get("tarballSha256")
        if not isinstance(dev_url, str) or not isinstance(dev_integrity, str):
            raise ReleaseError("npm completion CEE record has no tarball and integrity")
        if not isinstance(dev_tarball_sha256, str) or not SHA256_RE.fullmatch(dev_tarball_sha256):
            raise ReleaseError("npm completion CEE record has no valid tarball SHA-256")
        dev_tarball = self.http.read(dev_url)
        assert dev_tarball is not None
        _verify_integrity(f"{DEV_CEE_NAME}@{dev_version}", dev_tarball, dev_integrity)
        if _sha256(dev_tarball) != dev_tarball_sha256:
            raise ReleaseError("train CEE tarball does not match its recorded SHA-256")

        public_record = self._public_record(cee_version)
        public_url, public_integrity = self._distribution(
            f"{PUBLIC_CEE_NAME}@{cee_version}", public_record
        )
        public_tarball = self.http.read(public_url)
        assert public_tarball is not None
        _verify_integrity(
            f"{PUBLIC_CEE_NAME}@{cee_version}", public_tarball, public_integrity
        )
        proof = compare_cee_packages(
            dev_tarball,
            dev_version,
            public_tarball,
            cee_version,
            development_allow_scripts=development_allow_scripts,
        )

        return {
            "schemaVersion": 1,
            "releaseVersion": release_version,
            "nextDevelopmentVersion": next_version,
            "train": train,
            "sourceVersion": source_version,
            "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "phase": "validated",
            "trainState": {
                "sourceManifest": f"trains/{train}.json",
                "sourceManifestSha256": source_sha256,
                "npmPlan": f"npm/trains/{train}.json",
                "npmPlanSha256": npm_plan_sha256,
                "npmCompletion": f"npm/completed/{train}.json",
                "dockerPlan": f"docker/trains/{train}.json",
                "dockerPlanSha256": _sha256(docker_plan_content),
                "dockerCompletion": f"docker/completed/{train}.json",
                "frontendConfig": frontend_config_url,
                "frontendConfigSha256": frontend_config_sha256,
                "buildConfig": build_config_url,
                "buildConfigSha256": build_config_sha256,
            },
            "sourceRepositories": source.get("repositories"),
            "releaseRepositories": release_repositories,
            "mavenRepositories": maven_repositories,
            "mavenPhases": maven_phases,
            "publicationPlan": publication_plan,
            "dockerFrontendDefaults": docker_frontend_defaults,
            "cee": {
                "development": {
                    "name": DEV_CEE_NAME,
                    "version": dev_version,
                    "integrity": dev_integrity,
                    "tarball": dev_url,
                    "tarballSha256": dev_tarball_sha256,
                    "revision": planned_cee.get("revision"),
                },
                "public": {
                    "name": PUBLIC_CEE_NAME,
                    "version": cee_version,
                    "integrity": public_integrity,
                    "tarball": public_url,
                    "tarballSha256": _sha256(public_tarball),
                    "gitHead": public_record.get("gitHead"),
                },
                "promotionProof": proof,
                "consumers": consumers,
            },
        }

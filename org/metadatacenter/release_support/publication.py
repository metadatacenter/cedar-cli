"""CEDAR release publication."""
from __future__ import annotations
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure
from pathlib import Path, PurePosixPath
import base64
import datetime as dt
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from org.metadatacenter.release_support.distribution import (
    ReleaseDistributionMaterializer,
)
from org.metadatacenter.release_support.errors import (
    ReleaseError,
    RetryableReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _file_sha256,
    _json_bytes,
    _sha256,
)
from org.metadatacenter.release_support.output import (
    console,
)
from org.metadatacenter.release_support.packages import (
    _verify_integrity,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.transport import (
    HttpClient,
    NexusCircuitBreaker,
    _environment_with_nexus_credentials,
    _raise_command_failure,
)
from org.metadatacenter.release_support.validation import (
    ReleaseBuildValidator,
)


class ReleaseArtifactPublisher:
    """Publish only the already-integrated release trees and verify their registry bytes."""

    def __init__(
        self,
        state: ReleaseState,
        http: HttpClient | None = None,
        environment=None,
        executor=None,
        sleeper=None,
        nexus_guard=None,
        progress_reporter=None,
        verbose: bool = False,
    ):
        self.state = state
        self.environment = _environment_with_nexus_credentials(environment)
        self.http = http or HttpClient(environment=self.environment)
        self.executor = executor
        self.sleeper = sleeper or time.sleep
        self.progress_reporter = progress_reporter
        self.verbose = verbose
        self.nexus_guard = nexus_guard or NexusCircuitBreaker(
            self.http, self.environment)

    def ensure_nexus_ready(self, purpose: str) -> None:
        """Refuse a request-heavy registry phase unless Nexus can serve real content."""
        self.nexus_guard.require(purpose)

    @staticmethod
    def _integration_record(manifest: dict, repository: str) -> dict:
        record = manifest.get("remoteIntegration", {}).get("completedTasks", {}).get(repository)
        if not isinstance(record, dict):
            raise ReleaseError(f"release has no verified remote integration for {repository}")
        return record

    def tasks(self, manifest: dict) -> list[dict]:
        plan = manifest.get("publicationPlan")
        if not isinstance(plan, dict):
            raise ReleaseError("release manifest has no artifact publication plan")
        maven = plan.get("maven", {})
        npm = plan.get("npm", {})
        phases = manifest.get("mavenPhases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("release manifest has no Maven publication phases")
        release_workspace = Path(manifest["versionPreparation"]["release"]["workspace"])
        next_workspace = Path(
            manifest["versionPreparation"]["nextDevelopment"]["workspace"]
        )
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        tasks = [{
            "id": "maven:release:publish",
            "kind": "maven-release-upload",
            "variant": "release",
            "version": manifest["releaseVersion"],
            "repository": maven.get("releaseRepository"),
            "localRepository": str(attempt / "build-cache" / "release" / "m2" / "repository"),
        }, {
            "id": "maven:release:verify",
            "kind": "maven-verify",
            "variant": "release",
            "version": manifest["releaseVersion"],
            "repository": maven.get("releaseRepository"),
            "requiredArtifacts": maven.get("requiredArtifacts"),
        }]
        for surface in npm.get("surfaces", []):
            integration = self._integration_record(manifest, surface["repository"])
            task = {
                **surface,
                "id": f"npm:release:{surface['id']}",
                "kind": "npm-release",
                "variant": "release",
                "version": manifest["releaseVersion"],
                "registry": npm.get("registry"),
                "workspace": str(release_workspace),
                "expectedCommit": integration["main"]["commit"],
                "expectedTree": integration["main"]["tree"],
            }
            if surface.get("buildOutput"):
                task["buildEvidenceId"] = f"release:npm:{surface['id']}:build"
                task["distributionEvidenceId"] = (
                    f"release:npm:{surface['id']}:distribution"
                )
            elif surface.get("generatedBuildOutput"):
                task["buildEvidenceId"] = f"release:npm:{surface['id']}:build"
            tasks.append(task)
        return self._checked(tasks)

    @staticmethod
    def _checked(tasks: list[dict]) -> list[dict]:
        identifiers = [task["id"] for task in tasks]
        if len(identifiers) != len(set(identifiers)):
            raise ReleaseError("artifact publication plan contains duplicate tasks")
        return tasks

    @staticmethod
    def _local_ref_record(manifest: dict, variant: str, repository: str) -> dict:
        record = manifest.get("localRefs", {}).get(
            "completedTasks", {}).get(f"{variant}:{repository}")
        if not isinstance(record, dict):
            raise ReleaseError(f"release has no verified {variant} ref for {repository}")
        return record

    def snapshot_tasks(self, manifest: dict) -> list[dict]:
        """Deploy the next-development snapshots, in dependency order, from the prepared trees.

        These run before the remotes are integrated, because integrating them is what makes
        every repository's develop declare the next version, and the CI that each of those
        pushes triggers resolves the parent and libraries at that version from Nexus. Deployed
        afterwards, as they once were, the snapshots arrived minutes too late and left a tail
        of red develop builds that said nothing about the code.

        Running first costs nothing in verification. The trees are the same either way: an
        integration commit is written from the prepared tree and refuses to exist if the
        result differs, so binding to the verified local ref binds to identical bytes.
        """
        plan = manifest.get("publicationPlan")
        if not isinstance(plan, dict):
            raise ReleaseError("release manifest has no artifact publication plan")
        maven = plan.get("maven", {})
        phases = manifest.get("mavenPhases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("release manifest has no Maven publication phases")
        next_workspace = Path(
            manifest["versionPreparation"]["nextDevelopment"]["workspace"]
        )
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        tasks = []
        for phase in phases:
            prepared = self._local_ref_record(manifest, "nextDevelopment", phase["repository"])
            root = next_workspace / phase["repository"]
            tasks.append({
                "id": f"maven:nextDevelopment:{phase['name']}",
                "kind": "maven-snapshot-deploy",
                "variant": "nextDevelopment",
                "version": manifest["nextDevelopmentVersion"],
                "repository": phase["repository"],
                "workspace": str(next_workspace),
                "cwd": str(root),
                "expectedCommit": prepared["commit"],
                "expectedTree": prepared["tree"],
                "command": [
                    str(root / "mvnw"), "--batch-mode", "--no-transfer-progress",
                    f"-Dmaven.repo.local={attempt / 'publication-cache' / 'm2' / 'repository'}",
                    "deploy", "-DskipTests", "-DretryFailedDeploymentCount=3",
                ],
            })
        tasks.append({
            "id": "maven:nextDevelopment:verify",
            "kind": "maven-verify",
            "variant": "nextDevelopment",
            "version": manifest["nextDevelopmentVersion"],
            "repository": maven.get("nextDevelopmentRepository"),
            "requiredArtifacts": maven.get("requiredArtifacts"),
        })
        return self._checked(tasks)

    @staticmethod
    def _workspace_revision(task: dict) -> tuple[str, str]:
        root = Path(task["workspace"]) / task["repository"]
        try:
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                text=True, capture_output=True,
            ).stdout.strip()
            tree = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True,
                text=True, capture_output=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise ReleaseError(f"cannot verify publication workspace {root}: {error}") from error
        return commit, tree

    @classmethod
    def _verify_workspace(
        cls,
        task: dict,
        *,
        allowed_commits: set[str] | None = None,
    ) -> None:
        commit, tree = cls._workspace_revision(task)
        expected_commits = (
            {task["expectedCommit"]} if allowed_commits is None else allowed_commits
        )
        if commit not in expected_commits or tree != task["expectedTree"]:
            raise ReleaseError(f"publication workspace changed for {task['repository']}")

    @classmethod
    def _verify_snapshot_workspace(cls, manifest: dict, task: dict) -> None:
        """Accept the prepared snapshot commit or its recorded develop integration.

        Snapshot publication precedes remote integration and is therefore bound to the
        prepared local ref. Integration subsequently checks out a new develop commit whose
        tree is exactly that prepared tree. Once that repository has integration evidence,
        either checkout is valid evidence for the already-published snapshot; no other
        same-tree commit is.
        """
        completed = manifest.get("remoteIntegration", {}).get("completedTasks", {})
        integration = completed.get(task["repository"]) if isinstance(completed, dict) else None
        if not isinstance(integration, dict):
            cls._verify_workspace(task)
            return
        develop = integration.get("develop")
        if (
            integration.get("repository") != task["repository"]
            or not isinstance(develop, dict)
            or not isinstance(develop.get("commit"), str)
            or develop.get("tree") != task["expectedTree"]
        ):
            raise ReleaseError(
                "remote integration does not preserve the published snapshot tree for "
                f"{task['repository']}"
            )
        cls._verify_workspace(
            task,
            allowed_commits={task["expectedCommit"], develop["commit"]},
        )

    @staticmethod
    def _maven_candidates(local_repository: Path, version: str) -> list[Path]:
        group = local_repository / "org" / "metadatacenter"
        candidates = sorted(
            path for path in group.rglob("*")
            if path.is_file()
            and path.parent.name == version
            and not path.name.startswith(".")
            and path.name != "_remote.repositories"
            and not path.name.startswith("maven-metadata")
            and not path.name.endswith((
                ".lastUpdated", ".sha1", ".md5", ".sha256", ".sha512",
            ))
        ) if group.is_dir() else []
        if not candidates:
            raise ReleaseError(f"validated Maven repository has no files for {version}")
        return candidates

    def _upload(self, destination: str, content: bytes) -> None:
        username = self.environment.get("BMIR_NEXUS_USERNAME")
        password = self.environment.get("BMIR_NEXUS_PASSWORD")
        if not username or not password:
            raise ReleaseError("BMIR_NEXUS_USERNAME and BMIR_NEXUS_PASSWORD are required")
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        request = urllib.request.Request(
            destination, data=content, method="PUT",
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/octet-stream"},
        )
        try:
            with self.http.opener(request, timeout=120) as response:
                if response.status not in (200, 201, 204):
                    raise ReleaseError(
                        f"Nexus returned HTTP {response.status} for {destination}"
                    )
        except urllib.error.HTTPError as error:
            raise ReleaseError(f"cannot publish {destination}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RetryableReleaseError(f"cannot publish {destination}: {error}") from error

    def _publish_maven_release(self, task: dict) -> dict:
        local_repository = Path(task["localRepository"])
        candidates = self._maven_candidates(local_repository, task["version"])
        files = {}
        uploaded = 0
        existing = 0
        self._report_maven_progress(task, 0, len(candidates), uploaded, existing, None, None)
        for completed, source in enumerate(candidates, start=1):
            relative = source.relative_to(local_repository).as_posix()
            content = source.read_bytes()
            destination = task["repository"].rstrip("/") + "/" + relative
            remote = self.http.read(destination, missing_ok=True)
            if remote is None:
                self._upload(destination, content)
                uploaded += 1
                action = "uploaded"
            elif remote != content:
                raise ReleaseError(f"immutable Maven release path contains different bytes: {destination}")
            else:
                existing += 1
                action = "already present"
            files[relative] = _sha256(content)
            self._report_maven_progress(
                task, completed, len(candidates), uploaded, existing, relative, action,
            )
        return {
            **task,
            "files": files,
            "uploadedFiles": uploaded,
            "existingFiles": existing,
            "publishedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def _report_maven_progress(
        self,
        task: dict,
        completed: int,
        total: int,
        uploaded: int,
        existing: int,
        current: str | None,
        action: str | None,
    ) -> None:
        progress = {
            "id": task["id"],
            "kind": task["kind"],
            "completedFiles": completed,
            "totalFiles": total,
            "uploadedFiles": uploaded,
            "existingFiles": existing,
            "currentFile": current,
            "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        if self.progress_reporter is not None:
            self.progress_reporter(progress)
        if self.verbose and current is None:
            console.print(f"Maven release publication: 0/{total} files")
        elif self.verbose:
            console.print(
                f"Maven release publication: {completed}/{total} files; "
                f"{action}: {current}",
                markup=False,
            )

    def _nexus_artifacts(self, repository_url: str, version: str) -> set[str]:
        if not isinstance(repository_url, str) or "/repository/" not in repository_url:
            raise ReleaseError("release manifest contains an invalid Maven repository")
        base = repository_url.split("/repository/", 1)[0]
        repository = repository_url.rstrip("/").rsplit("/", 1)[-1]
        continuation = None
        artifacts = set()
        # Nexus indexes a snapshot component under its expanded timestamped version, such as
        # 2.9.4-20260828.215713-1, so searching for the -SNAPSHOT version matches nothing.
        # maven.baseVersion is the field that keeps the base version, and it applies only to
        # snapshots; release versions are indexed under version itself.
        key = "maven.baseVersion" if version.endswith("-SNAPSHOT") else "version"
        while True:
            query = {"repository": repository, key: version}
            if continuation:
                query["continuationToken"] = continuation
            result = self.http.read_json(
                f"{base}/service/rest/v1/search?{urllib.parse.urlencode(query)}"
            )
            assert result is not None
            payload, _ = result
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise ReleaseError("Nexus search returned an invalid artifact inventory")
            artifacts.update(
                item.get("name") for item in items
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )
            continuation = payload.get("continuationToken")
            if not continuation:
                return artifacts

    def _verify_maven_inventory(self, task: dict, wait: bool) -> dict:
        required = task.get("requiredArtifacts")
        if not isinstance(required, list) or not required:
            raise ReleaseError("Maven publication has no required artifact inventory")
        attempts = 12 if wait else 1
        published = set()
        missing = set(required)
        for attempt in range(attempts):
            published = self._nexus_artifacts(task["repository"], task["version"])
            missing = set(required) - published
            if not missing:
                break
            if attempt + 1 < attempts:
                self.sleeper(10)
        if missing:
            raise ReleaseError(
                f"Nexus is missing required {task['variant']} artifacts: "
                + ", ".join(sorted(missing))
            )
        return {
            **task,
            "verifiedArtifacts": sorted(published),
            "verifiedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def _verify_materialized_distribution(self, task: dict) -> None:
        manifest, _ = self.state.read_current_manifest()
        record = manifest.get("distributionMaterialization", {}).get(
            "completedTasks", {}).get(task.get("distributionEvidenceId"))
        if not isinstance(record, dict):
            raise ReleaseError(f"release has no materialized distribution proof for {task['id']}")
        ReleaseDistributionMaterializer(self.state).verify_record(manifest, record)

    @staticmethod
    def _runtime_file_hashes(root: Path, directories: list[str]) -> dict[str, str]:
        files = {}
        for relative in directories:
            safe = PurePosixPath(relative)
            if safe.is_absolute() or ".." in safe.parts:
                raise ReleaseError(f"unsafe npm runtime directory: {relative}")
            directory = root / relative
            if not directory.is_dir() or directory.is_symlink():
                raise ReleaseError(f"npm runtime directory is missing: {directory}")
            for path in sorted(directory.rglob("*")):
                if path.is_symlink():
                    raise ReleaseError(f"npm runtime assets contain a symbolic link: {path}")
                if path.is_file():
                    files[path.relative_to(root).as_posix()] = _file_sha256(path)
        if directories and not files:
            raise ReleaseError("npm runtime asset plan contains no files")
        return files

    @classmethod
    def _include_runtime_assets(
        cls, tarball: Path, package_root: Path, directories: list[str],
    ) -> dict[str, str]:
        runtime_files = cls._runtime_file_hashes(package_root, directories)
        if not runtime_files:
            return {}
        replacement = tarball.with_name(tarball.name + ".runtime")
        try:
            with tarfile.open(tarball, mode="r:gz") as source, replacement.open("wb") as raw:
                existing = {PurePosixPath(member.name) for member in source.getmembers()}
                additions = {
                    PurePosixPath("package") / relative for relative in runtime_files
                }
                duplicates = existing & additions
                if duplicates:
                    raise ReleaseError(
                        "npm pack unexpectedly included runtime assets: "
                        + ", ".join(str(path) for path in sorted(duplicates))
                    )
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w:") as target:
                        for member in source.getmembers():
                            stream = source.extractfile(member) if member.isfile() else None
                            target.addfile(member, stream)
                        for relative in sorted(runtime_files):
                            path = package_root / relative
                            content = path.read_bytes()
                            member = tarfile.TarInfo(f"package/{relative}")
                            member.size = len(content)
                            member.mode = path.stat().st_mode & 0o777
                            member.mtime = 0
                            member.uid = member.gid = 0
                            member.uname = member.gname = ""
                            target.addfile(member, io.BytesIO(content))
            replacement.replace(tarball)
        except (OSError, tarfile.TarError) as error:
            raise ReleaseError(f"cannot retain npm runtime assets in {tarball}: {error}") from error
        return runtime_files

    @staticmethod
    def _verify_npm_tarball_files(
        identity: str, content: bytes, expected: dict[str, str],
    ) -> None:
        actual = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                for relative, digest in expected.items():
                    name = f"package/{relative}"
                    members = [member for member in archive.getmembers() if member.name == name]
                    if len(members) != 1 or not members[0].isfile():
                        raise ReleaseError(f"{identity} is missing runtime asset {relative}")
                    stream = archive.extractfile(members[0])
                    if stream is None:
                        raise ReleaseError(f"{identity} runtime asset is unreadable: {relative}")
                    actual[relative] = _sha256(stream.read())
        except tarfile.TarError as error:
            raise ReleaseError(f"{identity} is not a readable npm tarball") from error
        if actual != expected:
            raise ReleaseError(f"{identity} runtime assets differ from the validated distribution")

    def _npm_version_record(self, registry: str, name: str, version: str) -> dict | None:
        url = registry.rstrip("/") + "/" + urllib.parse.quote(name, safe="")
        result = self.http.read_json(url, missing_ok=True)
        if result is None:
            return None
        metadata, _ = result
        record = metadata.get("versions", {}).get(version)
        return record if isinstance(record, dict) else None

    @staticmethod
    def _npm_tarball_package(identity: str, content: bytes) -> dict:
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                members = [
                    member for member in archive.getmembers()
                    if PurePosixPath(member.name) == PurePosixPath("package/package.json")
                ]
                if len(members) != 1 or not members[0].isfile():
                    raise ReleaseError(f"{identity} has no unique package/package.json")
                stream = archive.extractfile(members[0])
                if stream is None:
                    raise ReleaseError(f"{identity} package.json is unreadable")
                package = json.load(stream)
        except (tarfile.TarError, json.JSONDecodeError) as error:
            raise ReleaseError(f"{identity} is not a readable npm tarball") from error
        if not isinstance(package, dict):
            raise ReleaseError(f"{identity} package.json is not an object")
        return package

    def _verify_npm_package(self, task: dict, evidence: dict, *, wait: bool) -> dict:
        attempts = 12 if wait else 1
        record = None
        for attempt in range(attempts):
            record = self._npm_version_record(task["registry"], evidence["name"], task["version"])
            if record is not None:
                break
            if attempt + 1 < attempts:
                self.sleeper(10)
        if record is None:
            raise ReleaseError(f"npm registry is missing {evidence['name']}@{task['version']}")
        distribution = record.get("dist", {})
        tarball_url = distribution.get("tarball")
        integrity = distribution.get("integrity")
        if integrity != evidence["integrity"] or not isinstance(tarball_url, str):
            raise ReleaseError(f"npm registry metadata differs for {evidence['name']}@{task['version']}")
        tarball = self.http.read(tarball_url)
        assert tarball is not None
        _verify_integrity(f"{evidence['name']}@{task['version']}", tarball, integrity)
        if _sha256(tarball) != evidence["tarballSha256"]:
            raise ReleaseError(f"npm registry tarball differs for {evidence['name']}@{task['version']}")
        package = self._npm_tarball_package(
            f"{evidence['name']}@{task['version']}", tarball,
        )
        if (
            package.get("name") != evidence["name"]
            or package.get("version") != task["version"]
            or package.get("gitHead") != task["expectedCommit"]
        ):
            raise ReleaseError(f"npm package provenance differs for {evidence['name']}@{task['version']}")
        runtime_files = evidence.get("runtimeFiles")
        if task.get("packedRuntimeDirectories") or task.get("generatedBuildOutput"):
            if not isinstance(runtime_files, dict) or not runtime_files:
                raise ReleaseError(f"npm package has no runtime asset evidence for {task['id']}")
            self._verify_npm_tarball_files(
                f"{evidence['name']}@{task['version']}", tarball, runtime_files,
            )
        return {**evidence, "tarball": tarball_url, "verifiedAt": dt.datetime.now(dt.timezone.utc).isoformat()}

    def _pack_npm(self, task: dict) -> tuple[Path, dict]:
        self._verify_workspace(task)
        workspace = Path(task["workspace"])
        root = workspace / task["repository"]
        if task.get("buildOutput"):
            build_record = (
                self.state.read_current_manifest()[0]
                .get("buildValidation", {})
                .get("completedTasks", {})
                .get(task.get("buildEvidenceId"))
            )
            expected_output = str(root / task["buildOutput"])
            if (
                not isinstance(build_record, dict)
                or build_record.get("buildOutput") != expected_output
            ):
                raise ReleaseError(f"release has no build-output proof for {task['id']}")
            ReleaseBuildValidator.verify_completed_task(build_record)
            self._verify_materialized_distribution(task)
        attempt = Path(workspace).parent
        publication_root = attempt / "publication-packages"
        publication_root.mkdir(parents=True, exist_ok=True)
        npm_environment = dict(self.environment)
        npm_environment["npm_config_cache"] = str(attempt / "publication-cache" / "npm")
        stage = Path(tempfile.mkdtemp(prefix=f"{task['id'].replace(':', '-')}-", dir=publication_root))
        archive = stage / "source.tar"
        try:
            subprocess.run([
                "git", "-C", str(root), "archive", "--format=tar", f"--output={archive}",
                task["expectedCommit"],
            ], check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", None) or str(error)
            raise ReleaseError(f"cannot archive {task['repository']}: {detail.strip()}") from error
        source_root = stage / "source"
        source_root.mkdir()
        try:
            with tarfile.open(archive, mode="r:") as content:
                content.extractall(source_root, filter="data")
        except (OSError, tarfile.TarError) as error:
            raise ReleaseError(f"cannot extract {task['repository']} release source") from error
        package_root = source_root if task["directory"] == "." else source_root / task["directory"]
        generated_files = {}
        generated_output = task.get("generatedBuildOutput")
        if generated_output:
            generated_relative = PurePosixPath(generated_output)
            if generated_relative.is_absolute() or ".." in generated_relative.parts:
                raise ReleaseError(f"unsafe generated npm output for {task['id']}")
            manifest, _ = self.state.read_current_manifest()
            build_record = manifest.get("buildValidation", {}).get(
                "completedTasks", {}).get(task.get("buildEvidenceId"))
            expected_output = str(root / generated_output)
            if (
                not isinstance(build_record, dict)
                or build_record.get("buildOutput") != expected_output
            ):
                raise ReleaseError(f"release has no generated build-output proof for {task['id']}")
            ReleaseBuildValidator.verify_completed_task(build_record)
            destination = package_root / generated_relative
            if destination.exists():
                raise ReleaseError(
                    f"generated npm output collides with archived source for {task['id']}")
            shutil.copytree(Path(build_record["buildOutput"]), destination)
            generated_files = {
                f"{generated_output}/{relative}": digest
                for relative, digest in build_record["outputFiles"].items()
            }
        package_path = package_root / "package.json"
        try:
            package = json.loads(package_path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(f"cannot read staged npm package {package_path}: {error}") from error
        if package.get("version") != task["version"]:
            raise ReleaseError(f"{package_path} does not identify release {task['version']}")
        registry = package.get("publishConfig", {}).get("registry")
        if registry != task["registry"]:
            raise ReleaseError(f"{package_path} does not publish to the planned registry")
        package["gitHead"] = task["expectedCommit"]
        try:
            package_path.write_bytes(_json_bytes(package))
        except OSError as error:
            raise ReleaseError(f"cannot stamp npm provenance in {package_path}: {error}") from error
        try:
            pack = subprocess.run([
                "npm", "pack", str(package_root), "--pack-destination", str(stage),
                "--ignore-scripts", "--json",
            ], check=False, text=True, capture_output=True, env=npm_environment)
        except OSError as error:
            raise ReleaseError(f"cannot run npm pack for {task['id']}: {error}") from error
        if pack.returncode:
            detail = (pack.stderr or pack.stdout).strip()
            if pack.returncode < 0 and not detail:
                detail = "the process produced no diagnostic output of its own"
            raise ReleaseError(
                f"npm pack {describe_subprocess_failure(pack.returncode)} "
                f"for {task['id']}: {detail}"
            )
        try:
            packed = json.loads(pack.stdout)
            filename = packed[0]["filename"]
            if not isinstance(filename, str) or PurePosixPath(filename).name != filename:
                raise ReleaseError(f"npm pack returned an unsafe filename for {task['id']}")
            tarball_path = stage / filename
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ReleaseError(f"npm pack returned invalid evidence for {task['id']}") from error
        runtime_files = self._include_runtime_assets(
            tarball_path, package_root, task.get("packedRuntimeDirectories", []),
        )
        runtime_files.update(generated_files)
        if generated_files:
            self._verify_npm_tarball_files(
                f"packed {task['id']}", tarball_path.read_bytes(), generated_files,
            )
        try:
            content = tarball_path.read_bytes()
        except OSError as error:
            raise ReleaseError(f"cannot read npm tarball for {task['id']}: {error}") from error
        return tarball_path, {
            "name": package["name"],
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode(),
            "tarballSha256": _sha256(content),
            "packedTarball": str(tarball_path),
            "runtimeFiles": runtime_files,
        }

    def _publish_npm(self, task: dict) -> dict:
        tarball, evidence = self._pack_npm(task)
        existing = self._npm_version_record(task["registry"], evidence["name"], task["version"])
        attempt = Path(task["workspace"]).parent
        log = attempt / "publication-logs" / f"{task['id'].replace(':', '-')}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        output = "already present; publication skipped"
        if existing is None:
            command = [
                "npm", "publish", str(tarball), "--tag", "latest",
                "--registry", task["registry"], "--loglevel=notice",
            ]
            npm_environment = dict(self.environment)
            npm_environment["npm_config_cache"] = str(
                Path(task["workspace"]).parent / "publication-cache" / "npm"
            )
            try:
                result = subprocess.run(
                    command, check=False, text=True, capture_output=True,
                    env=npm_environment)
            except OSError as error:
                raise ReleaseError(
                    f"cannot run npm publish for {evidence['name']}: {error}"
                ) from error
            output = "\n".join(
                part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
            log.write_text(output + "\n", encoding="utf-8")
            if output and self.verbose:
                console.print(output, markup=False)
            if result.returncode:
                if result.returncode < 0 and not output:
                    output = "the process produced no diagnostic output of its own"
                _raise_command_failure(
                    command,
                    f"npm publish {describe_subprocess_failure(result.returncode)}: "
                    f"{evidence['name']}",
                    output,
                )
        if existing is not None:
            log.write_text(output + "\n", encoding="utf-8")
        verified = self._verify_npm_package(task, evidence, wait=True)
        return {
            **task,
            **verified,
            "log": str(log),
            "logSha256": _file_sha256(log),
            "publishedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def _deploy_snapshot(self, manifest: dict, task: dict) -> dict:
        self._verify_workspace(task)
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        log = attempt / "publication-logs" / f"{task['id'].replace(':', '-')}.log"
        environment = dict(self.environment)
        environment["CEDAR_HOME"] = task["workspace"]
        ReleaseBuildValidator._stream_command(
            task["command"], Path(task["cwd"]), environment, log, verbose=self.verbose,
        )
        return {
            **task,
            "log": str(log),
            "logSha256": _file_sha256(log),
            "publishedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def run_task(self, manifest: dict, task: dict) -> dict:
        if self.executor is not None:
            return self.executor(manifest, task)
        if task["kind"] == "maven-release-upload":
            return self._publish_maven_release(task)
        if task["kind"] == "maven-verify":
            return self._verify_maven_inventory(task, wait=True)
        if task["kind"] == "npm-release":
            return self._publish_npm(task)
        if task["kind"] == "maven-snapshot-deploy":
            return self._deploy_snapshot(manifest, task)
        raise ReleaseError(f"unknown artifact publication task {task['kind']}")

    def verify_record(self, manifest: dict, record: dict, tasks: list[dict] | None = None) -> None:
        available = self.tasks(manifest) if tasks is None else tasks
        task = next((item for item in available if item["id"] == record.get("id")), None)
        if task is None or task.get("kind") != record.get("kind"):
            raise ReleaseError(f"recorded publication task no longer exists: {record.get('id')}")
        if self.executor is not None:
            return
        if task["kind"] == "maven-release-upload":
            local_repository = Path(task["localRepository"])
            files = record.get("files")
            if not isinstance(files, dict) or not files:
                raise ReleaseError("recorded Maven release has no file evidence")
            for relative, digest in files.items():
                local = local_repository / relative
                if _file_sha256(local) != digest:
                    raise ReleaseError(f"recorded Maven release input changed: {local}")
                remote = self.http.read(task["repository"].rstrip("/") + "/" + relative)
                if remote is None or _sha256(remote) != digest:
                    raise ReleaseError(f"recorded Maven release artifact changed: {relative}")
        elif task["kind"] == "maven-verify":
            self._verify_maven_inventory(task, wait=False)
        elif task["kind"] == "npm-release":
            self._verify_workspace(task)
            self._verify_npm_package(task, record, wait=False)
            log = Path(record.get("log", ""))
            if not log.is_file() or _file_sha256(log) != record.get("logSha256"):
                raise ReleaseError(f"recorded npm publication log changed for {task['id']}")
        elif task["kind"] == "maven-snapshot-deploy":
            self._verify_snapshot_workspace(manifest, task)
            log = Path(record.get("log", ""))
            if not log.is_file() or _file_sha256(log) != record.get("logSha256"):
                raise ReleaseError(f"recorded publication log changed for {task['id']}")

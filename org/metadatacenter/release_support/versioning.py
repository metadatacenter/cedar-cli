"""CEDAR release versioning."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import datetime as dt
import json
import re
import shutil
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _file_sha256,
)
from org.metadatacenter.release_support.policy import (
    LICENSE_COPYRIGHT_RE,
    LICENSE_FILE_NAME,
    MAVEN_GENERATED_VERSION_FILES,
    NEXT_VERSION_RE,
    NPM_VERSION_SURFACES,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.workspace import (
    ReleaseWorkspacePreparer,
)


class ReleaseVersionPreparer:
    """Create release and next-development source variants from the same train commits."""

    def __init__(self, state: ReleaseState, workspace_preparer=None):
        self.state = state
        self.workspace_preparer = workspace_preparer or ReleaseWorkspacePreparer(state)

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def _stamp_npm_surface(cls, root: Path, relative: str, old: str, new: str) -> set[str]:
        surface = root if relative == "." else root / relative
        manifest_path = surface / "package.json"
        lock_path = surface / "package-lock.json"
        try:
            package = json.loads(manifest_path.read_bytes())
            lock = json.loads(lock_path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(f"cannot read npm version surface {surface}: {error}") from error
        if package.get("version") != old:
            raise ReleaseError(
                f"{manifest_path} version is {package.get('version')!r}, expected train source {old}"
            )
        if lock.get("version") != old:
            raise ReleaseError(
                f"{lock_path} version is {lock.get('version')!r}, expected train source {old}"
            )
        lock_root = lock.get("packages", {}).get("")
        if not isinstance(lock_root, dict) or lock_root.get("version") != old:
            raise ReleaseError(f"{lock_path} root package does not have train source version {old}")
        package["version"] = new
        lock["version"] = new
        lock_root["version"] = new
        cls._write_json(manifest_path, package)
        cls._write_json(lock_path, lock)
        prefix = "" if relative == "." else f"{relative}/"
        return {f"{prefix}package.json", f"{prefix}package-lock.json"}

    @staticmethod
    def _replace_exact(path: Path, old: bytes, new: bytes) -> bool:
        content = path.read_bytes()
        if old not in content:
            return False
        path.write_bytes(content.replace(old, new))
        return True

    @classmethod
    def _stamp_maven(cls, root: Path, old: str, new: str) -> set[str]:
        changed = set()
        for path in sorted(root.rglob("pom.xml")):
            if cls._replace_exact(path, old.encode(), new.encode()):
                changed.add(path.relative_to(root).as_posix())
        if not changed:
            raise ReleaseError(f"{root.name} has no Maven version {old} to stamp")
        for relative, pattern in MAVEN_GENERATED_VERSION_FILES.get(root.name, {}).items():
            path = root / relative
            old_marker = pattern.format(old).encode()
            new_marker = pattern.format(new).encode()
            try:
                content = path.read_bytes()
            except OSError as error:
                raise ReleaseError(f"cannot read tracked generated version file {path}: {error}") from error
            if content.count(old_marker) != 1:
                raise ReleaseError(
                    f"{path} does not contain exactly one generated API version {old}"
                )
            path.write_bytes(content.replace(old_marker, new_marker))
            changed.add(relative)
        return changed

    @classmethod
    def _stamp_development(cls, root: Path, old: str, new: str) -> set[str]:
        relative = "bin/util/set-env-generic.sh"
        path = root / relative
        changed = cls._replace_exact(
            path,
            f"export CEDAR_VERSION={old}".encode(),
            f"export CEDAR_VERSION={new}".encode(),
        )
        if not changed:
            raise ReleaseError(f"{path} does not declare train source version {old}")
        return {relative}

    @classmethod
    def _stamp_docker_build(
        cls, root: Path, old: str, new: str, frontend_defaults: dict | None = None,
    ) -> set[str]:
        changed = set()
        for path in sorted(root.rglob("Dockerfile")):
            if cls._replace_exact(
                path,
                f"ENV CEDAR_VERSION={old}".encode(),
                f"ENV CEDAR_VERSION={new}".encode(),
            ):
                changed.add(path.relative_to(root).as_posix())
        base = root / "bin" / "cedar-images-base.sh"
        for variable in (
            "IMAGE_VERSION", "CEDAR_MAVEN_VERSION", "CEDAR_APPLICATION_VERSION",
        ):
            if not cls._replace_exact(
                base,
                f"export {variable}={old}".encode(),
                f"export {variable}={new}".encode(),
            ):
                raise ReleaseError(
                    f"{base} does not declare {variable} at train source version {old}")
            changed.add(base.relative_to(root).as_posix())
        for variable, value in sorted((frontend_defaults or {}).items()):
            cls._replace_default(base, variable, value)
        if not changed:
            raise ReleaseError(f"{root.name} has no Docker version {old} to stamp")
        return changed

    @staticmethod
    def _replace_default(base: Path, variable: str, value: str) -> bool:
        """Point one `export VARIABLE=` line at a new value, whatever it named before.

        The compatibility defaults name whichever development packages the last operator pinned,
        so unlike the three version variables they cannot be matched by their old value. A
        variable the train's inputs name but the script does not declare is a configuration
        drift the release must not paper over.
        """
        content = base.read_text(encoding="utf-8")
        pattern = re.compile(rf"^export {re.escape(variable)}=.*$", re.MULTILINE)
        matches = pattern.findall(content)
        if len(matches) != 1:
            raise ReleaseError(
                f"{base} must declare {variable} exactly once; the train's Docker inputs name it "
                f"and found {len(matches)} declaration(s)")
        replacement = f"export {variable}={value}"
        if matches[0] == replacement:
            return False
        base.write_text(pattern.sub(replacement, content), encoding="utf-8")
        return True

    @classmethod
    def _stamp_docker_deploy(cls, root: Path, old: str, new: str) -> set[str]:
        changed = set()
        for path in sorted(root.rglob(".env")):
            if cls._replace_exact(
                path,
                f"CEDAR_DOCKER_VERSION={old}".encode(),
                f"CEDAR_DOCKER_VERSION={new}".encode(),
            ):
                changed.add(path.relative_to(root).as_posix())
        if not changed:
            raise ReleaseError(f"{root.name} has no deployment version {old} to stamp")
        return changed

    @classmethod
    def _stamp_license(cls, root: Path, year: str) -> set[str]:
        """Move the copyright year forward so releases, rather than January, keep it current.

        Only the year moves, and only on a copyright line of the shape preflight recognised.
        A licence that does not carry one is left alone rather than rewritten to a guess.
        """
        path = root / LICENSE_FILE_NAME
        if not path.is_file():
            return set()
        text = path.read_text(encoding="utf-8")
        match = LICENSE_COPYRIGHT_RE.search(text)
        if match is None or match.group(1) == year:
            return set()
        path.write_text(text[:match.start(1)] + year + text[match.end(1):], encoding="utf-8")
        return {LICENSE_FILE_NAME}

    @classmethod
    def _stamp_repository(
        cls,
        repository: str,
        root: Path,
        old: str,
        new: str,
        maven_repositories: set[str],
        copyright_year: str,
        docker_frontend_defaults: dict | None = None,
    ) -> set[str]:
        changed = cls._stamp_license(root, copyright_year)
        return changed | cls._stamp_versions(
            repository, root, old, new, maven_repositories, docker_frontend_defaults,
        )

    @classmethod
    def _stamp_versions(
        cls,
        repository: str,
        root: Path,
        old: str,
        new: str,
        maven_repositories: set[str],
        docker_frontend_defaults: dict | None = None,
    ) -> set[str]:
        if repository in maven_repositories:
            return cls._stamp_maven(root, old, new)
        if repository in NPM_VERSION_SURFACES:
            changed = set()
            for relative in NPM_VERSION_SURFACES[repository]:
                changed.update(cls._stamp_npm_surface(root, relative, old, new))
            return changed
        if repository == "cedar-development":
            return cls._stamp_development(root, old, new)
        if repository == "cedar-docker-build":
            return cls._stamp_docker_build(root, old, new, docker_frontend_defaults)
        if repository == "cedar-docker-deploy":
            return cls._stamp_docker_deploy(root, old, new)
        return set()

    def _ensure_clone(self, repository: str, revision: str, destination: Path) -> None:
        if (destination / ".git").exists():
            actual = self.workspace_preparer._run([
                "git", "-C", str(destination), "rev-parse", "HEAD",
            ])
            if actual != revision:
                raise ReleaseError(
                    f"existing isolated clone for {repository} is {actual}, expected {revision}"
                )
            return
        self.workspace_preparer._clone(repository, revision, destination)

    def _actual_changes(self, root: Path) -> set[str]:
        changed = set(filter(None, self.workspace_preparer._run([
            "git", "-C", str(root), "diff", "--name-only", "HEAD", "--",
        ]).splitlines()))
        untracked = set(filter(None, self.workspace_preparer._run([
            "git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
        ]).splitlines()))
        return changed | untracked

    @classmethod
    def _refresh_train_audit_baselines(cls, workspace: Path) -> bool:
        config_path = workspace / "cedar-development" / "ops" / "frontend-train.json"
        if not config_path.is_file():
            return False
        try:
            config = json.loads(config_path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(f"cannot read train audit baselines {config_path}: {error}") from error
        baselines = config.get("auditBaselines")
        if not isinstance(baselines, list) or not baselines:
            raise ReleaseError(f"{config_path} has no npm audit baselines")
        changed = False
        for baseline in baselines:
            if not isinstance(baseline, dict):
                raise ReleaseError(f"{config_path} contains an invalid npm audit baseline")
            repository = baseline.get("repository")
            relative = baseline.get("lock")
            if not isinstance(repository, str) or not isinstance(relative, str):
                raise ReleaseError(f"{config_path} contains an unnamed npm audit baseline")
            lock = workspace / repository / relative
            if not lock.is_file():
                # CEE and the TypeScript model publish independently and are deliberately
                # absent from a CEDAR release workspace; their source locks did not move.
                continue
            digest = _file_sha256(lock)
            if baseline.get("sha256") != digest:
                baseline["sha256"] = digest
                changed = True
        if changed:
            cls._write_json(config_path, config)
        return changed

    def prepare(self, manifest: dict) -> dict:
        frontend = manifest.get("frontendPreparation", {})
        release_workspace = Path(frontend.get("workspace", ""))
        if not release_workspace.is_dir():
            raise ReleaseError("release frontend workspace is missing")
        attempt = release_workspace.parent
        next_workspace = attempt / "next-workspace"
        release_repositories = manifest.get("releaseRepositories")
        repositories = manifest.get("sourceRepositories")
        maven_repositories = set(manifest.get("mavenRepositories", []))
        if not isinstance(release_repositories, list) or not release_repositories:
            raise ReleaseError("release manifest has no release repository set")
        if not isinstance(repositories, dict):
            raise ReleaseError("release manifest has no source repository inventory")
        old = manifest.get("sourceVersion")
        if not isinstance(old, str) or not NEXT_VERSION_RE.fullmatch(old):
            raise ReleaseError("release manifest has no explicit train source SNAPSHOT version")
        release_version = manifest["releaseVersion"]
        next_version = manifest["nextDevelopmentVersion"]
        copyright_year = str(dt.datetime.fromisoformat(manifest["createdAt"]).year)
        if old == next_version:
            raise ReleaseError("next development version must differ from the train source version")

        for repository, revision in repositories.items():
            self._ensure_clone(repository, revision, next_workspace / repository)
        for repository in release_repositories:
            revision = repositories.get(repository)
            self._ensure_clone(repository, revision, release_workspace / repository)

        cee_allowed_by_repo: dict[str, set[str]] = {}
        for consumer in manifest["cee"]["consumers"]:
            cee_allowed_by_repo.setdefault(consumer["repository"], set()).update({
                consumer["manifest"], consumer["lock"],
            })

        for repository, relative_paths in cee_allowed_by_repo.items():
            for relative in relative_paths:
                source = release_workspace / repository / relative
                destination = next_workspace / repository / relative
                expected = next(
                    record[f"{kind}Sha256"]
                    for record in frontend["consumers"]
                    if record["repository"] == repository
                    for kind in ("manifest", "lock")
                    if record[kind] == relative
                )
                if _file_sha256(source) != expected:
                    raise ReleaseError(f"prepared CEE consumer changed before version stamping: {source}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        docker_frontend_defaults = manifest.get("dockerFrontendDefaults") or {}
        variants = {}
        for variant, workspace, target in (
            ("release", release_workspace, release_version),
            ("nextDevelopment", next_workspace, next_version),
        ):
            records = {}
            stamped_by_repository = {}
            for repository in release_repositories:
                root = workspace / repository
                stamped_by_repository[repository] = self._stamp_repository(
                    repository, root, old, target, maven_repositories, copyright_year,
                    docker_frontend_defaults.get(variant),
                )
            if self._refresh_train_audit_baselines(workspace):
                stamped_by_repository.setdefault("cedar-development", set()).add(
                    "ops/frontend-train.json")
            for repository in release_repositories:
                root = workspace / repository
                stamped = stamped_by_repository[repository]
                allowed = set(stamped)
                allowed.update(cee_allowed_by_repo.get(repository, set()))
                actual = self._actual_changes(root)
                unexpected = sorted(actual - allowed)
                missing = sorted(allowed - actual)
                if unexpected or missing:
                    raise ReleaseError(
                        f"{variant} stamping produced an invalid change set for {repository}: "
                        f"unexpected={unexpected}, missing={missing}"
                    )
                records[repository] = {
                    "revision": repositories[repository],
                    "changedFiles": sorted(actual),
                    "fileSha256": {
                        relative: _file_sha256(root / relative)
                        for relative in sorted(actual)
                    },
                }
            variants[variant] = {
                "version": target,
                "workspace": str(workspace),
                "repositories": records,
            }
        return {
            "preparedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sourceVersion": old,
            **variants,
        }

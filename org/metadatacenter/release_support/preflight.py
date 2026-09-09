"""CEDAR release preflight."""
from __future__ import annotations
from org.metadatacenter import smoke_gate
from org.metadatacenter.github_ci import (
    GREEN_CONCLUSIONS,
    GithubCIProbeError,
    latest_runs_by_name,
    probe_exact_commit,
    run_url,
)
from org.metadatacenter.npm_policy import (
    npm_user_config_findings,
    unreviewed_install_scripts,
)
from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError,
    embedded_mongo_processes,
    require_no_embedded_mongo_processes,
    wait_for_no_embedded_mongo_processes,
)
from pathlib import Path, PurePosixPath
import dataclasses
import fnmatch
import json
import re
import shutil
import subprocess
import time
import urllib.request
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.integration import (
    ReleaseRemoteIntegrator,
)
from org.metadatacenter.release_support.lifecycle import (
    _next_release_stage,
)
from org.metadatacenter.release_support.output import (
    console,
)
from org.metadatacenter.release_support.policy import (
    CHECKOUT_BYTES_PER_REPOSITORY,
    FRONTEND_BUILD_SURFACES,
    FRONTEND_BYTES_PER_SURFACE_VARIANT,
    GENERATED_VERSION_FILE_GLOBS,
    LICENSE_COPYRIGHT_RE,
    LICENSE_FILE_NAME,
    MAVEN_BYTES_PER_REPOSITORY_VARIANT,
    MAVEN_GENERATED_VERSION_FILES,
    MINIMUM_SPACE_HEADROOM_BYTES,
    NEXUS_AUTHENTICATED_ENDPOINT,
    NEXUS_HOST,
    NEXUS_NPM_REGISTRY,
    NEXUS_REPOSITORY_PROBE,
    NEXUS_WRITABLE_ENDPOINT,
    NPM_VERSION_SURFACES,
    PROFILE_COMMAND,
    PROFILE_REQUIRED_VARIABLES,
    PUBLICATION_CACHE_AND_LOG_BYTES,
    REQUIRED_JAVA_MAJOR,
    REQUIRED_NODE_VERSION,
    REQUIRED_TOOLS,
    SPACE_HEADROOM_PERCENT,
    _integration_repositories,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.toolchain import (
    java_17_remediation,
    node_24_remediation,
)
from org.metadatacenter.release_support.transport import (
    HttpClient,
    _environment_with_nexus_credentials,
)


@dataclasses.dataclass(frozen=True)
class PreflightFinding:
    """One settled precondition, carrying the action that clears it."""

    check: str
    severity: str
    message: str
    remedy: str = ""

    @property
    def fatal(self) -> bool:
        return self.severity == "fail"


@dataclasses.dataclass(frozen=True)
class ReleaseSpaceBudget:
    components: dict[str, int]
    headroom_bytes: int

    @property
    def required_bytes(self) -> int:
        return sum(self.components.values()) + self.headroom_bytes

    def summary(self) -> str:
        gib = 1024 ** 3
        parts = [f"{name} {value / gib:.1f} GiB" for name, value in self.components.items()]
        parts.append(f"headroom {self.headroom_bytes / gib:.1f} GiB")
        return ", ".join(parts)


class ReleaseSpaceEstimator:
    """Estimate peak disposable space from the work declared by a release manifest."""

    def __init__(self, manifest: dict):
        self.manifest = manifest

    def estimate(self) -> ReleaseSpaceBudget:
        source_repositories = len(self.manifest.get("sourceRepositories", {}))
        release_repositories = len(self.manifest.get("releaseRepositories", []))
        maven_repositories = len(self.manifest.get("mavenRepositories", []))
        frontend_surfaces = len(FRONTEND_BUILD_SURFACES)
        components = {
            "clean checkouts": (
                source_repositories + release_repositories
            ) * CHECKOUT_BYTES_PER_REPOSITORY,
            "Maven release/next builds": (
                maven_repositories * 2 * MAVEN_BYTES_PER_REPOSITORY_VARIANT
            ),
            "frontend release/next builds": (
                frontend_surfaces * 2 * FRONTEND_BYTES_PER_SURFACE_VARIANT
            ),
            "publication caches and logs": PUBLICATION_CACHE_AND_LOG_BYTES,
        }
        subtotal = sum(components.values())
        headroom = max(
            MINIMUM_SPACE_HEADROOM_BYTES,
            subtotal * SPACE_HEADROOM_PERCENT // 100,
        )
        return ReleaseSpaceBudget(components, headroom)


class ReleasePreflight:
    """Settle every release precondition that is knowable before the first build.

    The 2.9.3 release failed five times, and each failure was a condition that already
    held when the release started: undeclared generated files, absent Nexus credentials,
    a blocked push to main, a red develop, and content carried only on main. Each cost
    the hours of Maven and frontend building that preceded the phase that noticed. Every
    check here answers one of those questions from local state or a cheap remote read, so
    a release either refuses in its first minute or runs with its preconditions settled.
    """

    CHECKS = (
        "check_no_release_in_progress",
        "check_toolchain",
        "check_embedded_test_processes",
        "check_profile",
        "check_disk_space",
        "check_working_trees",
        "check_git_identity",
        "check_nexus_authorization",
        "check_npm_authorization",
        "check_npm_configuration",
        "check_push_permission",
        "check_target_version_unused",
        "check_target_artifacts_unused",
        "check_develop_is_green",
        "check_smoke_gate",
        "check_source_contract",
        "check_generated_version_files",
        "check_license_files",
        "check_remote_survey",
    )

    def __init__(
        self,
        manifest: dict,
        *,
        state: "ReleaseState | None" = None,
        command_runner=None,
        http: HttpClient | None = None,
        environment=None,
        accepted_red_develop: dict[str, str] | None = None,
        accepted_main_only: set[str] | None = None,
        space_estimator: ReleaseSpaceEstimator | None = None,
        ci_sleeper=time.sleep,
        ci_delays: tuple[float, ...] = (2, 5, 10),
    ):
        self.manifest = manifest
        self.environment = _environment_with_nexus_credentials(environment)
        self.state = state or ReleaseState()
        self.command_runner = command_runner or subprocess.run
        self.http = http or HttpClient(environment=self.environment)
        self.accepted_red_develop = dict(accepted_red_develop or {})
        self.accepted_main_only = set(accepted_main_only or ())
        self.space_estimator = space_estimator or ReleaseSpaceEstimator(manifest)
        self.ci_sleeper = ci_sleeper
        self.ci_delays = ci_delays
        self._source_path_cache: dict[str, list[str]] = {}

    @property
    def repositories(self) -> list[str]:
        return _integration_repositories(self.manifest)

    def _root(self, repository: str) -> Path:
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        return Path(cedar_home) / repository

    def _capture(self, args: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
        """Run a command and report its outcome instead of raising on failure.

        A failing command is the answer to several checks rather than an error, so
        preflight needs the return code where the release runner needs an exception.
        """
        try:
            result = self.command_runner(
                args,
                cwd=str(cwd) if cwd else None,
                env=self.environment,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            return 127, "", str(error)
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()

    def run(self) -> list[PreflightFinding]:
        findings: list[PreflightFinding] = []
        for name in self.CHECKS:
            findings.extend(getattr(self, name)())
        return findings

    def run_resume(self) -> list[PreflightFinding]:
        """Recheck only conditions still relevant to the recorded next stage."""
        stage = _next_release_stage(self.manifest)
        checks = [
            "check_toolchain", "check_embedded_test_processes",
            "check_profile", "check_disk_space",
        ]
        if stage != "acceptance":
            checks.append("check_npm_configuration")
        if stage in {"frontends", "versions", "builds"}:
            checks.extend([
                "check_working_trees", "check_git_identity",
                "check_nexus_authorization", "check_npm_authorization",
                "check_push_permission", "check_target_version_unused",
                "check_target_artifacts_unused", "check_source_contract",
                "check_develop_is_green", "check_smoke_gate",
                "check_generated_version_files", "check_license_files",
                "check_remote_survey",
            ])
        elif stage == "local-refs":
            checks.extend([
                "check_git_identity", "check_nexus_authorization",
                "check_npm_authorization", "check_push_permission",
                "check_target_version_unused", "check_target_artifacts_unused",
                "check_remote_survey",
            ])
        elif stage == "snapshots":
            checks.extend([
                "check_nexus_authorization", "check_npm_authorization",
                "check_push_permission", "check_target_version_unused",
                "check_target_artifacts_unused", "check_remote_survey",
            ])
        elif stage == "remotes":
            checks.extend([
                "check_nexus_authorization", "check_npm_authorization",
                "check_push_permission", "check_target_artifacts_unused",
            ])
        elif stage == "artifacts":
            checks.extend(["check_nexus_authorization", "check_npm_authorization"])
        elif stage == "acceptance":
            checks.append("check_nexus_authorization")
        findings = []
        for name in checks:
            findings.extend(getattr(self, name)())
        return findings

    def check_no_release_in_progress(self) -> list[PreflightFinding]:
        """A release already holds the slot, and start would refuse only after planning."""
        try:
            current = self.state.read_current()
        except ReleaseError:
            return []
        if current.get("concludedAt"):
            return []
        active = current.get("releaseVersion")
        if active == self.manifest.get("releaseVersion"):
            return [PreflightFinding(
                "state", "fail", f"release {active} is already active",
                "cedarcli release resume, or cedarcli release status",
            )]
        return [PreflightFinding(
            "state", "fail",
            f"release {active} is still active and has not reached acceptance",
            "finish it with cedarcli release resume; acceptance releases the slot",
        )]

    def check_toolchain(self) -> list[PreflightFinding]:
        findings = []
        for tool in REQUIRED_TOOLS:
            if shutil.which(tool, path=self.environment.get("PATH")) is None:
                findings.append(PreflightFinding(
                    "toolchain", "fail", f"{tool} is not on PATH",
                    f"install {tool} and make it available to the release shell",
                ))
        code, _, stderr = self._capture(["java", "-version"])
        if code != 0:
            findings.append(PreflightFinding(
                "toolchain", "fail", "java is not on PATH",
                java_17_remediation(),
            ))
            return findings
        match = re.search(r'version "(\d+)', stderr)
        major = int(match.group(1)) if match else None
        if major != REQUIRED_JAVA_MAJOR:
            findings.append(PreflightFinding(
                "toolchain", "fail",
                f"Java {major or 'of unknown version'} is active, and CEDAR builds require "
                f"Java {REQUIRED_JAVA_MAJOR}",
                java_17_remediation(),
            ))
        code, node, stderr = self._capture(["node", "--version"])
        if code != 0 or node != REQUIRED_NODE_VERSION:
            findings.append(PreflightFinding(
                "toolchain", "fail",
                f"Node {node or 'of unknown version'} is active, and release builds require "
                f"{REQUIRED_NODE_VERSION}",
                node_24_remediation(),
            ))
        return findings

    def check_embedded_test_processes(self) -> list[PreflightFinding]:
        try:
            processes = embedded_mongo_processes()
        except BuildSafetyError as error:
            return [PreflightFinding("test-processes", "fail", str(error))]
        if not processes:
            return []
        detail = ", ".join(process.describe() for process in processes)
        return [PreflightFinding(
            "test-processes", "fail",
            f"embedded Mongo test process(es) remain before the release build: {detail}",
            "cedarcli test status; cedarcli test cleanup",
        )]

    def check_profile(self) -> list[PreflightFinding]:
        missing = [name for name in PROFILE_REQUIRED_VARIABLES if not self.environment.get(name)]
        if not missing:
            return []
        return [PreflightFinding(
            "profile", "fail",
            "the CEDAR profile is not sourced, so " + ", ".join(missing) + " are undefined",
            PROFILE_COMMAND,
        )]

    def check_disk_space(self) -> list[PreflightFinding]:
        try:
            free = shutil.disk_usage(self.state.root.parent).free
        except OSError as error:
            return [PreflightFinding("disk", "warn", f"cannot measure free space: {error}")]
        budget = self.space_estimator.estimate()
        if free >= budget.required_bytes:
            return []
        return [PreflightFinding(
            "disk", "fail",
            f"{free / 1024 ** 3:.1f} GiB free, but this release is estimated to need "
            f"{budget.required_bytes / 1024 ** 3:.1f} GiB ({budget.summary()})",
            "free space; obsolete release attempts are removed automatically when a release starts",
        )]

    def check_working_trees(self) -> list[PreflightFinding]:
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                findings.append(PreflightFinding(
                    "working-tree", "fail", f"{repository} is not checked out at {root}",
                    f"cedarcli git clone {repository}",
                ))
                continue
            code, branch, _ = self._capture(
                ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])
            if code != 0:
                findings.append(PreflightFinding(
                    "working-tree", "fail", f"{repository} is not a readable git repository"))
                continue
            if branch != "develop":
                findings.append(PreflightFinding(
                    "working-tree", "fail", f"{repository} is on {branch} rather than develop",
                    f"git -C {root} switch develop",
                ))
            # Untracked files are ordinary in a development tree, and the release builds from
            # the train's commits rather than from this one. A modified tracked file is the
            # signal worth blocking on: it is work someone may believe is in the release.
            _, dirty, _ = self._capture([
                "git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"])
            if dirty:
                count = len(dirty.splitlines())
                findings.append(PreflightFinding(
                    "working-tree", "fail",
                    f"{repository} has {count} uncommitted change(s)",
                    f"commit or stash them in {root}",
                ))
            code, ahead, _ = self._capture(
                ["git", "-C", str(root), "rev-list", "--count", "@{upstream}..HEAD"])
            if code == 0 and ahead.isdigit() and int(ahead) > 0:
                findings.append(PreflightFinding(
                    "working-tree", "fail",
                    f"{repository} has {ahead} unpushed commit(s) on develop",
                    f"git -C {root} push",
                ))
        return findings

    def check_git_identity(self) -> list[PreflightFinding]:
        if (
            self.environment.get("CEDAR_RELEASE_GIT_NAME")
            and self.environment.get("CEDAR_RELEASE_GIT_EMAIL")
        ):
            return []
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            _, name, _ = self._capture(["git", "-C", str(root), "config", "user.name"])
            _, email, _ = self._capture(["git", "-C", str(root), "config", "user.email"])
            if not name or not email:
                findings.append(PreflightFinding(
                    "git-identity", "fail",
                    f"{repository} has no Git author name and email for release commits",
                    "configure git user.name/user.email, or CEDAR_RELEASE_GIT_NAME and "
                    "CEDAR_RELEASE_GIT_EMAIL",
                ))
        return findings

    def check_nexus_authorization(self) -> list[PreflightFinding]:
        username = self.environment.get("BMIR_NEXUS_USERNAME")
        password = self.environment.get("BMIR_NEXUS_PASSWORD")
        if not username or not password:
            return [PreflightFinding(
                "nexus", "fail",
                "BMIR_NEXUS_USERNAME and BMIR_NEXUS_PASSWORD are not both set, and Nexus "
                "reads fall back to anonymous, so nothing else reveals this until the "
                "first upload",
                "add both to the bmir-nexus-releases server in ~/.m2/settings.xml or export them",
            )]
        findings = []
        authenticated = self._reachable(NEXUS_AUTHENTICATED_ENDPOINT)
        writable = self._reachable(NEXUS_WRITABLE_ENDPOINT)
        repository = self._reachable(NEXUS_REPOSITORY_PROBE)
        if authenticated is not None and not authenticated.startswith("HTTP 5"):
            return [PreflightFinding(
                "nexus", "fail",
                f"BMIR_NEXUS_USERNAME does not authenticate against Nexus: {authenticated}",
                "check the credentials against the bmir-nexus-releases server entry",
            )]
        # A registry over its request budget serves its status endpoints and fails every
        # repository path, which reads as an outage until someone finds the usage page. It
        # is the one failure that gets worse the harder a release tries, so it is named.
        if repository is not None and writable is None:
            return [PreflightFinding(
                "nexus", "fail",
                f"Nexus serves its status endpoints but not its repositories ({repository}), "
                "which is what an instance over its daily request budget looks like",
                "check the Usage Center for requests per day, and let the 24-hour window "
                "roll off before releasing",
            )]
        if writable is not None and repository is not None:
            return [PreflightFinding(
                "nexus", "fail",
                f"Nexus is not writable ({writable}) and cannot serve a repository read "
                f"({repository})",
                "restore Nexus repository and write availability before releasing",
            )]
        if writable is not None:
            findings.append(PreflightFinding(
                "nexus", "fail",
                f"Nexus is not writable: {writable}",
                "restore Nexus write availability before releasing",
            ))
        if repository is not None:
            findings.append(PreflightFinding(
                "nexus", "fail",
                f"Nexus cannot serve a repository read: {repository}",
                "wait for Nexus to recover before releasing",
            ))
        elif authenticated is not None:
            findings.append(PreflightFinding(
                "nexus", "fail", f"Nexus is not healthy: {authenticated}",
                "wait for Nexus to recover before releasing",
            ))
        return findings

    def _reachable(self, url: str) -> str | None:
        """Return None when the URL reads cleanly, or a short description of the failure."""
        try:
            self.http.read(url)
        except ReleaseError as error:
            text = str(error)
            code = re.search(r"HTTP (\d{3})", text)
            return f"HTTP {code.group(1)}" if code else text
        return None

    def check_npm_authorization(self) -> list[PreflightFinding]:
        code, _, stderr = self._capture(
            ["npm", "whoami", "--registry", NEXUS_NPM_REGISTRY])
        if code == 0:
            return []
        return [PreflightFinding(
            "npm", "fail",
            f"npm is not authenticated against {NEXUS_NPM_REGISTRY}: {stderr.splitlines()[-1] if stderr else 'no identity'}",
            f"npm login --registry {NEXUS_NPM_REGISTRY}",
        )]

    def check_npm_configuration(self) -> list[PreflightFinding]:
        configured = self.environment.get("NPM_CONFIG_USERCONFIG") \
            or self.environment.get("npm_config_userconfig")
        if configured:
            path = Path(configured).expanduser()
        else:
            code, output, stderr = self._capture(["npm", "config", "get", "userconfig"])
            if code != 0 or not output:
                return [PreflightFinding(
                    "npm-config", "fail",
                    "npm user configuration path is unreadable: "
                    + (stderr.splitlines()[-1] if stderr else "npm returned no path"),
                    "repair npm configuration before releasing",
                )]
            path = Path(output).expanduser()
        try:
            findings = npm_user_config_findings(path)
        except ValueError as error:
            return [PreflightFinding("npm-config", "fail", str(error))]
        return [
            PreflightFinding(
                "npm-config", finding.severity,
                f"{finding.message} in {path}", finding.remedy,
            )
            for finding in findings
        ]

    def check_push_permission(self) -> list[PreflightFinding]:
        """Ask each remote whether the release's own writes would be accepted.

        A release writes main and a tag in every repository. A branch protection rule or a
        lapsed token refuses those at remote integration, after the build phase has already
        run, so the question is asked here with a push that transmits nothing.
        """
        findings = []
        version = self.manifest.get("releaseVersion")
        next_version = self.manifest.get("nextDevelopmentVersion")
        tag = f"release-{version}"
        completed = self.manifest.get("remoteIntegration", {}).get("completedTasks", {})
        for repository in self.repositories:
            if isinstance(completed, dict) and repository in completed:
                continue
            root = self._root(repository)
            if not root.is_dir():
                continue
            source = self.manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                continue
            targets = [
                f"{source}:refs/heads/main",
                f"{source}:refs/heads/develop",
                f"{source}:refs/heads/release/pre-{version}",
                f"{source}:refs/tags/{tag}",
            ]
            if repository in self.manifest.get("releaseRepositories", []):
                targets.append(f"{source}:refs/heads/release/post-{next_version}")
            code, _, stderr = self._capture([
                "git", "-C", str(root), "push", "--dry-run", "--force", "origin",
                *targets,
            ])
            if code != 0:
                detail = stderr.splitlines()[-1] if stderr else "push refused"
                findings.append(PreflightFinding(
                    "push", "fail",
                    f"{repository} refuses one or more release ref writes: {detail}",
                    "grant push access or adjust branch protection for main, develop, release/*, "
                    "and tags",
                ))
        return findings

    def check_target_version_unused(self) -> list[PreflightFinding]:
        version = self.manifest.get("releaseVersion")
        next_version = self.manifest.get("nextDevelopmentVersion")
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            references = [
                f"refs/tags/release-{version}",
                f"refs/heads/release/pre-{version}",
            ]
            if repository in self.manifest.get("releaseRepositories", []):
                references.append(f"refs/heads/release/post-{next_version}")
            code, output, _ = self._capture([
                "git", "-C", str(root), "ls-remote", "--refs", "origin", *references])
            if code == 0 and output:
                findings.append(PreflightFinding(
                    "version", "fail",
                    f"{repository} already carries release-{version} target ref(s): "
                    + ", ".join(line.split("\t", 1)[-1] for line in output.splitlines()),
                    "choose unused release/next versions, or remove the stale refs deliberately",
                ))
        return findings

    def _source_paths(self, repository: str) -> list[str]:
        if repository in self._source_path_cache:
            return self._source_path_cache[repository]
        source = self.manifest.get("sourceRepositories", {}).get(repository)
        if not source:
            self._source_path_cache[repository] = []
            return []
        root = self._root(repository)
        code, output, _ = self._capture([
            "git", "-C", str(root), "ls-tree", "-r", "--name-only", source])
        paths = output.splitlines() if code == 0 else []
        self._source_path_cache[repository] = paths
        return paths

    def _source_content(self, repository: str, relative: str) -> str | None:
        source = self.manifest.get("sourceRepositories", {}).get(repository)
        if not source:
            return None
        code, output, _ = self._capture([
            "git", "-C", str(self._root(repository)), "show", f"{source}:{relative}"])
        return output if code == 0 else None

    def _source_mode(self, repository: str, relative: str) -> str | None:
        source = self.manifest.get("sourceRepositories", {}).get(repository)
        if not source:
            return None
        code, output, _ = self._capture([
            "git", "-C", str(self._root(repository)), "ls-tree", source, "--", relative])
        return output.split()[0] if code == 0 and output.split() else None

    def _source_json(self, repository: str, relative: str) -> dict | None:
        content = self._source_content(repository, relative)
        if content is None:
            return None
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def check_target_artifacts_unused(self) -> list[PreflightFinding]:
        version = self.manifest.get("releaseVersion")
        findings = []
        query = urllib.parse.urlencode({"repository": "releases", "version": version})
        try:
            result = self.http.read_json(f"{NEXUS_HOST}/service/rest/v1/search?{query}")
        except ReleaseError as error:
            findings.append(PreflightFinding("version", "fail", str(error)))
            result = None
        if result is not None:
            payload, _ = result
            items = payload.get("items", [])
            if isinstance(items, list) and items:
                findings.append(PreflightFinding(
                    "version", "fail",
                    f"Maven releases already contains {len(items)} artifact record(s) for {version}",
                    "choose an unused release version",
                ))
        registry = self.manifest.get("publicationPlan", {}).get("npm", {}).get(
            "registry", NEXUS_NPM_REGISTRY)
        for surface in self.manifest.get("publicationPlan", {}).get("npm", {}).get("surfaces", []):
            repository = surface.get("repository")
            directory = surface.get("directory", ".")
            relative = "package.json" if directory == "." else f"{directory}/package.json"
            package = self._source_json(repository, relative)
            name = package.get("name") if isinstance(package, dict) else None
            if not isinstance(name, str) or not name:
                findings.append(PreflightFinding(
                    "source", "fail", f"cannot determine npm identity from {repository}:{relative}"))
                continue
            url = registry.rstrip("/") + "/" + urllib.parse.quote(name, safe="")
            try:
                record = self.http.read_json(url, missing_ok=True)
            except ReleaseError as error:
                findings.append(PreflightFinding("version", "fail", str(error)))
                continue
            if record is not None and isinstance(record[0].get("versions", {}).get(version), dict):
                findings.append(PreflightFinding(
                    "version", "fail", f"npm registry already contains {name}@{version}",
                    "choose an unused release version",
                ))
        return findings

    def check_develop_is_green(self) -> list[PreflightFinding]:
        """Refuse to release a source commit that its own CI reports broken.

        The question is asked of the exact commit the train was built from, not of whatever
        develop points at now. That is both the more precise question and the stable one: a
        release advances develop to the next snapshot in every repository at once, and the
        CI those pushes trigger can race the parent snapshot they depend on, leaving a tail
        of red runs that say nothing about the source being released.
        """
        if shutil.which("gh", path=self.environment.get("PATH")) is None:
            return [PreflightFinding(
                "ci", "fail", "gh is not on PATH, so the source commit's CI state cannot be read",
                "install the GitHub CLI and authenticate it with gh auth login",
            )]
        findings = []
        for repository in self.repositories:
            source = self.manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                continue
            has_workflow = any(
                path.startswith(".github/workflows/")
                for path in self._source_paths(repository)
            )
            if not has_workflow:
                findings.append(PreflightFinding(
                    "ci", "warn",
                    f"{repository} has no CI workflow contract; release validation builds it",
                ))
                continue
            try:
                probe = probe_exact_commit(
                    repository,
                    source,
                    runner=self.command_runner,
                    sleeper=self.ci_sleeper,
                    delays=self.ci_delays,
                    reporter=lambda message: console.print(f"  [yellow]ci: {message}[/yellow]"),
                )
            except GithubCIProbeError as error:
                findings.append(PreflightFinding(
                    "ci", "fail", str(error),
                ))
                continue
            runs = list(probe.runs)
            if repository == "cedar-development":
                runs = [
                    record for record in runs
                    if record.get("path") != ".github/workflows/build-train.yml"
                ]
            if not runs:
                findings.append(PreflightFinding(
                    "ci", "fail",
                    f"{repository} has no CI run for the train source {source[:8]} "
                    "after bounded indexing grace",
                ))
                continue
            for name, record in latest_runs_by_name(runs).items():
                conclusion = record.get("conclusion")
                status = record.get("status")
                run_id = str(record.get("id") or "")
                url = run_url(record)
                where = f" ({url})" if url else ""
                if status != "completed":
                    findings.append(PreflightFinding(
                        "ci", "fail",
                        f"{repository} {name} is still {status or 'pending'} for "
                        f"{source[:8]}{where}",
                        f"watch the run before retrying: {url}" if url else "wait for CI to settle",
                    ))
                    continue
                if conclusion in GREEN_CONCLUSIONS:
                    continue
                if conclusion == "cancelled":
                    # Somebody stopped this run. That is an action taken about the workflow,
                    # never a result about the code, so it is reported and not blocked on.
                    findings.append(PreflightFinding(
                        "ci", "warn",
                        f"{repository} {name} was cancelled for the train source "
                        f"{source[:8]} in run {run_id}{where}",
                    ))
                    continue
                if self.accepted_red_develop.get(repository) == run_id:
                    findings.append(PreflightFinding(
                        "ci", "warn",
                        f"{repository} develop is {conclusion} in run {run_id}, "
                        f"accepted explicitly{where}",
                    ))
                    continue
                findings.append(PreflightFinding(
                    "ci", "fail",
                    f"{repository} {name} is {conclusion} for the train source "
                    f"{source[:8]} in run {run_id}{where}",
                    f"fix develop and build a new train, or accept this run with "
                    f"--accept-red-develop {repository}={run_id}",
                ))
        return findings

    def check_smoke_gate(self) -> list[PreflightFinding]:
        """Refuse to release a source that no passing whole-stack smoke run covers.

        The question is asked of the train's source commits, for the reason the CI check gives:
        develop moves on between a train and its release, and the run that answers for what is
        being released is the one made against exactly that. `cedarcli test e2e` records each run
        under the heads it tested, so a later rerun against newer heads does not disturb the answer
        here, and no flag skips the check: a flaky run is rerun, not accepted.
        """
        expected = self.manifest.get("sourceRepositories") or {}
        if not expected:
            return [PreflightFinding(
                "smoke", "fail",
                "the manifest records no source repositories to match a smoke run against",
            )]
        return [
            PreflightFinding("smoke", "fail", message, smoke_gate.REMEDY)
            for message in smoke_gate.findings_for(self.environment.get("CEDAR_HOME"), expected)
        ]

    def check_source_contract(self) -> list[PreflightFinding]:
        """Validate build and publication topology in the exact immutable train commits."""
        findings = []
        source_version = self.manifest.get("sourceVersion")
        required: dict[str, set[str]] = {}
        for phase in self.manifest.get("mavenPhases", []):
            required.setdefault(phase.get("repository"), set()).add("mvnw")
        for surface in FRONTEND_BUILD_SURFACES:
            repository = surface["repository"]
            if repository not in self.repositories:
                continue
            prefix = "" if surface["directory"] == "." else f"{surface['directory']}/"
            required.setdefault(repository, set()).update({
                f"{prefix}package.json", f"{prefix}package-lock.json",
            })
        registry = self.manifest.get("publicationPlan", {}).get("npm", {}).get("registry")
        for surface in self.manifest.get("publicationPlan", {}).get("npm", {}).get("surfaces", []):
            repository = surface.get("repository")
            directory = surface.get("directory", ".")
            prefix = "" if directory == "." else f"{directory}/"
            paths = required.setdefault(repository, set())
            paths.update({f"{prefix}package.json", f"{prefix}package-lock.json"})
            for relative in surface.get("preserveFiles", []):
                target = f"{prefix}{relative}"
                paths.add(target)
        for consumer in self.manifest.get("cee", {}).get("consumers", []):
            required.setdefault(consumer.get("repository"), set()).update({
                consumer.get("manifest"), consumer.get("lock"),
            })

        for repository, paths in sorted(required.items()):
            if not isinstance(repository, str):
                continue
            inventory = set(self._source_paths(repository))
            for relative in sorted(item for item in paths if isinstance(item, str)):
                if relative in inventory:
                    continue
                if relative.endswith("/license.txt") and "license.txt" in inventory:
                    continue
                findings.append(PreflightFinding(
                    "source", "fail",
                    f"train source {repository} is missing required release input {relative}",
                ))
        for surface in FRONTEND_BUILD_SURFACES:
            repository = surface["repository"]
            if repository not in self.repositories:
                continue
            prefix = "" if surface["directory"] == "." else f"{surface['directory']}/"
            package = self._source_json(repository, f"{prefix}package.json")
            lock = self._source_json(repository, f"{prefix}package-lock.json")
            identity = f"{repository}:{surface['directory']}"
            try:
                pending = unreviewed_install_scripts(package, lock, identity)
            except ValueError as error:
                findings.append(PreflightFinding("npm-scripts", "fail", str(error)))
                continue
            if pending:
                findings.append(PreflightFinding(
                    "npm-scripts", "fail",
                    f"{identity} has unreviewed npm install scripts: " + ", ".join(pending),
                    "record an exact true/false allowScripts decision in the captured package.json",
                ))
        for phase in self.manifest.get("mavenPhases", []):
            repository = phase.get("repository")
            mode = self._source_mode(repository, "mvnw")
            if mode is not None and mode != "100755":
                findings.append(PreflightFinding(
                    "source", "fail",
                    f"train source {repository}:mvnw is not executable (Git mode {mode})",
                ))

        for repository, surfaces in NPM_VERSION_SURFACES.items():
            if repository not in self.manifest.get("releaseRepositories", []):
                continue
            for directory in surfaces:
                prefix = "" if directory == "." else f"{directory}/"
                package = self._source_json(repository, f"{prefix}package.json")
                lock = self._source_json(repository, f"{prefix}package-lock.json")
                root = lock.get("packages", {}).get("") if isinstance(lock, dict) else None
                if (
                    not isinstance(package, dict) or package.get("version") != source_version
                    or not isinstance(lock, dict) or lock.get("version") != source_version
                    or not isinstance(root, dict) or root.get("version") != source_version
                ):
                    findings.append(PreflightFinding(
                        "source", "fail",
                        f"{repository}:{directory} does not carry train source version {source_version} "
                        "in package.json and package-lock.json",
                    ))
        for repository in self.manifest.get("mavenRepositories", []):
            source = self.manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                continue
            code, _, _ = self._capture([
                "git", "-C", str(self._root(repository)), "grep", "-q", "--fixed-strings",
                source_version, source, "--", "*pom.xml",
            ])
            if code != 0:
                findings.append(PreflightFinding(
                    "source", "fail",
                    f"train source {repository} has no Maven version {source_version} to stamp",
                ))
        special_markers = {
            "cedar-development": (
                "bin/util/set-env-generic.sh",
                [f"export CEDAR_VERSION={source_version}"],
            ),
            "cedar-docker-build": (
                "bin/cedar-images-base.sh",
                [
                    f"export IMAGE_VERSION={source_version}",
                    f"export CEDAR_MAVEN_VERSION={source_version}",
                    f"export CEDAR_APPLICATION_VERSION={source_version}",
                ],
            ),
        }
        for repository, (relative, markers) in special_markers.items():
            if repository not in self.manifest.get("releaseRepositories", []):
                continue
            content = self._source_content(repository, relative)
            for marker in markers:
                if content is None or marker not in content:
                    findings.append(PreflightFinding(
                        "source", "fail",
                        f"train source {repository}:{relative} does not contain {marker!r}",
                    ))
            if repository == "cedar-docker-build":
                defaults = (self.manifest.get("dockerFrontendDefaults") or {}).get(
                    "nextDevelopment", {})
                for variable in sorted(defaults):
                    declared = content is not None and re.search(
                        rf"^export {re.escape(variable)}=", content, re.MULTILINE)
                    if not declared:
                        findings.append(PreflightFinding(
                            "source", "fail",
                            f"train source {repository}:{relative} does not declare {variable}, "
                            "which the train's Docker inputs name",
                            "declare the variable in the images base script, or drop it from "
                            "frontend-train.json, and build a new train",
                        ))
        if "cedar-docker-deploy" in self.manifest.get("releaseRepositories", []):
            matches = 0
            for relative in self._source_paths("cedar-docker-deploy"):
                if not relative.endswith(".env"):
                    continue
                content = self._source_content("cedar-docker-deploy", relative)
                matches += int(content is not None and f"CEDAR_DOCKER_VERSION={source_version}" in content)
            if not matches:
                findings.append(PreflightFinding(
                    "source", "fail",
                    "train source cedar-docker-deploy has no deployment version "
                    f"{source_version} to stamp",
                ))
        for surface in self.manifest.get("publicationPlan", {}).get("npm", {}).get("surfaces", []):
            repository = surface.get("repository")
            directory = surface.get("directory", ".")
            relative = "package.json" if directory == "." else f"{directory}/package.json"
            package = self._source_json(repository, relative)
            configured = package.get("publishConfig", {}).get("registry") \
                if isinstance(package, dict) else None
            if registry and configured != registry:
                findings.append(PreflightFinding(
                    "source", "fail",
                    f"{repository}:{relative} publishes to {configured!r}, expected {registry}",
                ))
        return findings

    def check_generated_version_files(self) -> list[PreflightFinding]:
        """Find version-bearing generated files the stamping table does not declare.

        An undeclared file is regenerated during the build with the release version inside,
        which the prepared-file guard then reports as drift. Declaring it is the fix, and
        finding it here costs a directory walk rather than a build.
        """
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            declared = set(MAVEN_GENERATED_VERSION_FILES.get(repository, {}))
            source = self.manifest.get("sourceRepositories", {}).get(repository)
            candidates = (
                self._source_paths(repository) if source else [
                    path.relative_to(root).as_posix()
                    for glob in GENERATED_VERSION_FILE_GLOBS for path in root.glob(glob)
                ]
            )
            for relative in sorted(set(candidates)):
                if not any(fnmatch.fnmatch(relative, glob) for glob in GENERATED_VERSION_FILE_GLOBS):
                    continue
                if relative in declared:
                    continue
                findings.append(PreflightFinding(
                    "generated-files", "fail",
                    f"{repository} regenerates {relative}, which carries the version and "
                    "is not declared",
                    f"add {relative} to MAVEN_GENERATED_VERSION_FILES[{repository!r}]",
                ))
        return findings

    def check_license_files(self) -> list[PreflightFinding]:
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            source = self.manifest.get("sourceRepositories", {}).get(repository)
            content = self._source_content(repository, LICENSE_FILE_NAME) if source else None
            path = root / LICENSE_FILE_NAME
            if content is None and not source and path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace")
            if content is None:
                findings.append(PreflightFinding(
                    "license", "warn",
                    f"{repository} has no {LICENSE_FILE_NAME}, so its copyright year is not stamped",
                ))
                continue
            if not LICENSE_COPYRIGHT_RE.search(content):
                findings.append(PreflightFinding(
                    "license", "fail",
                    f"{repository} has a {LICENSE_FILE_NAME} with no recognisable copyright year",
                    f"restore the 'Copyright (c) YYYY,' line in {path}",
                ))
        return findings

    def check_remote_survey(self) -> list[PreflightFinding]:
        """Refuse to publish over work that exists only on main.

        A file that main carries and the train's develop does not is a commit someone made
        straight to main, or a hotfix nobody back-merged. The release replaces it, and the
        replacement is a push: the work is gone from the branch that carries it, and the
        release that removed it says nothing at the time. This was an advisory once, and a
        hotfix and the unit test guarding it came within one reading of that line of being
        deleted by a release nobody would have thought to question.

        Naming the repository accepts the replacement, for the case where develop deleted the
        file deliberately and main is simply behind.
        """
        try:
            replaced = ReleaseRemoteIntegrator(self.state, environment=self.environment).survey(
                self.manifest)
        except ReleaseError as error:
            return [PreflightFinding("remote", "fail", str(error))]
        findings = []
        for repository, paths in sorted(replaced.items()):
            listing = ", ".join(paths)
            if repository in self.accepted_main_only:
                findings.append(PreflightFinding(
                    "remote", "warn",
                    f"{repository} carries {len(paths)} file(s) on main alone, replaced with "
                    f"this release by explicit acceptance: {listing}",
                ))
                continue
            findings.append(PreflightFinding(
                "remote", "fail",
                f"{repository} carries {len(paths)} file(s) on main alone, which the release "
                f"replaces: {listing}",
                f"port main's commits to develop and build a new train, or accept the "
                f"replacement with --accept-main-only {repository}",
            ))
        return findings

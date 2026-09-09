"""CEDAR release policy."""
from __future__ import annotations
import re
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)


PUBLIC_NPM_REGISTRY = "https://registry.npmjs.org/"


DEV_CEE_NAME = "@org.metadatacenter/cedar-embeddable-editor"


PUBLIC_CEE_NAME = "cedar-embeddable-editor"


STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


NEXT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+-SNAPSHOT$")


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


DEV_MODEL_SPEC_RE = re.compile(
    rb"npm:@org\.metadatacenter/cedar-model-typescript-library@"
    rb"[0-9]+\.[0-9]+\.[0-9]+-dev\.[0-9A-Za-z.-]+"
)


LOAD_TRACE_RE = re.compile(
    rb"20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}"
    rb"(?: [0-9a-f]{7,40})?"
)


REQUIRED_CEE_FILES = {
    "bundle-manifest.json",
    "cedar-embeddable-editor.d.ts",
    "cedar-embeddable-editor.js",
    "package.json",
}


INDEPENDENT_RELEASE_REPOSITORIES = {
    "cedar-embeddable-editor",
    "cedar-model-typescript-library",
}


REQUIRED_NODE_VERSION = "v24.19.0"


NPM_VERSION_SURFACES = {
    "cedar-template-editor": ["."],
    "cedar-workspace": ["."],
    "cedar-template-designer": ["."],
    "cedar-model-typescript-library-demo": ["."],
    "cedar-openview": ["cedar-openview-src", "cedar-openview-dist"],
    "cedar-content-distribution": ["."],
    "cedar-monitoring": ["cedar-monitoring-src", "cedar-monitoring-dist"],
    "cedar-bridging": ["cedar-bridging-src", "cedar-bridging-dist"],
    "cedar-component-demo": [
        "cedar-cee-demo-angular-src",
        "cedar-cee-demo-angular-dist",
        "cedar-cee-demo-ember-src",
        "cedar-cee-demo-react",
    ],
}


LICENSE_FILE_NAME = "license.txt"


LICENSE_COPYRIGHT_RE = re.compile(r"^Copyright \(c\) (\d{4}),", re.MULTILINE)


MAVEN_GENERATED_VERSION_FILES = {
    "cedar-artifact-server": {
        "cedar-artifact-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-artifact-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-bridge-server": {
        "cedar-bridge-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-bridge-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-group-server": {
        "cedar-group-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-group-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-impex-server": {
        "cedar-impex-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-impex-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-messaging-server": {
        "cedar-messaging-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-messaging-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-monitor-server": {
        "cedar-monitor-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-monitor-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-openview-server": {
        "cedar-openview-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-openview-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-repo-server": {
        "cedar-repo-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-repo-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-submission-server": {
        "cedar-submission-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-submission-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-user-server": {
        "cedar-user-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-user-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-worker-server": {
        "cedar-worker-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-worker-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-resource-server": {
        "cedar-resource-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-resource-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-terminology-server": {
        "cedar-terminology-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-terminology-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-valuerecommender-server": {
        "cedar-valuerecommender-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-valuerecommender-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
}


FRONTEND_BUILD_SURFACES = [
    {"id": "template-editor", "repository": "cedar-template-editor", "directory": ".",
     "install": [], "build": []},
    {"id": "workspace", "repository": "cedar-workspace", "directory": ".",
     "install": [], "build": []},
    {"id": "template-designer", "repository": "cedar-template-designer", "directory": ".",
     "install": [], "build": []},
    {"id": "model-typescript-library-demo",
     "repository": "cedar-model-typescript-library-demo", "directory": ".",
     "install": [], "build": ["npm", "run", "build"],
     "buildOutput": "dist"},
    {"id": "openview", "repository": "cedar-openview", "directory": "cedar-openview-src",
     "install": [], "build": ["npm", "run", "build"],
     "buildOutput": "cedar-openview-src/dist/cedar-openview"},
    {"id": "bridging", "repository": "cedar-bridging", "directory": "cedar-bridging-src",
     "install": [], "build": ["npm", "run", "build"],
     "buildOutput": "cedar-bridging-src/dist/cedar-bridging"},
    {"id": "monitoring", "repository": "cedar-monitoring", "directory": "cedar-monitoring-src",
     "install": ["--legacy-peer-deps"], "build": ["npm", "run", "build"],
     "buildOutput": "cedar-monitoring-src/dist/cedar-monitoring"},
    {"id": "content", "repository": "cedar-content-distribution", "directory": ".",
     "install": [], "build": []},
    {"id": "cee-demo-angular", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-angular-src", "install": [],
     "build": ["npm", "run", "build"],
     "buildOutput": "cedar-cee-demo-angular-src/dist/cedar-cee-demo-angular-src/browser"},
    {"id": "cee-demo-ember", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-ember-src", "install": [],
     "build": ["npm", "run", "build"]},
    {"id": "cee-demo-react", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-react", "install": [],
     "build": ["npm", "run", "build"]},
]


MAVEN_RELEASE_REPOSITORY = "https://nexus.bmir.stanford.edu/repository/releases/"


MAVEN_SNAPSHOT_REPOSITORY = "https://nexus.bmir.stanford.edu/repository/snapshots/"


NPM_RELEASE_SURFACES = [
    {"id": "template-editor", "repository": "cedar-template-editor", "directory": "."},
    {"id": "workspace", "repository": "cedar-workspace", "directory": "."},
    {"id": "template-designer", "repository": "cedar-template-designer", "directory": "."},
    {"id": "model-typescript-library-demo",
     "repository": "cedar-model-typescript-library-demo", "directory": ".",
     "generatedBuildOutput": "dist"},
    {
        "id": "openview", "repository": "cedar-openview", "directory": "cedar-openview-dist",
        "buildOutput": "cedar-openview-src/dist/cedar-openview",
        "preserveFiles": ["README.md", "license.txt", "package-lock.json", "package.json"],
        "packedRuntimeDirectories": ["node_modules"],
        "ceeRuntime": {
            "source": (
                "cedar-openview-src/node_modules/cedar-embeddable-editor/"
                "cedar-embeddable-editor.js"
            ),
            "distribution": (
                "node_modules/cedar-embeddable-editor/cedar-embeddable-editor.js"
            ),
            "replacements": [
                [
                    "https://terminology.metadatacenter.orgx/",
                    "https://terminology.metadatacenter.org/",
                ],
                [
                    "https://bridge.metadatacenter.orgx/",
                    "https://bridge.metadatacenter.org/",
                ],
            ],
        },
    },
    {"id": "content", "repository": "cedar-content-distribution", "directory": "."},
    {
        "id": "monitoring", "repository": "cedar-monitoring",
        "directory": "cedar-monitoring-dist",
        "buildOutput": "cedar-monitoring-src/dist/cedar-monitoring",
        "preserveFiles": ["README.md", "license.txt", "package-lock.json", "package.json"],
    },
    {
        "id": "bridging", "repository": "cedar-bridging", "directory": "cedar-bridging-dist",
        "buildOutput": "cedar-bridging-src/dist/cedar-bridging",
        "preserveFiles": ["README.md", "license.txt", "package-lock.json", "package.json"],
    },
    {
        "id": "cee-demo-angular", "repository": "cedar-component-demo",
        "directory": "cedar-cee-demo-angular-dist",
        "buildOutput": "cedar-cee-demo-angular-src/dist/cedar-cee-demo-angular-src/browser",
        "preserveFiles": ["README.md", "license.txt", "package-lock.json", "package.json"],
    },
]


def _integration_repositories(manifest: dict) -> list[str]:
    """Repositories whose remote refs the release actually changes."""
    repositories = list(manifest.get("releaseRepositories", []))
    for consumer in manifest.get("cee", {}).get("consumers", []):
        repository = consumer.get("repository") if isinstance(consumer, dict) else None
        if isinstance(repository, str) and repository not in repositories:
            repositories.append(repository)
    return repositories


RETRYABLE_TRANSPORT_TEXT = (
    "connection reset",
    "connection refused",
    "connection timed out",
    "operation timed out",
    "temporary failure in name resolution",
    "could not resolve host",
    "remote end hung up unexpectedly",
    "unexpected disconnect",
    "tls connection was non-properly terminated",
    "ssl_error_syscall",
    "stream error in the http/2 framing layer",
    "rpc failed; curl 92",
)


def _validate_stable_version(value: str, label: str) -> str:
    if not STABLE_VERSION_RE.fullmatch(value or ""):
        raise ReleaseError(f"invalid {label} {value!r}; expected MAJOR.MINOR.PATCH")
    return value


def _stable_version_key(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))


MINIFIED_TOKEN_SPLIT = re.compile(rb"([A-Za-z_$][A-Za-z0-9_$]*)")


MINIFIED_NAME_RE = re.compile(rb"^[A-Za-z_$][A-Za-z0-9_$]{0,2}$")


# Words a minifier never hands to a local, so a difference in one is a code change however short.
RESERVED_SHORT_WORDS = frozenset({
    b"do", b"if", b"in", b"for", b"let", b"new", b"try", b"var", b"of", b"NaN",
})


ABANDONABLE_RELEASE_PHASES = frozenset({
    "started",
    "preparing-frontends", "frontend-preparation-failed", "frontends-prepared",
    "preparing-versions", "version-preparation-failed", "versions-prepared",
    "validating-builds", "build-validation-failed", "builds-validated",
    "creating-local-refs", "local-ref-creation-failed", "local-refs-created",
})


# Variables the CEDAR profile defines and the Maven suites read. A build started without
# them fails deep inside Dropwizard configuration with UndefinedEnvironmentVariableException.
PROFILE_REQUIRED_VARIABLES = (
    "CEDAR_HOME",
    "CEDAR_HOST",
    "CEDAR_DEVELOP_HOME",
    "CEDAR_NET_GATEWAY",
    "CEDAR_FRONTEND_TARGET",
)


PROFILE_COMMAND = ("CEDAR_PROFILE=develop source "
                   "$CEDAR_HOME/cedar-development/bin/templates/cedar-profile-native.sh")


# javac earns its place beside java below: the version probe runs `java -version`, which a
# JRE-only host satisfies while still being unable to compile anything. That is not hypothetical
# - openjdk-*-jre-headless is exactly what apt offers when `java` is not found, so a reprovisioned
# build host lands there by default and the absent compiler surfaces minutes into a release as a
# Maven failure rather than as a missing toolchain.
REQUIRED_TOOLS = ("git", "javac", "mvn", "node", "npm")


REQUIRED_JAVA_MAJOR = 17


# Where Homebrew keeps the release's Node when the shell's default node is another version:
# Apple silicon first, then Intel.
NODE_24_CANDIDATE_DIRECTORIES = (
    "/opt/homebrew/opt/node@24/bin",
    "/usr/local/opt/node@24/bin",
)


LINUX_JVM_ROOT = "/usr/lib/jvm"


# Calibrated allocations for one clean release workspace. The final requirement
# is derived from the manifest's repository/build counts; these are deliberately
# named so observed train footprints can tune the model without restoring a
# single opaque free-space threshold.
CHECKOUT_BYTES_PER_REPOSITORY = 48 * 1024 ** 2


MAVEN_BYTES_PER_REPOSITORY_VARIANT = 192 * 1024 ** 2


FRONTEND_BYTES_PER_SURFACE_VARIANT = 512 * 1024 ** 2


PUBLICATION_CACHE_AND_LOG_BYTES = 2 * 1024 ** 3


MINIMUM_SPACE_HEADROOM_BYTES = 4 * 1024 ** 3


SPACE_HEADROOM_PERCENT = 25


NEXUS_HOST = "https://nexus.bmir.stanford.edu"


NEXUS_NPM_REGISTRY = f"{NEXUS_HOST}/repository/npm-cedar/"


# Anonymous callers receive 403 from this endpoint, so a 200 proves the configured
# credentials authenticate. It does not prove the deploy privilege on a given
# repository, which only a write can establish.
NEXUS_AUTHENTICATED_ENDPOINT = f"{NEXUS_HOST}/service/rest/v1/status/check"


NEXUS_WRITABLE_ENDPOINT = f"{NEXUS_HOST}/service/rest/v1/status/writable"


# The status endpoints answer from the web tier and stay green while every repository
# behind them fails, so the check that decides whether a release can publish reads
# something a release actually reads.
NEXUS_REPOSITORY_PROBE = (
    f"{NEXUS_HOST}/repository/snapshots/org/metadatacenter/cedar-parent/maven-metadata.xml"
)


# Files a Maven build regenerates with the project version inside. Every match must be
# declared in MAVEN_GENERATED_VERSION_FILES, or the prepared-file guard trips mid-build.
GENERATED_VERSION_FILE_GLOBS = (
    "*/src/main/resources/assets/swagger-api/swagger.json",
    "*/src/main/resources/assets/swagger-api/swagger.yaml",
)


ACCEPT_RED_DEVELOP_HELP = (
    "Accept one repository's red develop by naming the exact run, as <repository>=<run-id>"
)


ACCEPT_MAIN_ONLY_HELP = (
    "Accept replacing one repository's main-only files by naming the repository"
)

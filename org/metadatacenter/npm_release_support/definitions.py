"""The four packages CEDAR publishes to public npmjs outside the platform release."""
from __future__ import annotations

from dataclasses import dataclass
import re

PUBLIC_REGISTRY = "https://registry.npmjs.org/"
NEXUS_REGISTRY = "https://nexus.bmir.stanford.edu/repository/npm-cedar/"
NPM_ACCOUNT = "metadatacenter"
GITHUB_OWNER = "metadatacenter"
MODEL_PUBLIC_NAME = "cedar-model-typescript-library"
MODEL_DEV_NAME = "@org.metadatacenter/cedar-model-typescript-library"
STABLE_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
DEV_MODEL_VERSION = re.compile(r"^\d+\.\d+\.\d+-dev\.[0-9A-Za-z.]+$")


@dataclass(frozen=True)
class NpmPackage:
    """What the route needs to know about one package, and nothing a package can derive itself.

    Each repository derives its publish identity from its version: a stable version stages the
    unscoped package for npmjs, a development version the scoped one for Nexus. The route therefore
    sets versions and pins and reads what the repository staged, rather than naming files.
    """

    id: str
    title: str
    repository: str
    public_name: str
    dev_name: str
    stage_directory: str
    installs: tuple[str, ...]
    gate: tuple[tuple[str, ...], ...]
    main_build: tuple[tuple[str, ...], ...]
    development_check: tuple[tuple[str, ...], ...]
    model_manifests: tuple[str, ...] = ()
    changelog: str | None = None
    load_trace: str | None = None
    published_manifest: str | None = None
    framework_demos: bool = False

    @property
    def consumes_model(self) -> bool:
        return bool(self.model_manifests)

    @property
    def tag_prefix(self) -> str:
        return "release-"

    def tag(self, version: str) -> str:
        return f"{self.tag_prefix}{version}"

    @property
    def github(self) -> str:
        return f"{GITHUB_OWNER}/{self.repository}"


_CEE_LOAD_TRACE = (
    "src/app/modules/shared/components/cedar-embeddable-metadata-editor/"
    "cedar-embeddable-metadata-editor.component.ts"
)

PACKAGES: dict[str, NpmPackage] = {
    package.id: package
    for package in (
        NpmPackage(
            id="model",
            title="TypeScript model library",
            repository="cedar-model-typescript-library",
            public_name=MODEL_PUBLIC_NAME,
            dev_name=MODEL_DEV_NAME,
            stage_directory="dist",
            installs=(".",),
            gate=(("npm", "run", "test:ci"),),
            main_build=(("npm", "run", "test:package"),),
            development_check=(("npm", "run", "test:package"),),
            published_manifest="package-dist.json",
        ),
        NpmPackage(
            id="cee",
            title="CEE",
            repository="cedar-embeddable-editor",
            public_name="cedar-embeddable-editor",
            dev_name="@org.metadatacenter/cedar-embeddable-editor",
            stage_directory="dist-npm/cedar-embeddable-editor",
            installs=(".", "harness", "visual"),
            gate=(("npm", "run", "test:ci"),),
            main_build=(("npm", "run", "test:package"),),
            development_check=(("npm", "run", "test:ci"),),
            model_manifests=(".", "visual"),
            changelog="CHANGELOG.md",
            load_trace=_CEE_LOAD_TRACE,
            framework_demos=True,
        ),
        NpmPackage(
            id="ced",
            title="CED",
            repository="cedar-embeddable-designer",
            public_name="cedar-embeddable-designer",
            dev_name="@org.metadatacenter/cedar-embeddable-designer",
            stage_directory="dist-npm/cedar-embeddable-designer",
            installs=(".", "browser"),
            gate=(("npm", "run", "test:ci"),),
            main_build=(("npm", "run", "dist"),),
            development_check=(("npm", "run", "test:ci"),),
            model_manifests=(".",),
            changelog="CHANGELOG.md",
        ),
        NpmPackage(
            id="cetp",
            title="CETP",
            repository="cedar-embeddable-term-picker",
            public_name="cedar-embeddable-term-picker",
            dev_name="@org.metadatacenter/cedar-embeddable-term-picker",
            stage_directory="dist-npm/cedar-embeddable-term-picker",
            installs=(".", "browser"),
            gate=(("npm", "run", "test:ci"),),
            main_build=(("npm", "run", "dist"),),
            development_check=(("npm", "run", "test:ci"),),
            model_manifests=(".",),
            changelog="CHANGELOG.md",
        ),
    )
}

# The model is compiled into the other three, so it is released first. The other three do not
# build against one another, so their relative order is only a convention.
RELEASE_ORDER = ("model", "cee", "ced", "cetp")

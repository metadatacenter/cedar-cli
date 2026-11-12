"""What the operator asked for: packages, versions and notes, settled before anything runs."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from org.metadatacenter.npm_release_support.definitions import (
    DEV_MODEL_VERSION,
    PACKAGES,
    RELEASE_ORDER,
    STABLE_VERSION,
)
from org.metadatacenter.npm_release_support.errors import NpmReleaseError


@dataclass(frozen=True)
class PackageRequest:
    package: str
    version: str
    next_base: str
    model_version: str | None = None
    development_model_version: str | None = None
    notes: str | None = None

    def as_record(self) -> dict:
        return {
            "package": self.package,
            "version": self.version,
            "nextBase": self.next_base,
            "modelVersion": self.model_version,
            "developmentModelVersion": self.development_model_version,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RunRequest:
    packages: tuple[PackageRequest, ...]
    defer_baselines: bool = False
    model_in_run: bool = field(default=False)


def version_key(version: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in version.split("."))


def _stable(value: str, label: str) -> str:
    if not STABLE_VERSION.fullmatch(value or ""):
        raise NpmReleaseError(f"{label} {value!r} is not a MAJOR.MINOR.PATCH version")
    return value


def _split_spec(value: str, option: str) -> tuple[str, str]:
    package, separator, version = (value or "").partition("@")
    package = package.strip()
    if not separator or not package or not version.strip():
        raise NpmReleaseError(f"{option} expects <package>@<version>, not {value!r}")
    if package not in PACKAGES:
        known = ", ".join(RELEASE_ORDER)
        raise NpmReleaseError(f"{option} names unknown package {package!r}; known: {known}")
    return package, version.strip()


def _split_assignment(value: str, option: str) -> tuple[str, str]:
    package, separator, text = (value or "").partition("=")
    package = package.strip()
    if not separator or not package or not text.strip():
        raise NpmReleaseError(f"{option} expects <package>=<value>, not {value!r}")
    if package not in PACKAGES:
        known = ", ".join(RELEASE_ORDER)
        raise NpmReleaseError(f"{option} names unknown package {package!r}; known: {known}")
    return package, text.strip()


_SUBSECTION = re.compile(r"### [A-Z][A-Za-z ]*")


def read_notes(path: str, package: str) -> str:
    """Read the bullets an entry carries, refusing anything that would break the changelog's shape.

    The changelog entry is cut out again by the platform release's promotion proof, from its heading
    to the next `## [`, so notes holding an entry heading of their own would end that cut early.
    Keep a Changelog's `### Added`-style subsection headings cannot, so notes may group their
    bullets under them; every other heading is refused.
    """
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as error:
        raise NpmReleaseError(f"cannot read the {package} notes at {path}: {error}") from error
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if not lines:
        raise NpmReleaseError(f"the {package} notes at {path} are empty")
    for line in lines:
        if line.lstrip().startswith("#") and not _SUBSECTION.fullmatch(line):
            raise NpmReleaseError(
                f"the {package} notes at {path} contain a heading ({line!r}); "
                "notes hold the entry's bullets, under '### ' subsections at most")
    if not any(line.startswith("- ") for line in lines):
        raise NpmReleaseError(f"the {package} notes at {path} contain no '- ' bullet")
    return "\n".join(lines) + "\n"


def parse_run_request(
    specs: list[str],
    nexts: list[str] | None,
    model_version: str | None,
    development_model_version: str | None,
    notes: list[str] | None,
    defer_baselines: bool = False,
) -> RunRequest:
    """Turn the command line into an ordered, fully explicit request, or refuse it.

    Every version is chosen by the operator. Nothing is inferred from another package, and the
    newest public model is never substituted for one the operator did not name.
    """
    if not specs:
        raise NpmReleaseError("name at least one package to release, as <package>@<version>")
    versions: dict[str, str] = {}
    for spec in specs:
        package, version = _split_spec(spec, "package")
        if package in versions:
            raise NpmReleaseError(f"{package} is named twice")
        versions[package] = _stable(version, f"{package} version")
    next_bases: dict[str, str] = {}
    for value in nexts or []:
        package, version = _split_spec(value, "--next")
        if package not in versions:
            raise NpmReleaseError(f"--next names {package}, which is not being released")
        if package in next_bases:
            raise NpmReleaseError(f"--next names {package} twice")
        next_bases[package] = _stable(version, f"{package} next development base")
    note_files: dict[str, str] = {}
    for value in notes or []:
        package, path = _split_assignment(value, "--notes")
        if package not in versions:
            raise NpmReleaseError(f"--notes names {package}, which is not being released")
        note_files[package] = read_notes(path, package)

    model_in_run = "model" in versions
    consumers = [package for package in versions if PACKAGES[package].consumes_model]
    if model_in_run and model_version and model_version != versions["model"]:
        raise NpmReleaseError(
            f"--model-version {model_version} contradicts the model release {versions['model']}")
    pinned_model = versions["model"] if model_in_run else model_version
    if consumers and not pinned_model:
        raise NpmReleaseError(
            f"{', '.join(consumers)} build against the model library; give --model-version, "
            "the public model version to pin, or release the model in the same run")
    if pinned_model:
        _stable(pinned_model, "model version")
    if development_model_version:
        if not consumers:
            raise NpmReleaseError("--dev-model-version applies only to CEE, CED and CETP")
        if not DEV_MODEL_VERSION.fullmatch(development_model_version):
            raise NpmReleaseError(
                f"--dev-model-version {development_model_version!r} is not a model development version")

    requests = []
    for package in RELEASE_ORDER:
        if package not in versions:
            continue
        version = versions[package]
        if package not in next_bases:
            raise NpmReleaseError(
                f"give --next {package}@<version>, the base of {package}'s next development version")
        if version_key(next_bases[package]) <= version_key(version):
            raise NpmReleaseError(
                f"{package}'s next development base {next_bases[package]} must exceed {version}")
        definition = PACKAGES[package]
        if definition.changelog and package not in note_files:
            raise NpmReleaseError(
                f"give --notes {package}=<file>, the bullets of its {definition.changelog} entry")
        requests.append(PackageRequest(
            package=package,
            version=version,
            next_base=next_bases[package],
            model_version=pinned_model if definition.consumes_model else None,
            development_model_version=development_model_version if definition.consumes_model else None,
            notes=note_files.get(package),
        ))
    return RunRequest(tuple(requests), defer_baselines=defer_baselines, model_in_run=model_in_run)

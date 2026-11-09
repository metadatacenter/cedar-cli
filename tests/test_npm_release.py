"""The npm release route, against real Git remotes and a stand-in npm registry and GitHub."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

import pytest

from org.metadatacenter.github_ci import GithubCIProbe
from org.metadatacenter.npm_package import inspect_tarball
from org.metadatacenter.npm_release_support import route as route_module
from org.metadatacenter.npm_release_support.definitions import PACKAGES, PUBLIC_REGISTRY
from org.metadatacenter.npm_release_support.edits import (
    changelog_entry,
    insert_changelog_entry,
    published_manifest_name,
    stamp_load_trace,
)
from org.metadatacenter.npm_release_support.errors import NpmReleaseError, NpmReleaseWait
from org.metadatacenter.npm_release_support.preflight import NpmReleasePreflight
from org.metadatacenter.npm_release_support.requests import parse_run_request
from org.metadatacenter.npm_release_support.route import NpmReleaseRoute
from org.metadatacenter.npm_release_support.state import NpmReleaseState
from org.metadatacenter.npm_release_support.tools import Result, Tools


# Requests ------------------------------------------------------------------------------------


def _notes(tmp_path, text="- Fixes a thing.\n"):
    path = tmp_path / "notes.md"
    path.write_text(text)
    return str(path)


def test_a_run_orders_the_model_first_and_pins_it_in_every_consumer(tmp_path):
    notes = _notes(tmp_path)
    request = parse_run_request(
        ["cetp@0.2.0", "cee@2.0.34", "model@1.0.28"],
        ["model@1.0.29", "cee@2.0.35", "cetp@0.2.1"], None, None,
        [f"cee={notes}", f"cetp={notes}"])
    assert [row.package for row in request.packages] == ["model", "cee", "cetp"]
    assert [row.model_version for row in request.packages] == [None, "1.0.28", "1.0.28"]
    assert request.model_in_run


@pytest.mark.parametrize("specs,nexts,model,message", [
    (["cee@2.0.34"], ["cee@2.0.35"], None, "give --model-version"),
    (["cee@2.0.34"], [], "1.0.28", "give --next cee"),
    (["cee@2.0.34"], ["cee@2.0.34"], "1.0.28", "must exceed"),
    (["model@1.0.28", "cee@2.0.34"], ["model@1.0.29", "cee@2.0.35"], "1.0.27", "contradicts"),
    (["model@1.0"], ["model@1.0.1"], None, "not a MAJOR.MINOR.PATCH"),
    (["vue@1.0.0"], [], None, "unknown package"),
    (["model@1.0.28"], ["model@1.0.29"], "1.0.27", "contradicts"),
])
def test_a_request_that_leaves_a_version_to_inference_is_refused(tmp_path, specs, nexts, model, message):
    notes = [f"cee={_notes(tmp_path)}"] if any(spec.startswith("cee") for spec in specs) else []
    with pytest.raises(NpmReleaseError, match=message):
        parse_run_request(specs, nexts, model, None, notes)


def test_a_changelog_package_needs_notes_without_headings(tmp_path):
    with pytest.raises(NpmReleaseError, match="give --notes cee"):
        parse_run_request(["cee@2.0.34"], ["cee@2.0.35"], "1.0.28", None, [])
    heading = _notes(tmp_path, "## Added\n- A thing.\n")
    with pytest.raises(NpmReleaseError, match="contain a heading"):
        parse_run_request(["cee@2.0.34"], ["cee@2.0.35"], "1.0.28", None, [f"cee={heading}"])


def test_notes_may_group_their_bullets_under_changelog_subsections(tmp_path):
    text = "- First public release.\n\n### Added\n\n- A thing.\n\n### Fixed\n\n- A defect.\n"
    request = parse_run_request(["ced@0.1.0"], ["ced@0.1.1"], "1.0.27", None,
                                [f"ced={_notes(tmp_path, text)}"])
    assert request.packages[0].notes == text
    # The proof cuts the entry from its heading to the next `## [`, which a subsection never is.
    changelog = HEADER + "## [0.1.0-dev.20260916.2593d382] - 2026-09-16 (Nexus)\n\n- Old.\n"
    entry = changelog_entry("0.1.0", "2026-11-09", "1.0.27", text)
    result = insert_changelog_entry(changelog, "0.1.0", entry)
    start = result.index("## [0.1.0]")
    assert result[:start] + result[result.index("## [0.1.0-dev."):] == changelog
    for heading in ("#### Deeper\n- A thing.\n", "### added\n- A thing.\n", "# Title\n- A thing.\n"):
        with pytest.raises(NpmReleaseError, match="contain a heading"):
            parse_run_request(["ced@0.1.0"], ["ced@0.1.1"], "1.0.27", None,
                              [f"ced={_notes(tmp_path, heading)}"])


# Edits ---------------------------------------------------------------------------------------


HEADER = "# Changelog\n\nAll notable changes.\n\n"


def test_the_entry_goes_above_the_newest_release_and_leaves_unreleased_alone():
    text = HEADER + "## [Unreleased]\n\n- Pending.\n\n## [2.0.33] - 2026-11-07\n\n- Old.\n"
    entry = changelog_entry("2.0.34", "2026-11-09", "1.0.28", "- New.\n")
    result = insert_changelog_entry(text, "2.0.34", entry)
    assert result == (HEADER + "## [Unreleased]\n\n- Pending.\n\n"
                      "## [2.0.34] - 2026-11-09\n\nAligns with `cedar-model-typescript-library@1.0.28`.\n\n"
                      "- New.\n\n## [2.0.33] - 2026-11-07\n\n- Old.\n")
    # What the platform release's promotion proof checks: cutting the entry out restores the file.
    start = result.index("## [2.0.34]")
    assert result[:start] + result[result.index("## [2.0.33]"):] == text
    with pytest.raises(NpmReleaseError, match="already has an entry"):
        insert_changelog_entry(result, "2.0.34", entry)


def test_the_load_trace_and_published_name_change_one_value_each():
    source = "  private static INNER_VERSION = '2026-11-07 21:00 a1648a13';\n"
    assert stamp_load_trace(source, "2026-11-09 14:00") == "  private static INNER_VERSION = '2026-11-09 14:00';\n"
    manifest = '{\n  "name": "@org.metadatacenter/cedar-model-typescript-library",\n  "version": "1"\n}\n'
    assert published_manifest_name(manifest, "cedar-model-typescript-library") == (
        '{\n  "name": "cedar-model-typescript-library",\n  "version": "1"\n}\n')


# The world -----------------------------------------------------------------------------------


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def _make_repository(home: Path, origins: Path, definition, files: dict[str, str]) -> Path:
    origin = origins / f"{definition.repository}.git"
    git(origins, "init", "--bare", "-b", "main", str(origin))
    checkout = home / definition.repository
    git(home, "clone", str(origin), str(checkout))
    git(checkout, "config", "user.email", "release@example.org")
    git(checkout, "config", "user.name", "Release")
    git(checkout, "checkout", "-b", "main")
    for name, content in files.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(checkout, "add", "-A")
    git(checkout, "commit", "-m", "Initial")
    git(checkout, "push", "origin", "main")
    git(checkout, "checkout", "-b", "develop")
    git(checkout, "push", "origin", "develop")
    git(checkout, "branch", "--set-upstream-to=origin/develop", "develop")
    return checkout


def _model_files() -> dict[str, str]:
    version = "1.0.28-dev.20261106.95d31b9"
    return {
        ".gitignore": "dist/\nnode_modules/\n",
        "README.md": "# CEDAR Model TypeScript Library\n\nText.\n",
        "package.json": json.dumps({"name": "cedar-model-typescript-library", "version": version}, indent=2) + "\n",
        "package-lock.json": json.dumps({"name": "cedar-model-typescript-library", "version": version,
                                         "packages": {"": {"version": version}}}, indent=2) + "\n",
        "package-dist.json": json.dumps({"name": "@org.metadatacenter/cedar-model-typescript-library",
                                         "version": version}, indent=2) + "\n",
    }


def _cee_files() -> dict[str, str]:
    dev_model = "1.0.28-dev.20261107.g06be587cca8c.t1858"
    pin = f"npm:@org.metadatacenter/cedar-model-typescript-library@{dev_model}"
    lock_entry = {"version": dev_model, "resolved": route_module.nexus_tarball_url(
        "@org.metadatacenter/cedar-model-typescript-library", dev_model), "integrity": "sha512-dev"}
    manifest = lambda name: json.dumps(  # noqa: E731
        {"name": name, "version": "2.0.34-dev.20261107.a1648a13",
         "dependencies": {"cedar-model-typescript-library": pin}}, indent=2) + "\n"
    lock = json.dumps({"packages": {"": {}, "node_modules/cedar-model-typescript-library": lock_entry}},
                      indent=2) + "\n"
    return {
        ".gitignore": "dist-npm/\nnode_modules/\n",
        "README.md": "# CEDAR Embeddable Editor (CEE)\n\nText.\n",
        "CHANGELOG.md": HEADER + "## [2.0.33] - 2026-11-07\n\n- Old.\n",
        "package.json": manifest("cedar-embeddable-editor"),
        "package-lock.json": lock,
        "visual/package.json": manifest("visual"),
        "visual/package-lock.json": lock,
        PACKAGES["cee"].load_trace: "  private static INNER_VERSION = '2026-11-07 21:00 a1648a13';\n",
    }


def _component_files(name: str) -> dict[str, str]:
    """A CED or CETP checkout: one manifest that pins the model, and a browser test package."""
    files = {key: value for key, value in _cee_files().items()
             if not key.startswith("visual/") and key != PACKAGES["cee"].load_trace}
    files["README.md"] = f"# {name}\n\nText.\n"
    files["CHANGELOG.md"] = HEADER + "## [0.1.0-dev.20260916.2593d382] - 2026-09-16 (Nexus)\n\n- Old.\n"
    manifest = json.loads(files["package.json"])
    manifest.update(name=name, version="0.1.0-dev.20261002.8bca7f71")
    files["package.json"] = json.dumps(manifest, indent=2) + "\n"
    files["browser/package.json"] = json.dumps({"name": f"{name}-browser"}, indent=2) + "\n"
    return files


def _tarball(stage: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=f"package/{path.relative_to(stage)}")
    return buffer.getvalue()


class World(Tools):
    """Real Git; npm, GitHub and the long commands answered from memory."""

    def __init__(self, home: Path):
        super().__init__({}, sleeper=lambda seconds: None, clock=self._tick, reporter=lambda line: None)
        self.home = home
        self.time = 0.0
        self.registry: dict[str, bytes] = {}
        self.pulls: dict[str, dict] = {}
        self.releases: dict[tuple[str, str], dict] = {}
        self.commands: list[tuple[str, ...]] = []
        self.fail_publish_after_upload = False
        self.failing_ci: set[str] = set()
        self.unreachable: set[str] = set()

    def _tick(self):
        self.time += 1
        return self.time

    # npm and the long commands

    def logged(self, argv, cwd, log):
        self.commands.append(tuple(argv))
        cwd = Path(cwd)
        if argv[:2] == ["npm", "version"]:
            version = argv[2]
            for name in ("package.json", "package-lock.json"):
                path = cwd / name
                value = json.loads(path.read_text())
                value["version"] = version
                if name == "package-lock.json":
                    value.setdefault("packages", {}).setdefault("", {})["version"] = version
                _write_json(path, value)
            if (cwd / "package-dist.json").exists():
                value = json.loads((cwd / "package-dist.json").read_text())
                value["version"] = version
                _write_json(cwd / "package-dist.json", value)
        elif argv[:3] == ["npm", "install", "--save-exact"]:
            spec = argv[3]
            name, _, rest = spec.partition("@")
            pin = rest
            version = rest.rsplit("@", 1)[-1]
            manifest = json.loads((cwd / "package.json").read_text())
            manifest["dependencies"][name] = pin
            _write_json(cwd / "package.json", manifest)
            lock = json.loads((cwd / "package-lock.json").read_text())
            resolved = (f"{PUBLIC_REGISTRY}{name}/-/{name}-{version}.tgz" if not pin.startswith("npm:")
                        else route_module.nexus_tarball_url(f"@org.metadatacenter/{name}", version))
            lock["packages"][f"node_modules/{name}"] = {"version": version, "resolved": resolved,
                                                        "integrity": f"sha512-{version}"}
            _write_json(cwd / "package-lock.json", lock)
        elif argv[:3] in (["npm", "run", "test:package"], ["npm", "run", "dist"]):
            root = json.loads((cwd / "package.json").read_text())
            definition = next(row for row in PACKAGES.values() if (self.home / row.repository) == cwd)
            stage = cwd / definition.stage_directory
            stage.mkdir(parents=True, exist_ok=True)
            dev = "-dev." in root["version"]
            _write_json(stage / "package.json", {
                "name": definition.dev_name if dev else definition.public_name, "version": root["version"]})
            (stage / "README.md").write_bytes((cwd / "README.md").read_bytes())

    def npm(self, cwd, *args, check=True):
        self.commands.append(("npm", *args))
        if args[0] == "whoami":
            return Result(0, "metadatacenter\n", "")
        if args[0] == "publish":
            content = Path(args[1]).read_bytes()
            identity = inspect_tarball(content, "tarball").package
            key = f"{identity['name']}@{identity['version']}"
            if "--dry-run" in args:
                return Result(0, "", f"npm notice Publishing to {PUBLIC_REGISTRY} with tag latest "
                                     f"and public access (dry-run)\n+ {key}\n")
            if key in self.registry:
                return Result(1, "", "npm error code EPUBLISHCONFLICT")
            self.registry[key] = content
            if self.fail_publish_after_upload:
                self.fail_publish_after_upload = False
                return Result(1, "", "npm error network socket hang up")
            return Result(0, f"+ {key}\n", "")
        if args[0] == "view" and args[2] == "readme":
            content = self.registry.get(args[1])
            if not content:
                return Result(1, "", "E404")
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                return Result(0, archive.extractfile("package/README.md").read().decode(), "")
        raise AssertionError(f"unexpected npm {args}")

    def npm_view(self, spec, registry):
        if "@" in spec[1:]:
            content = self.registry.get(spec)
            if not content:
                return None
            inspection = inspect_tarball(content, spec)
            return {"dist": {"integrity": inspection.integrity, "tarball": f"memory://{spec}"}}
        versions = [key.rsplit("@", 1)[1] for key in self.registry if key.rsplit("@", 1)[0] == spec]
        if not versions:
            return None
        latest = max(versions, key=lambda value: tuple(int(part) for part in value.split(".")))
        return {"dist-tags": {"latest": latest}}

    def download(self, url):
        return self.registry[url.removeprefix("memory://")]

    def reachable(self, url):
        return url not in self.unreachable

    # GitHub

    def gh(self, *args, check=True):
        self.commands.append(("gh", *args))
        repository = args[args.index("--repo") + 1].split("/")[1] if "--repo" in args else None
        origin = self.home.parent / "origins" / f"{repository}.git"
        if args[:2] == ("pr", "list"):
            rows = [pull for pull in self.pulls.values() if pull["repository"] == repository and pull["state"] == "OPEN"]
            return Result(0, json.dumps([{"number": pull["number"], "url": pull["url"],
                                          "headRefOid": git(origin, "rev-parse", "develop")} for pull in rows]), "")
        if args[:2] == ("pr", "create"):
            number = len(self.pulls) + 1
            self.pulls[str(number)] = {"repository": repository, "number": number, "state": "OPEN",
                                       "url": f"https://github.com/metadatacenter/{repository}/pull/{number}"}
            return Result(0, self.pulls[str(number)]["url"] + "\n", "")
        if args[:2] == ("pr", "view"):
            pull = self.pulls[args[2]]
            head = pull.get("head") or git(origin, "rev-parse", "develop")
            return Result(0, json.dumps({
                "state": pull["state"], "headRefOid": head,
                "mergeCommit": {"oid": pull["merge"]} if pull.get("merge") else None,
                "statusCheckRollup": [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED",
                                       "conclusion": "SUCCESS"}]}), "")
        if args[:2] == ("pr", "merge"):
            pull = self.pulls[args[2]]
            head = git(origin, "rev-parse", "develop")
            assert args[args.index("--match-head-commit") + 1] == head
            work = self.home.parent / f"merge-{repository}-{pull['number']}"
            git(self.home.parent, "clone", "-b", "main", str(origin), str(work))
            git(work, "config", "user.email", "github@example.org")
            git(work, "config", "user.name", "GitHub")
            git(work, "merge", "--no-ff", "-m", f"Merge pull request #{pull['number']}", head)
            git(work, "push", "origin", "main")
            pull.update(state="MERGED", merge=git(work, "rev-parse", "HEAD"), head=head)
            return Result(0, "", "")
        if args[:2] == ("release", "view"):
            key = (repository, args[2])
            if key not in self.releases:
                return Result(1, "", "release not found")
            return Result(0, json.dumps(self.releases[key]), "")
        if args[:2] == ("release", "create"):
            self.releases[(repository, args[2])] = {"url": f"https://github.com/r/{args[2]}", "isDraft": True}
            return Result(0, "", "")
        raise AssertionError(f"unexpected gh {args}")

    def probe(self, repository, revision, **_):
        conclusion = "failure" if revision in self.failing_ci else "success"
        return GithubCIProbe(repository, revision, (
            {"id": 1, "name": "Test", "event": "push", "head_sha": revision, "status": "completed",
             "conclusion": conclusion, "html_url": "https://ci/1"},
            {"id": 2, "name": "Test", "event": "pull_request", "head_sha": revision, "status": "completed",
             "conclusion": conclusion, "html_url": "https://ci/2"},
        ), 1)


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    origins = tmp_path / "origins"
    home.mkdir()
    origins.mkdir()

    def pack(stage, destination, environment=None):
        destination.mkdir(parents=True, exist_ok=True)
        content = _tarball(Path(stage))
        tarball = destination / "package.tgz"
        tarball.write_bytes(content)
        return tarball, inspect_tarball(content, str(stage))

    monkeypatch.setattr(route_module, "pack_and_inspect", pack)
    world = World(home)
    world.origins = origins
    return world


def _route(world, state, **kwargs):
    return NpmReleaseRoute(world.home, world, state, reporter=lambda line: None, ci_probe=world.probe,
                           registry_delays=(0, 0), **kwargs)


def _run(world, state, packages, *, defer=True):
    records = []
    for request in packages:
        definition = PACKAGES[request.package]
        repository = world.home / definition.repository
        records.append({**request.as_record(), "repository": definition.repository, "completed": [],
                        "evidence": {}, "stage": None, "baseCommit": git(repository, "rev-parse", "HEAD")})
    run = {"id": "run-1", "packages": records, "completed": [], "evidence": {}, "deferBaselines": defer}
    state.start(run)
    return run


# The route -----------------------------------------------------------------------------------


def test_the_model_release_publishes_the_gated_bytes_tags_main_and_advances_develop(world, tmp_path):
    checkout = _make_repository(world.home, world.origins, PACKAGES["model"], _model_files())
    state = NpmReleaseState(tmp_path / "state")
    request = parse_run_request(["model@1.0.28"], ["model@1.0.29"], None, None, [])
    run = _run(world, state, request.packages)

    finished = _route(world, state).drive(run)

    assert finished["conclusion"] == "released"
    record = finished["packages"][0]
    content = world.registry["cedar-model-typescript-library@1.0.28"]
    assert inspect_tarball(content, "x").integrity == record["evidence"]["build-main"]["integrity"]
    origin = world.origins / "cedar-model-typescript-library.git"
    merge = record["evidence"]["merge"]["commit"]
    assert git(origin, "rev-parse", "release-1.0.28^{commit}") == merge
    assert git(origin, "rev-parse", "main") == merge
    advanced = json.loads(git(origin, "show", "develop:package-dist.json"))
    assert advanced["name"] == "@org.metadatacenter/cedar-model-typescript-library"
    assert re.fullmatch(r"1\.0\.29-dev\.\d{8}\.[0-9a-f]{7,}", advanced["version"])
    assert git(origin, "merge-base", "--is-ancestor", merge, "develop") == ""
    released = json.loads(git(origin, "show", f"{merge}:package-dist.json"))
    assert released == {"name": "cedar-model-typescript-library", "version": "1.0.28"}
    assert git(checkout, "log", "-1", "--format=%s", "origin/develop") == (
        "Advance TypeScript model library to the next development version")


def test_a_consumer_pins_the_public_model_and_returns_to_its_development_pin(world, tmp_path):
    _make_repository(world.home, world.origins, PACKAGES["cee"], _cee_files())
    state = NpmReleaseState(tmp_path / "state")
    notes = _notes(tmp_path, "- Adds a thing.\n")
    request = parse_run_request(["cee@2.0.34"], ["cee@2.0.35"], "1.0.28", None, [f"cee={notes}"])
    run = _run(world, state, request.packages)

    finished = _route(world, state).drive(run)

    record = finished["packages"][0]
    origin = world.origins / "cedar-embeddable-editor.git"
    merge = record["evidence"]["merge"]["commit"]
    for manifest in ("package.json", "visual/package.json"):
        released = json.loads(git(origin, "show", f"{merge}:{manifest}"))
        assert released["dependencies"]["cedar-model-typescript-library"] == "1.0.28"
        advanced = json.loads(git(origin, "show", f"develop:{manifest}"))
        assert advanced["dependencies"]["cedar-model-typescript-library"] == (
            "npm:@org.metadatacenter/cedar-model-typescript-library@1.0.28-dev.20261107.g06be587cca8c.t1858")
    changelog = git(origin, "show", f"{merge}:CHANGELOG.md")
    assert "## [2.0.34] - " in changelog and "Aligns with `cedar-model-typescript-library@1.0.28`." in changelog
    trace = git(origin, "show", f"{merge}:{PACKAGES['cee'].load_trace}")
    assert re.search(r"INNER_VERSION = '\d{4}-\d\d-\d\d \d\d:\d\d';", trace)
    assert ("cedar-embeddable-editor", "release-2.0.34") in world.releases
    demos = [command for command in world.commands if "cee-demos" in command]
    assert len(demos) == 2 and demos[1][-1].endswith(".tgz")


def test_a_publish_interrupted_after_upload_resumes_by_proving_the_bytes(world, tmp_path):
    _make_repository(world.home, world.origins, PACKAGES["model"], _model_files())
    state = NpmReleaseState(tmp_path / "state")
    request = parse_run_request(["model@1.0.28"], ["model@1.0.29"], None, None, [])
    run = _run(world, state, request.packages)
    world.fail_publish_after_upload = True

    with pytest.raises(NpmReleaseError, match="socket hang up"):
        _route(world, state).drive(run)
    stopped, _ = state.read()
    assert stopped["failure"]["stage"] == "publish"
    assert "publish" not in stopped["packages"][0]["completed"]

    finished = _route(world, state).drive(stopped)
    assert finished["packages"][0]["evidence"]["publish"]["published"] == "already"
    assert finished["conclusion"] == "released"


def test_red_ci_stops_before_merge_and_the_run_can_still_be_abandoned(world, tmp_path):
    checkout = _make_repository(world.home, world.origins, PACKAGES["model"], _model_files())
    state = NpmReleaseState(tmp_path / "state")
    request = parse_run_request(["model@1.0.28"], ["model@1.0.29"], None, None, [])
    run = _run(world, state, request.packages)
    original_commit = route_module.NpmReleaseRoute._commit

    def commit_and_fail_ci(self, definition, record, run):
        evidence = original_commit(self, definition, record, run)
        world.failing_ci.add(evidence["commit"])
        return evidence

    route = _route(world, state)
    route._commit = lambda definition, record, run: commit_and_fail_ci(route, definition, record, run)
    with pytest.raises(NpmReleaseError, match="failed CI"):
        route.drive(run)
    stopped, _ = state.read()
    assert stopped["failure"]["stage"] == "checks"
    assert git(world.origins / "cedar-model-typescript-library.git", "rev-parse", "main") == git(
        checkout, "rev-parse", "origin/main")
    assert not world.registry


def test_ci_still_running_at_the_deadline_waits_rather_than_fails(world, tmp_path):
    _make_repository(world.home, world.origins, PACKAGES["model"], _model_files())
    state = NpmReleaseState(tmp_path / "state")
    request = parse_run_request(["model@1.0.28"], ["model@1.0.29"], None, None, [])
    run = _run(world, state, request.packages)

    def pending(repository, revision, **_):
        return GithubCIProbe(repository, revision, (
            {"id": 1, "name": "Test", "event": "push", "head_sha": revision, "status": "in_progress"},), 1)

    route = NpmReleaseRoute(world.home, world, state, reporter=lambda line: None, ci_probe=pending,
                            registry_delays=(0,), ci_timeout_seconds=10)
    with pytest.raises(NpmReleaseWait, match="still running"):
        route.drive(run)
    stopped, _ = state.read()
    assert stopped["waiting"]["stage"] == "checks" and not stopped["failure"]


def test_the_baseline_refresh_commits_only_its_file_and_waits_for_a_platform_release(world, tmp_path):
    _make_repository(world.home, world.origins, PACKAGES["model"], _model_files())
    development = world.home / "cedar-development"
    origin = world.origins / "cedar-development.git"
    git(world.origins, "init", "--bare", "-b", "develop", str(origin))
    git(world.home, "clone", str(origin), str(development))
    git(development, "config", "user.email", "release@example.org")
    git(development, "config", "user.name", "Release")
    git(development, "checkout", "-b", "develop")
    _write_json(development / "ops/frontend-train.json", {"auditBaselines": []})
    (development / "other.md").write_text("one\n")
    git(development, "add", "-A")
    git(development, "commit", "-m", "Initial")
    git(development, "push", "origin", "develop")
    (development / "other.md").write_text("another session's edit\n")

    state = NpmReleaseState(tmp_path / "state")
    request = parse_run_request(["model@1.0.28"], ["model@1.0.29"], None, None, [])
    run = _run(world, state, request.packages, defer=False)
    active = {"value": True}

    def refresh(repositories):
        _write_json(development / "ops/frontend-train.json", {"auditBaselines": repositories})
        return 0

    route = _route(world, state, baseline_refresher=refresh, platform_release_active=lambda: active["value"])
    with pytest.raises(NpmReleaseWait, match="platform release is active"):
        route.drive(run)
    active["value"] = False
    waiting, _ = state.read()
    finished = route.drive(waiting)

    assert finished["evidence"]["baselines"]["changed"]
    assert json.loads(git(origin, "show", "develop:ops/frontend-train.json")) == {
        "auditBaselines": ["cedar-model-typescript-library"]}
    assert git(origin, "show", "develop:other.md") == "one"
    assert (development / "other.md").read_text() == "another session's edit\n"


# Preflight -----------------------------------------------------------------------------------


def _tokens_lock(files: dict[str, str]) -> tuple[dict[str, str], str]:
    url = route_module.nexus_tarball_url("@org.metadatacenter/cedar-design-tokens", "0.1.0-dev.20261106.5294fab4")
    lock = json.loads(files["package-lock.json"])
    lock["packages"]["node_modules/@org.metadatacenter/cedar-design-tokens"] = {
        "version": "0.1.0-dev.20261106.5294fab4", "resolved": url}
    return {**files, "package-lock.json": json.dumps(lock, indent=2) + "\n"}, url


def test_preflight_refuses_a_lock_naming_a_package_nexus_has_deleted(world, tmp_path):
    files, url = _tokens_lock(_cee_files())
    _make_repository(world.home, world.origins, PACKAGES["cee"], files)
    notes = _notes(tmp_path)
    request = parse_run_request(["cee@2.0.34"], ["cee@2.0.35"], "1.0.28", None, [f"cee={notes}"])
    world.registry["cedar-model-typescript-library@1.0.28"] = _tarball_of("cedar-model-typescript-library", "1.0.28", tmp_path)
    preflight = NpmReleasePreflight(world.home, world)

    _, findings = preflight._package(request.packages[0], request)
    assert findings == []

    world.unreachable.add(url)
    _, findings = preflight._package(request.packages[0], request)
    assert len(findings) == 1 and "cedar-design-tokens 0.1.0-dev.20261106.5294fab4, which Nexus no longer serves" in findings[0]


def _tarball_of(name: str, version: str, tmp_path: Path) -> bytes:
    stage = tmp_path / f"stage-{name}-{version}"
    _write_json(stage / "package.json", {"name": name, "version": version})
    return _tarball(stage)


def test_preflight_refuses_a_published_or_older_version_and_an_unreleased_model(world, tmp_path):
    _make_repository(world.home, world.origins, PACKAGES["cee"], _cee_files())
    notes = _notes(tmp_path)
    world.registry["cedar-embeddable-editor@2.0.34"] = _tarball_of("cedar-embeddable-editor", "2.0.34", tmp_path)
    preflight = NpmReleasePreflight(world.home, world)
    for version, expected in (("2.0.34", "already on npmjs"), ("2.0.33", "would not be newer")):
        request = parse_run_request([f"cee@{version}"], ["cee@2.0.40"], "1.0.99", None, [f"cee={notes}"])
        _, findings = preflight._package(request.packages[0], request)
        assert any(expected in finding for finding in findings), findings
        assert any("cedar-model-typescript-library@1.0.99 is not on npmjs" in finding for finding in findings)


def test_preflight_refuses_a_dirty_tree_and_a_diverged_main(world, tmp_path):
    checkout = _make_repository(world.home, world.origins, PACKAGES["model"], _model_files())
    git(checkout, "checkout", "main")
    (checkout / "hotfix.txt").write_text("fix\n")
    git(checkout, "add", "hotfix.txt")
    git(checkout, "commit", "-m", "Hotfix")
    git(checkout, "push", "origin", "main")
    git(checkout, "checkout", "develop")
    (checkout / "scratch.txt").write_text("x\n")
    request = parse_run_request(["model@1.0.28"], ["model@1.0.29"], None, None, [])
    _, findings = NpmReleasePreflight(world.home, world)._package(request.packages[0], request)
    assert any("uncommitted or untracked" in finding for finding in findings)
    assert any("main has commits develop lacks" in finding for finding in findings)


def test_a_logged_command_keeps_its_output_and_names_its_log_on_failure(tmp_path):
    lines = []
    tools = Tools({"PATH": "/usr/bin:/bin"}, reporter=lines.append)
    log = tmp_path / "logs" / "gate.log"
    tools.logged(["sh", "-c", "echo hello"], tmp_path, log)
    with pytest.raises(NpmReleaseError, match=re.escape(str(log))):
        tools.logged(["sh", "-c", "echo broken >&2; exit 3"], tmp_path, log)
    text = log.read_text()
    assert "hello" in text and "broken" in text
    assert any(line.startswith("  passed in") for line in lines)


# The commands --------------------------------------------------------------------------------


def test_the_commands_refuse_an_incomplete_request_and_an_abandon_after_merge(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from org.metadatacenter import npm_release

    monkeypatch.setenv("CEDAR_NPM_RELEASE_STATE_DIR", str(tmp_path / "state"))
    runner = CliRunner()
    result = runner.invoke(npm_release.app, ["plan", "cee@2.0.34", "--next", "cee@2.0.35"])
    assert result.exit_code == 1 and "give --model-version" in result.output

    result = runner.invoke(npm_release.app, ["status"])
    assert result.exit_code == 1 and "there is no npm release run" in result.output

    state = NpmReleaseState(tmp_path / "state")
    state.start({"id": "run-1", "completed": [], "evidence": {}, "packages": [
        {"package": "model", "version": "1.0.28", "completed": ["prepare", "merge"], "evidence": {}}]})
    result = runner.invoke(npm_release.app, ["abandon", "--reason", "wrong version"])
    assert result.exit_code == 1 and "already merged to main" in result.output
    assert state.active()


def test_the_release_command_group_offers_npm():
    from typer.testing import CliRunner

    from org.metadatacenter import release_train

    result = CliRunner().invoke(release_train.app, ["npm", "--help"])
    assert result.exit_code == 0
    for command in ("plan", "start", "resume", "status", "abandon"):
        assert command in result.output


def test_a_ced_release_publishes_the_public_package_and_returns_develop_to_its_pin(world, tmp_path):
    _make_repository(world.home, world.origins, PACKAGES["ced"], _component_files("cedar-embeddable-designer"))
    state = NpmReleaseState(tmp_path / "state")
    notes = _notes(tmp_path, "- First public release.\n")
    request = parse_run_request(["ced@0.1.0"], ["ced@0.1.1"], "1.0.28", None, [f"ced={notes}"])
    run = _run(world, state, request.packages)

    finished = _route(world, state).drive(run)

    assert finished["conclusion"] == "released"
    published = inspect_tarball(world.registry["cedar-embeddable-designer@0.1.0"], "x").package
    assert published == {"name": "cedar-embeddable-designer", "version": "0.1.0"}
    origin = world.origins / "cedar-embeddable-designer.git"
    merge = finished["packages"][0]["evidence"]["merge"]["commit"]
    assert git(origin, "rev-parse", "release-0.1.0^{commit}") == merge
    released = json.loads(git(origin, "show", f"{merge}:package.json"))
    assert released["dependencies"]["cedar-model-typescript-library"] == "1.0.28"
    advanced = json.loads(git(origin, "show", "develop:package.json"))
    assert re.fullmatch(r"0\.1\.1-dev\.\d{8}\.[0-9a-f]{7,}", advanced["version"])
    assert advanced["dependencies"]["cedar-model-typescript-library"].startswith(
        "npm:@org.metadatacenter/cedar-model-typescript-library@1.0.28-dev.")
    changelog = git(origin, "show", f"{merge}:CHANGELOG.md")
    assert "## [0.1.0] - " in changelog and "- First public release." in changelog
    assert ("cedar-embeddable-designer", "release-0.1.0") in world.releases

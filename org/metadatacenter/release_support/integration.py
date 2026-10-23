"""CEDAR release integration."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from pathlib import Path, PurePosixPath
import datetime as dt
import os
import re
import tempfile
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.policy import (
    GIT_SHA_RE,
    NEXT_DEVELOPMENT_VERSION_FILES,
    _development_only_repositories,
    _integration_repositories,
    maven_dependency_version,
)
from org.metadatacenter.release_support.refs import (
    ReleaseRefCreator,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.workspace import (
    ReleaseWorkspacePreparer,
)


class ReleaseRemoteIntegrator:
    """Integrate verified release commits into remote main/develop without touching source clones."""

    def __init__(self, state: ReleaseState, git_runner=None, remote_resolver=None, environment=None):
        self.state = state
        self.environment = dict(invocation_environment() if environment is None else environment)
        self.git = git_runner or ReleaseWorkspacePreparer(
            state, environment=self.environment,
        )
        self.ref_creator = ReleaseRefCreator(
            state, git_runner=self.git, environment=self.environment,
        )
        self.remote_resolver = remote_resolver or self._source_remote

    def _source_remote(self, repository: str) -> str:
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        root = Path(cedar_home) / repository
        remote = self.git._run(["git", "-C", str(root), "remote", "get-url", "origin"])
        expected = (
            rf"(?:https://github\.com/metadatacenter/{re.escape(repository)}(?:\.git)?|"
            rf"git@github\.com:metadatacenter/{re.escape(repository)}\.git)"
        )
        if not re.fullmatch(expected, remote):
            raise ReleaseError(f"{repository} origin is not the expected metadatacenter remote")
        return remote

    @staticmethod
    def _by_repository(manifest: dict) -> dict[str, dict]:
        completed = manifest.get("localRefs", {}).get("completedTasks", {})
        if not isinstance(completed, dict):
            raise ReleaseError("release has no verified local refs")
        result: dict[str, dict] = {}
        for record in completed.values():
            repository = record.get("repository")
            variant = record.get("variant")
            if not isinstance(repository, str) or variant not in {"release", "nextDevelopment"}:
                raise ReleaseError("release contains an invalid local ref record")
            if variant in result.setdefault(repository, {}):
                raise ReleaseError(f"release contains duplicate local refs for {repository}")
            result[repository][variant] = record
        expected = {
            task["id"] for task in ReleaseRefCreator(ReleaseState()).tasks(manifest)
        }
        if set(completed) != expected:
            raise ReleaseError("release does not contain the complete verified local ref set")
        return result

    SURVEY_VERSION_FILES = frozenset({
        "pom.xml", "package.json", "package-lock.json", "npm-shrinkwrap.json",
    })

    def survey(self, manifest: dict) -> dict[str, list[str]]:
        """Compare every release remote with the train source before anything is built.

        Both questions this answers are otherwise reached only at remote integration, once
        the release has already spent its Maven and frontend builds: whether a remote
        develop has moved off the train source, which is fatal, and what main carries that
        develop does not, which the release replaces. Answering them from ls-remote costs
        seconds and keeps a stale remote from being discovered hours in.
        """
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        findings: dict[str, list[str]] = {}
        # Every repository is examined before the refusal, because the remedy depends on how many
        # moved. One drifted repository can often be explained; eighteen means the train is spent
        # and a new one has to be built, and reporting only the first hides that difference.
        drifted: list[tuple[str, str]] = []
        captured = 0
        for repository in _integration_repositories(manifest):
            captured += 1
            source = manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                raise ReleaseError(f"{repository} has no recorded train source")
            root = Path(cedar_home) / repository
            remote = self.remote_resolver(repository)
            references = self._remote_refs(
                root, remote, ["refs/heads/main", "refs/heads/develop"],
            )
            develop = references.get("refs/heads/develop")
            main = references.get("refs/heads/main")
            if develop is None or main is None:
                raise ReleaseError(f"{repository} remote must contain main and develop")
            if develop != source:
                drifted.append((repository, source))
                continue
            self.git._run([
                "git", "-C", str(root), "fetch", "--quiet", "--no-tags", remote,
                "+refs/heads/main:refs/remotes/cedar-release/survey-main",
                "+refs/heads/develop:refs/remotes/cedar-release/survey-develop",
            ])
            base = self.git._run([
                "git", "-C", str(root), "merge-base",
                "refs/remotes/cedar-release/survey-main",
                "refs/remotes/cedar-release/survey-develop",
            ])
            changed_on_main = set(self.git._run([
                "git", "-C", str(root), "diff", "--name-only", base,
                "refs/remotes/cedar-release/survey-main",
            ]).splitlines())
            changed_on_develop = set(self.git._run([
                "git", "-C", str(root), "diff", "--name-only", base,
                "refs/remotes/cedar-release/survey-develop",
            ]).splitlines())
            replaced = sorted(
                path for path in changed_on_main - changed_on_develop
                if path and PurePosixPath(path).name not in self.SURVEY_VERSION_FILES
            )
            if replaced:
                findings[repository] = replaced
        if len(drifted) == 1:
            repository, source = drifted[0]
            raise ReleaseError(
                f"{repository} develop advanced beyond train source {source}"
            )
        if drifted:
            raise ReleaseError(
                f"{len(drifted)} of {captured} captured repositories advanced beyond their train "
                f"source, so this train can no longer back a release: "
                + ", ".join(repository for repository, _ in drifted)
            )
        # A repository the release only advances is free to move, since the release commits
        # onto its develop as it finds it. Only the files it advances must be as captured.
        for repository in _development_only_repositories(manifest):
            source = manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                raise ReleaseError(f"{repository} has no recorded train source")
            root = Path(cedar_home) / repository
            remote = self.remote_resolver(repository)
            self.git._run([
                "git", "-C", str(root), "fetch", "--quiet", "--no-tags", remote,
                "+refs/heads/develop:refs/remotes/cedar-release/survey-develop",
            ])
            self._require_captured_files(
                manifest, repository, root, source, "refs/remotes/cedar-release/survey-develop",
                list(NEXT_DEVELOPMENT_VERSION_FILES.get(repository, {})),
                "build a new train, which captures the file as develop now holds it",
            )
        return findings

    def tasks(self, manifest: dict) -> list[dict]:
        by_repository = self._by_repository(manifest)
        development_only = _development_only_repositories(manifest)
        tasks = []
        for repository, records in by_repository.items():
            release = records.get("release")
            if not isinstance(release, dict):
                if repository not in development_only:
                    raise ReleaseError(f"release has no stable local ref for {repository}")
                release = None
            tasks.append({
                "id": repository,
                "repository": repository,
                "sourceRevision": manifest["sourceRepositories"][repository],
                "release": release,
                "nextDevelopment": records.get("nextDevelopment"),
            })
        return tasks

    def _remote_refs(self, root: Path, remote: str, references: list[str]) -> dict[str, str]:
        output = self.git._run(["git", "-C", str(root), "ls-remote", "--refs", remote, *references])
        result = {}
        for line in output.splitlines():
            commit, reference = line.split("\t", 1)
            if not GIT_SHA_RE.fullmatch(commit):
                raise ReleaseError(f"remote returned an invalid commit for {reference}")
            result[reference] = commit
        return result

    def _integration_commit(
        self,
        root: Path,
        branch: str,
        base: str,
        prepared: dict,
        message: str,
    ) -> str:
        reference = f"refs/heads/{branch}"
        commit = self.ref_creator._ref(root, reference)
        if commit is None:
            # The integration commit must carry the prepared tree exactly, so it is written
            # from that tree rather than merged towards it. A merge preserves whatever the
            # base branch holds and the prepared side does not also change -- a file the
            # base still carries, or one the release deleted -- which silently readmits
            # unreleased content into the release. Building the commit from the prepared
            # tree makes the published branch equal the validated release content, and the
            # base branch keeps that history through the recorded parent.
            name, email = self.ref_creator._identity(root)
            commit = self.git._run([
                "git", "-c", f"user.name={name}", "-c", f"user.email={email}",
                "-c", "commit.gpgSign=false", "-C", str(root), "commit-tree",
                prepared["tree"], "-p", base, "-p", prepared["commit"], "-m", message,
            ])
            self.git._run(["git", "-C", str(root), "branch", "--force", branch, commit])
        parents = self.git._run([
            "git", "-C", str(root), "rev-list", "--parents", "-n", "1", commit,
        ]).split()
        if parents != [commit, base, prepared["commit"]]:
            raise ReleaseError(
                f"integration commit for {prepared['repository']} has unexpected parents"
            )
        tree = self.git._run(["git", "-C", str(root), "rev-parse", f"{commit}^{{tree}}"])
        if tree != prepared["tree"]:
            raise ReleaseError(
                f"integration commit for {prepared['repository']} changed the prepared tree"
            )
        # Publication packs each surface from the workspace's checked-out commit and refuses
        # any workspace whose HEAD is not the integration commit, so leave the workspace on
        # the integration branch. Writing the commit from the prepared tree does not move
        # HEAD by itself the way the merge this replaced did.
        self.git._run(["git", "-C", str(root), "switch", "--quiet", "--force", branch])
        return commit

    def _push(self, root: Path, remote: str, commit: str, reference: str) -> None:
        self.git._run(["git", "-C", str(root), "push", "--porcelain", remote,
                       f"{commit}:{reference}"])

    # How many times a develop that moves between fetch and push is followed before the
    # integration stops and leaves the rest to resume.
    DEVELOPMENT_PUSH_ATTEMPTS = 3

    def _blob(self, root: Path, revision: str, path: str) -> str | None:
        entry = self.git._run(["git", "-C", str(root), "ls-tree", revision, "--", path])
        return entry.split()[2] if entry else None

    def _contains(self, root: Path, ancestor: str, descendant: str) -> bool:
        return self.git._run([
            "git", "-C", str(root), "rev-list", "--count", f"{descendant}..{ancestor}",
        ]) == "0"

    def _fetch_develop(self, root: Path, remote: str) -> str:
        self.git._run([
            "git", "-C", str(root), "fetch", "--quiet", "--no-tags", remote,
            "+refs/heads/develop:refs/remotes/cedar-release/develop",
        ])
        return self.git._run([
            "git", "-C", str(root), "rev-parse", "refs/remotes/cedar-release/develop",
        ])

    def _require_captured_files(
        self, manifest: dict, repository: str, root: Path, source: str, develop: str,
        paths: list[str], remedy: str,
    ) -> None:
        """Refuse unless develop carries each file the release advances as the train captured it.

        The release stamped the captured file, so a develop that has since changed it holds
        something the release has neither seen nor validated.
        """
        old = manifest.get("sourceVersion") or ""
        artifacts = NEXT_DEVELOPMENT_VERSION_FILES.get(repository, {})
        for path in paths:
            current = self._blob(root, develop, path)
            if current is not None and current == self._blob(root, source, path):
                continue
            if current is None:
                raise ReleaseError(
                    f"{repository} develop no longer has {path}, which the release advances; "
                    f"{remedy}")
            content = self.git._run(["git", "-C", str(root), "cat-file", "blob", current])
            stale = [
                artifact for artifact in artifacts.get(path, ())
                if len(maven_dependency_version(artifact, old).findall(content.encode())) != 1
            ]
            detail = (
                f", and it no longer names {' and '.join(stale)} at train source version {old}"
                if stale else ""
            )
            raise ReleaseError(
                f"{repository} develop changed {path} after the train captured it{detail}; "
                f"{remedy}")

    def _development_commit(
        self, root: Path, branch: str, base: str, prepared: dict, message: str,
    ) -> str:
        """Merge the prepared commit into develop as it stands, taking its files alone.

        The tree is the base's, with each prepared file in place of the captured one. The files
        are unchanged on the base since the train, so this tree is what a merge would produce.
        Writing it directly leaves nothing for a merge strategy to decide.
        """
        reference = f"refs/heads/{branch}"
        commit = self.ref_creator._ref(root, reference)
        if commit is not None and self.git._run([
            "git", "-C", str(root), "rev-parse", f"{commit}^1",
        ]) != base:
            # Written on a develop that moved before the push landed; it is rebuilt on the tip.
            commit = None
        if commit is None:
            with tempfile.TemporaryDirectory(prefix="cedar-release-index-") as directory:
                environment = {
                    **self.environment, "GIT_INDEX_FILE": str(Path(directory) / "index"),
                }
                self.git._run(
                    ["git", "-C", str(root), "read-tree", base], environment=environment)
                for path in prepared["changedFiles"]:
                    entry = self.git._run([
                        "git", "-C", str(root), "ls-tree", prepared["commit"], "--", path,
                    ])
                    if not entry:
                        raise ReleaseError(
                            f"prepared commit for {prepared['repository']} has no {path}")
                    mode, _kind, blob = entry.split()[:3]
                    self.git._run([
                        "git", "-C", str(root), "update-index", "--add", "--cacheinfo",
                        f"{mode},{blob},{path}",
                    ], environment=environment)
                tree = self.git._run(
                    ["git", "-C", str(root), "write-tree"], environment=environment)
            name, email = self.ref_creator._identity(root)
            commit = self.git._run([
                "git", "-c", f"user.name={name}", "-c", f"user.email={email}",
                "-c", "commit.gpgSign=false", "-C", str(root), "commit-tree",
                tree, "-p", base, "-p", prepared["commit"], "-m", message,
            ])
            # The branch may be checked out from an earlier attempt, which `branch --force`
            # refuses to move; switching resets it and the checkout together.
            self.git._run([
                "git", "-C", str(root), "switch", "--quiet", "--force", "-C", branch, commit,
            ])
        parents = self.git._run([
            "git", "-C", str(root), "rev-list", "--parents", "-n", "1", commit,
        ]).split()
        if parents != [commit, base, prepared["commit"]]:
            raise ReleaseError(
                f"integration commit for {prepared['repository']} has unexpected parents")
        changed = set(filter(None, self.git._run([
            "git", "-C", str(root), "diff", "--no-renames", "--name-only", base, commit, "--",
        ]).splitlines()))
        if changed != set(prepared["changedFiles"]) or any(
            self._blob(root, commit, path) != self._blob(root, prepared["commit"], path)
            for path in prepared["changedFiles"]
        ):
            raise ReleaseError(
                f"integration commit for {prepared['repository']} changes more than the "
                "prepared files")
        self.git._run(["git", "-C", str(root), "switch", "--quiet", "--force", branch])
        return commit

    def _integrate_development(self, manifest: dict, task: dict) -> dict:
        """Advance develop alone, in a repository that takes no release commit.

        Such a repository moves on its own, so the release commits onto its develop as it finds
        it: a merge of the prepared commit, whose tree is develop's with the prepared files in
        place. It refuses only when develop has changed one of those files since the train, or
        no longer contains the train source. The prepared commit is published as the
        release/post branch. Main and the tags are the repository's own.
        """
        prepared = task["nextDevelopment"]
        self.ref_creator.verify_record(manifest, prepared)
        repository = task["repository"]
        root = Path(prepared["workspace"]) / repository
        remote = self.remote_resolver(repository)
        source = task["sourceRevision"]
        post_branch_ref = f"refs/heads/{prepared['branch']}"
        local_develop_ref = f"cedar-release/integrate-develop-{manifest['releaseVersion']}"
        message = (
            f"Advance after CEDAR {manifest['releaseVersion']} to "
            f"{manifest['nextDevelopmentVersion']}"
        )
        for _attempt in range(self.DEVELOPMENT_PUSH_ATTEMPTS):
            tip = self._fetch_develop(root, remote)
            existing = self.ref_creator._ref(root, f"refs/heads/{local_develop_ref}")
            landed = existing is not None and self._contains(root, existing, tip)
            if landed:
                base = self.git._run(["git", "-C", str(root), "rev-parse", f"{existing}^1"])
            else:
                base = tip
                if not self._contains(root, source, base):
                    raise ReleaseError(
                        f"{repository} develop no longer contains train source {source}, so "
                        "merging the prepared commit would restore history develop dropped")
                self._require_captured_files(
                    manifest, repository, root, source, base, prepared["changedFiles"],
                    "restore the captured file on develop, then resume the release",
                )
            commit = self._development_commit(root, local_develop_ref, base, prepared, message)
            existing_post = self._remote_refs(
                root, remote, [post_branch_ref]).get(post_branch_ref)
            if existing_post is not None and existing_post != prepared["commit"]:
                raise ReleaseError(f"{repository} remote {post_branch_ref} already differs")
            if existing_post is None:
                self._push(root, remote, prepared["commit"], post_branch_ref)
            if landed:
                break
            try:
                self._push(root, remote, commit, "refs/heads/develop")
                break
            except ReleaseError:
                # The remote refuses the push when develop has moved since the fetch, and the
                # next attempt follows it. Any other refusal stops the integration.
                moved = self._remote_refs(root, remote, ["refs/heads/develop"]).get(
                    "refs/heads/develop")
                if moved == base:
                    raise
        else:
            raise ReleaseError(
                f"{repository} develop moved during each of {self.DEVELOPMENT_PUSH_ATTEMPTS} "
                "integration attempts; resume the release to follow it again")
        tree = self.git._run(["git", "-C", str(root), "rev-parse", f"{commit}^{{tree}}"])
        record = {
            "id": task["id"],
            "repository": repository,
            "remote": remote,
            "sourceRevision": source,
            "develop": {"base": base, "commit": commit, "tree": tree},
            "postBranch": {"ref": post_branch_ref, "commit": prepared["commit"]},
            "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.verify_record(manifest, record)
        return record

    def integrate(self, manifest: dict, task: dict) -> dict:
        if task["release"] is None:
            return self._integrate_development(manifest, task)
        release = task["release"]
        next_development = task.get("nextDevelopment")
        self.ref_creator.verify_record(manifest, release)
        if next_development:
            self.ref_creator.verify_record(manifest, next_development)
        root = Path(release["workspace"]) / task["repository"]
        develop_root = (
            Path(next_development["workspace"]) / task["repository"]
            if next_development else root
        )
        remote = self.remote_resolver(task["repository"])
        release_branch_ref = f"refs/heads/{release['branch']}"
        tag_ref = f"refs/tags/{release['tag']}"
        post_branch_ref = (
            f"refs/heads/{next_development['branch']}" if next_development else None
        )
        queried = ["refs/heads/main", "refs/heads/develop", release_branch_ref, tag_ref]
        if post_branch_ref:
            queried.append(post_branch_ref)
        remote_refs = self._remote_refs(root, remote, queried)
        for fetch_root in {root, develop_root}:
            self.git._run([
                "git", "-C", str(fetch_root), "fetch", "--quiet", "--no-tags", remote,
                "+refs/heads/main:refs/remotes/cedar-release/main",
                "+refs/heads/develop:refs/remotes/cedar-release/develop",
            ])
        source = task["sourceRevision"]
        develop_prepared = next_development or release

        local_main_ref = f"cedar-release/integrate-main-{manifest['releaseVersion']}"
        local_develop_ref = f"cedar-release/integrate-develop-{manifest['releaseVersion']}"
        existing_main = self.ref_creator._ref(root, f"refs/heads/{local_main_ref}")
        existing_develop = self.ref_creator._ref(
            develop_root, f"refs/heads/{local_develop_ref}",
        )
        remote_main = remote_refs.get("refs/heads/main")
        remote_develop = remote_refs.get("refs/heads/develop")
        if remote_main is None or remote_develop is None:
            raise ReleaseError(f"{task['repository']} remote must contain main and develop")
        if existing_develop is None and remote_develop != source:
            raise ReleaseError(
                f"{task['repository']} develop advanced beyond train source {source}"
            )

        main_base = remote_main
        if existing_main is not None and remote_main == existing_main:
            main_base = self.git._run(["git", "-C", str(root), "rev-parse", f"{existing_main}^1"])
        develop_base = source
        main_commit = self._integration_commit(
            root, local_main_ref, main_base, release,
            f"Release CEDAR {manifest['releaseVersion']} from train {manifest['train']}",
        )
        develop_commit = self._integration_commit(
            develop_root, local_develop_ref, develop_base, develop_prepared,
            f"Advance after CEDAR {manifest['releaseVersion']} to "
            f"{manifest['nextDevelopmentVersion']}",
        )

        prepared_refs = [
            (root, release_branch_ref, release["commit"]),
            (root, tag_ref, release["commit"]),
        ]
        if post_branch_ref:
            prepared_refs.append((develop_root, post_branch_ref, next_development["commit"]))
        for push_root, reference, commit in prepared_refs:
            existing = remote_refs.get(reference)
            if existing is not None and existing != commit:
                raise ReleaseError(f"{task['repository']} remote {reference} already differs")
            if existing is None:
                self._push(push_root, remote, commit, reference)
                remote_refs[reference] = commit

        if remote_refs["refs/heads/main"] != main_commit:
            if remote_refs["refs/heads/main"] != main_base:
                raise ReleaseError(f"{task['repository']} main changed during release integration")
            self._push(root, remote, main_commit, "refs/heads/main")
            remote_refs["refs/heads/main"] = main_commit
        if remote_refs["refs/heads/develop"] != develop_commit:
            if remote_refs["refs/heads/develop"] != develop_base:
                raise ReleaseError(f"{task['repository']} develop changed during release integration")
            self._push(develop_root, remote, develop_commit, "refs/heads/develop")
            remote_refs["refs/heads/develop"] = develop_commit

        record = {
            "id": task["id"],
            "repository": task["repository"],
            "remote": remote,
            "sourceRevision": source,
            "releaseBranch": {"ref": release_branch_ref, "commit": release["commit"]},
            "tag": {"ref": tag_ref, "commit": release["commit"]},
            "main": {"base": main_base, "commit": main_commit, "tree": release["tree"]},
            "develop": {
                "base": develop_base,
                "commit": develop_commit,
                "tree": develop_prepared["tree"],
            },
            "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        if post_branch_ref:
            record["postBranch"] = {
                "ref": post_branch_ref, "commit": next_development["commit"],
            }
        self.verify_record(manifest, record)
        return record

    def verify_record(self, manifest: dict, record: dict) -> None:
        task = next((item for item in self.tasks(manifest) if item["id"] == record.get("id")), None)
        if task is None:
            raise ReleaseError(f"recorded remote task no longer exists: {record.get('id')}")
        if task["release"] is None:
            self._verify_development_record(task, record)
            return
        root = Path(task["release"]["workspace"]) / task["repository"]
        references = [
            record["releaseBranch"]["ref"], record["tag"]["ref"],
            "refs/heads/main", "refs/heads/develop",
        ]
        if record.get("postBranch"):
            references.append(record["postBranch"]["ref"])
        actual = self._remote_refs(root, record["remote"], references)
        expected = {
            record["releaseBranch"]["ref"]: record["releaseBranch"]["commit"],
            record["tag"]["ref"]: record["tag"]["commit"],
            "refs/heads/main": record["main"]["commit"],
            "refs/heads/develop": record["develop"]["commit"],
        }
        if record.get("postBranch"):
            expected[record["postBranch"]["ref"]] = record["postBranch"]["commit"]
        if actual != expected:
            raise ReleaseError(f"remote refs changed after integration for {task['repository']}")

    def _verify_development_record(self, task: dict, record: dict) -> None:
        """The post branch still names the prepared commit, and develop still contains its merge.

        Develop is free to move on after the release, as it was before it.
        """
        repository = task["repository"]
        root = Path(task["nextDevelopment"]["workspace"]) / repository
        post = record["postBranch"]
        actual = self._remote_refs(root, record["remote"], ["refs/heads/develop", post["ref"]])
        if actual.get(post["ref"]) != post["commit"]:
            raise ReleaseError(f"remote refs changed after integration for {repository}")
        commit = record["develop"]["commit"]
        if actual.get("refs/heads/develop") == commit:
            return
        if actual.get("refs/heads/develop") is None or not self._contains(
            root, commit, self._fetch_develop(root, record["remote"]),
        ):
            raise ReleaseError(
                f"remote develop no longer contains the integrated commit for {repository}")

    def retire_superseded_release_branches(self, manifest: dict) -> dict:
        """Delete the release branches earlier releases left behind.

        A release writes `release/pre-<version>` to every repository it touches and
        `release/post-<next>` to the release repositories, and `check_target_version_unused`
        refuses a version whose refs already exist. Nothing ever removed them, so they
        accumulated a pair per release per repository — 2.9.5 through 2.9.14 were still
        standing in every repository until they were swept by hand.

        The current pair stays. It is the evidence for the release being accepted, and the
        one a rollback would reach for; everything older is superseded by the tag that names
        the same tree.

        This is housekeeping rather than proof, and it runs after a release is already
        accepted, so a remote that refuses a delete is recorded and returned rather than
        raised. A branch nobody could remove is untidy; an accepted release reported as
        failed because of one would be wrong.
        """
        keep = {
            f"release/pre-{manifest['releaseVersion']}",
            f"release/post-{manifest['nextDevelopmentVersion']}",
        }
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        retired: list[dict] = []
        failures: list[dict] = []
        for repository in sorted(manifest.get("sourceRepositories", {})):
            root = Path(cedar_home) / repository
            if not root.is_dir():
                continue
            try:
                remote = self.remote_resolver(repository)
                listing = self.git._run([
                    "git", "-C", str(root), "ls-remote", "--heads", remote,
                    "refs/heads/release/*",
                ])
                present = sorted({
                    line.split("\t", 1)[-1][len("refs/heads/"):]
                    for line in listing.splitlines() if line.strip()
                })
                superseded = [branch for branch in present if branch not in keep]
                if not superseded:
                    continue
                self.git._run([
                    "git", "-C", str(root), "push", remote, "--delete", *superseded,
                ])
                retired.append({"repository": repository, "branches": superseded})
            except ReleaseError as error:
                failures.append({"repository": repository, "error": str(error)})
        return {
            "retiredAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "kept": sorted(keep),
            "repositories": retired,
            "branchCount": sum(len(record["branches"]) for record in retired),
            "failures": failures,
        }

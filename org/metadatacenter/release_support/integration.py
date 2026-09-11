"""CEDAR release integration."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from pathlib import Path, PurePosixPath
import datetime as dt
import os
import re
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.policy import (
    GIT_SHA_RE,
    _integration_repositories,
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
        for repository in _integration_repositories(manifest):
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
                raise ReleaseError(
                    f"{repository} develop advanced beyond train source {source}"
                )
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
        return findings

    def tasks(self, manifest: dict) -> list[dict]:
        by_repository = self._by_repository(manifest)
        tasks = []
        for repository, records in by_repository.items():
            release = records.get("release")
            if not isinstance(release, dict):
                raise ReleaseError(f"release has no stable local ref for {repository}")
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

    def integrate(self, manifest: dict, task: dict) -> dict:
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

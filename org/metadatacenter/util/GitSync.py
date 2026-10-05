import os
import subprocess
import time
from typing import Dict, Optional

from org.metadatacenter.model.GitSyncState import GitSyncState

UTF_8 = 'utf-8'


class GitSync:
    """
    How a working copy stands relative to its remote-tracking branch.

    A version declaration read from disk says what a checkout holds, not whether that checkout is
    current. The two answers differ whenever a clone has not been pulled, and a caller that has
    only the first will read a stale clone as a version defect. This resolves the second.

    Results are cached per git root, because the repository list contains sub-repositories that
    share one.
    """

    _cache: Dict[str, GitSyncState] = {}

    @classmethod
    def clear_cache(cls):
        cls._cache = {}

    @classmethod
    def state_for_dir(cls, path: str) -> GitSyncState:
        root = cls._git_root(path)
        if root is None:
            return GitSyncState.not_a_repo()
        if root not in cls._cache:
            cls._cache[root] = cls._resolve(root)
        return cls._cache[root]

    @classmethod
    def _resolve(cls, root: str) -> GitSyncState:
        branch = cls._run(root, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch is None:
            return GitSyncState.not_a_repo()
        if branch == "HEAD":
            return GitSyncState.detached(cls._fetch_age_seconds(root))

        counts = cls._run(root, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
        if counts is None:
            return GitSyncState.no_upstream(branch, cls._fetch_age_seconds(root))

        parts = counts.split()
        if len(parts) != 2:
            return GitSyncState.no_upstream(branch, cls._fetch_age_seconds(root))
        return GitSyncState.tracking(branch, int(parts[0]), int(parts[1]), cls._fetch_age_seconds(root))

    @staticmethod
    def _git_root(path: str) -> Optional[str]:
        if not os.path.isdir(path):
            return None
        return GitSync._run(path, ["rev-parse", "--show-toplevel"])

    @staticmethod
    def _fetch_age_seconds(root: str) -> Optional[float]:
        """
        How long ago this repository last heard from its remote.

        An ahead/behind count is only as fresh as the remote-tracking ref it compares against, so a
        caller reporting the count should be able to say how old it is. FETCH_HEAD is written by
        fetch and by pull. A clone that has done neither last heard from its remote when it was
        cloned, which its packed refs date; reading it as unknown let a clone never fetched pass a
        check that refuses one fetched yesterday. Git resolves both paths, because a worktree's
        ``.git`` is a file naming another directory.
        """
        for marker in ("FETCH_HEAD", "packed-refs"):
            path = GitSync._run(root, ["rev-parse", "--git-path", marker])
            if path is None:
                continue
            try:
                return time.time() - os.path.getmtime(os.path.join(root, path))
            except OSError:
                continue
        return None

    @staticmethod
    def _run(cwd: str, arguments) -> Optional[str]:
        try:
            process = subprocess.Popen(
                ["git"] + arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
            stdout, _ = process.communicate()
        except OSError:
            return None
        if process.returncode != 0:
            return None
        return stdout.decode(UTF_8).strip()

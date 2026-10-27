import os
from pathlib import Path

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util


class PlanError(ValueError):
    """A request that names no work. The command reports it rather than run an empty plan."""


class Planner:

    def __init__(self):
        super().__init__()

    @staticmethod
    def repo_containing(wd: str = None):
        """The innermost registered repository whose checkout contains `wd`.

        The wrapper runs the CLI from its own checkout and passes the operator's directory as
        CEDAR_CLI_CALLER_DIR. `wd` defaults to that directory, and a relative `wd` is resolved
        against it. A directory below a checkout's root, such as a Maven module, belongs to that
        checkout. A repository registered inside another, such as cedar-bridging-src, wins over
        its parent. Paths are compared without regard to case, as the macOS file system compares
        them.
        """
        caller = invocation_environment().get('CEDAR_CLI_CALLER_DIR') or os.getcwd()
        directory = (Path(caller) / Path(wd or '.').expanduser()).resolve()
        wanted = Path(str(directory).lower())
        matches = []
        for repo in GlobalContext.repos.get_list_all():
            root = Path(str(Path(Util.get_wd(repo)).resolve()).lower())
            if root == wanted or root in wanted.parents:
                matches.append((len(root.parts), repo))
        if not matches:
            raise PlanError(f"No registered repository contains {directory}. Run the command "
                            "inside a CEDAR checkout, or name one with --wd.")
        return max(matches, key=lambda match: match[0])[1]

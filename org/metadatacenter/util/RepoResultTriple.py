from rich.console import Console

console = Console()


class RepoResultTriple:
    def __init__(self, repo, out, err, returncode=None):
        self.repo = repo
        self.out = out
        self.err = err
        self.returncode = (1 if err else 0) if returncode is None else returncode

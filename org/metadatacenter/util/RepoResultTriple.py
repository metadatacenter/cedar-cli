from rich.console import Console

console = Console()


class RepoResultTriple:
    def __init__(self, repo, out, err):
        self.repo = repo
        self.out = out
        self.err = err

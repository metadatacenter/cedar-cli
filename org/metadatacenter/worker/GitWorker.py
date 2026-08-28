import subprocess
from pathlib import PurePosixPath

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.rule import Rule
from rich.style import Style
from rich.table import Table

from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.util.GlobalContext import GlobalContext, UTF_8
from org.metadatacenter.util.RepoResultTriple import RepoResultTriple
from org.metadatacenter.util.ResultTable import ResultTable
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()

GIT_STATUS_CHAR_LIMIT = 300


class GitWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def register_active_repo(triple, table, active_repos, suggestion):
        table.add_row(triple.repo.name, triple.out[0:GIT_STATUS_CHAR_LIMIT] + '...' if len(triple.out) > 0 else '', "[red]" + triple.err,
                      suggestion)
        active_repos.append(triple.repo)

    def render_status_table(self, result):
        active_repos = []
        table = Table("Repo", "Output", "Error", "Suggested", show_lines=True, title="Repos that require attention")
        cnt = 0
        for triple in result.results:
            if "our branch is behind" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Pull")
                cnt += 1
            elif "ntracked files" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Add, Commit, Push")
                cnt += 1
            elif "hanges not staged" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Add, Commit, Push")
                cnt += 1
            elif "hanges to be committed" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Commit, Push")
                cnt += 1
            elif "our branch is ahead of" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Push")
                cnt += 1
            elif len(triple.err) > 0:
                self.register_active_repo(triple, table, active_repos, "Handle error")
                cnt += 1
        if cnt > 0:
            table.caption = str(cnt) + " repos to act on"
            table.style = Style(color="red")
            console.print()
            console.print(table)
        else:
            console.print(Panel("Nothing to add, commit or push, all changes are published", style=Style(color="green")))
        return active_repos

    @staticmethod
    def execute_shell_on_all_repos_with_table(command_list,
                                              cwd_is_home=False,
                                              headers=None,
                                              show_lines=True,
                                              status_line="Processing",
                                              repo_list=None
                                              ):
        if headers is None:
            headers = ["Repo", "Output", "Error"]
        result = ResultTable(headers, show_lines)
        if repo_list is None:
            repo_list = GlobalContext.repos.get_list_top()
        with Progress() as progress:
            task = progress.add_task("[red]" + status_line + "...", total=len(repo_list))
            for repo in repo_list:
                commands_to_execute = [cmd.format(repo.name) for cmd in command_list]
                rule = Rule("[bold red]" + repo.name)
                progress.print(rule)
                out = ""
                err = ""
                exception = ""
                return_code = -1
                try:
                    cwd = Util.get_wd(repo) if cwd_is_home is False else Util.cedar_home
                    # print(commands_to_execute)
                    process = subprocess.Popen(commands_to_execute, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, cwd=cwd,
                                               executable=GlobalContext.get_shell())
                    stdout, stderr = process.communicate()
                    out = stdout.decode(UTF_8).strip()
                    err = stderr.decode(UTF_8).strip()
                    return_code = process.returncode
                except subprocess.CalledProcessError as e:
                    exception = str(e)
                except OSError as e:
                    exception = str(e)
                except:
                    exception = "Error in subprocess"

                out_data = out
                error_data = ""
                if return_code == 0:
                    out_data += "\n" + err
                else:
                    error_data += "\n" + err
                if exception != "":
                    error_data += "\n" + exception
                out_data = out_data.strip()
                error_data = error_data.strip()
                result.add_result(RepoResultTriple(repo, out_data, error_data))
                progress.print(out_data)
                if len(error_data) > 0:
                    progress.print(error_data)
                    progress.print(Panel(err, title="[bold yellow]Error", subtitle="[bold yellow]" + repo.name, style=Style(color="red")))
                progress.update(task, advance=1)
        result.print_table()
        return result

    def branch(self):
        self.execute_shell_on_all_repos_with_table(
            command_list=["echo $(git rev-parse --abbrev-ref HEAD)"],
            headers=["Repo", "Branch", "Error"],
            show_lines=False,
            status_line="Checking",
        )

    def pull(self):
        self.execute_shell_on_all_repos_with_table(
            command_list=["git pull"],
            status_line="Pulling",
        )

    def fetch(self):
        self.execute_shell_on_all_repos_with_table(
            command_list=["git fetch"],
            status_line="Fetching",
        )

    def status(self):
        result = self.execute_shell_on_all_repos_with_table(
            command_list=["git status"],
        )
        return self.render_status_table(result)

    def checkout(self, branch: str):
        self.execute_shell_on_all_repos_with_table(
            command_list=["git checkout " + branch],
            status_line="Checking out",
        )

    def clone_docker(self):
        self.execute_shell_on_all_repos_with_table(
            status_line="Cloning",
            repo_list=GlobalContext.repos.get_for_docker_list(),
            command_list=["git clone " + ReposFactory.git_base + "{0}"],
            cwd_is_home=True,
        )

    def clone_all(self):
        self.execute_shell_on_all_repos_with_table(
            status_line="Cloning",
            command_list=["git clone " + ReposFactory.git_base + "{0}"],
            cwd_is_home=True,
        )

    def next(self):
        active_repos = self.status()
        if len(active_repos) > 0:
            last_repo_path = Util.read_cedar_file('last_git_repo')
            found_idx = -1

            if last_repo_path is not None:
                for idx, repo in enumerate(active_repos):
                    if Util.get_wd(repo) == last_repo_path:
                        found_idx = idx

            found_idx += 1
            if found_idx >= len(active_repos):
                found_idx = 0
            next_repo = active_repos[found_idx]
            path = Util.get_wd(next_repo)
            console.print("Found repo with activity, changing current working directory to: " + path)
            Util.write_cedar_file(Util.LAST_GIT_FILE, path + "\n")
            Util.write_cedar_file(Util.NEXT_GIT_FILE, path + "\n")
        else:
            Util.delete_cedar_file(Util.LAST_GIT_FILE)
            Util.delete_cedar_file(Util.NEXT_GIT_FILE)

    def remote(self):
        self.execute_shell_on_all_repos_with_table(
            status_line="Checking remote",
            command_list=["git remote -v"],
        )

    def list_tag(self):
        self.execute_shell_on_all_repos_with_table(
            command_list=[
                "echo Local\n" +
                "git --no-pager branch --sort=-creatordate | head -4\n" +
                "echo Remote\n" +
                "git --no-pager ls-remote --tag --sort=-creatordate | head -4 | awk '{{ print \" \",$2}}'"
            ],
            status_line="Listing tags",
        )

    def list_branch(self):
        self.execute_shell_on_all_repos_with_table(
            command_list=[
                "echo Local\n" +
                "git --no-pager branch --sort=-creatordate | head -4\n" +
                "echo Remote\n" +
                "git --no-pager branch -r --sort=-creatordate | head -4"
            ],
            status_line="Listing branches",
        )

    def git_add_commit_push(self, comment: str, repo_name: str, paths: list[str]):
        repo = next((candidate for candidate in GlobalContext.repos.get_list_all()
                     if candidate.get_fqn() == repo_name), None)
        if repo is None:
            raise ValueError("Unknown repository: " + repo_name)

        explicit_paths = self._validate_explicit_paths(paths)
        cwd = Util.get_wd(repo)
        result = ResultTable(["Repo", "Output", "Error"], True)

        try:
            changed_paths = self._get_changed_paths(cwd)
            if not changed_paths:
                raise ValueError("Repository has no changes")
            outside_paths = sorted(path for path in changed_paths
                                   if not self._is_covered_by_explicit_path(path, explicit_paths))
            if outside_paths:
                raise ValueError("Refusing to stage because these changes are outside --path: "
                                 + ", ".join(outside_paths))

            output = []
            for command in (
                    ["git", "add", "--", *explicit_paths],
                    ["git", "commit", "-m", comment, "--", *explicit_paths],
                    ["git", "push"],
            ):
                completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
                if completed.stdout.strip():
                    output.append(completed.stdout.strip())
                if completed.returncode != 0:
                    error = completed.stderr.strip() or "Git command failed: " + " ".join(command[:2])
                    result.add_result(RepoResultTriple(repo, "\n".join(output), error))
                    result.print_table()
                    return result
                if completed.stderr.strip():
                    output.append(completed.stderr.strip())

            result.add_result(RepoResultTriple(repo, "\n".join(output), ""))
        except (OSError, ValueError) as e:
            result.add_result(RepoResultTriple(repo, "", str(e)))

        result.print_table()
        return result

    @staticmethod
    def _validate_explicit_paths(paths: list[str]) -> list[str]:
        if not paths:
            raise ValueError("At least one --path is required")

        explicit_paths = []
        for raw_path in paths:
            path = PurePosixPath(raw_path)
            if (not raw_path.strip() or path.is_absolute() or str(path) == "." or ".." in path.parts
                    or str(path) == ".git" or str(path).startswith(".git/") or raw_path.startswith(":")
                    or any(character in raw_path for character in "*?[")):
                raise ValueError("Path must be an explicit repository-relative file or directory: " + raw_path)
            normalized = str(path)
            if normalized not in explicit_paths:
                explicit_paths.append(normalized)
        return explicit_paths

    @staticmethod
    def _get_changed_paths(cwd: str) -> set[str]:
        commands = (
            ["git", "diff", "--name-only", "--no-renames", "-z"],
            ["git", "diff", "--cached", "--name-only", "--no-renames", "-z"],
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        )
        changed_paths = set()
        for command in commands:
            completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                raise ValueError(completed.stderr.strip() or "Unable to inspect repository changes")
            changed_paths.update(path for path in completed.stdout.split("\0") if path)
        return changed_paths

    @staticmethod
    def _is_covered_by_explicit_path(changed_path: str, explicit_paths: list[str]) -> bool:
        changed = PurePosixPath(changed_path)
        return any(changed == PurePosixPath(path) or PurePosixPath(path) in changed.parents
                   for path in explicit_paths)

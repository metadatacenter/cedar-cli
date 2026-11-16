import shlex
from pathlib import Path

from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import PlanError, Planner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

# The cedar-project profile that adds cedar-libraries to its reactor.
LIBRARIES_PROFILE = 'libraries'
# The artifact library's checks of the shared corpus in cedar-test-artifacts, run as its CI runs them.
# The first holds the corpus's committed fixtures to what the library's writers produce, the second
# writes every corpus artifact as JSON both directly and through YAML, and the third holds the record
# of the YAML keys the reader reads, which the TypeScript library compares its own with.
CORPUS_VERIFIERS = (
    'org.metadatacenter.artifacts.model.CorpusFixtureVerifier',
    'org.metadatacenter.artifacts.model.CorpusYamlTripVerifier',
    'org.metadatacenter.artifacts.model.reader.CorpusYamlKeyVerifier',
)


class BuildPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def parent(plan: Plan):
        plan.add_task(
            "Build parent",
            TaskType.BUILD,
            GlobalContext.repos.get_parent()
        )

    @staticmethod
    def libraries(plan: Plan):
        plan.add_task(
            "Build libraries",
            TaskType.BUILD,
            GlobalContext.repos.get_libraries()
        )

    @staticmethod
    def libraries_and_project(plan: Plan):
        """Build the libraries and the servers in one Maven reactor.

        cedar-project's `libraries` profile adds cedar-libraries to its modules, so Maven schedules
        both sets of modules together and a server's library chain starts once the core, config and
        model libraries are installed, rather than after the artifact library's tests.
        """
        project = GlobalContext.repos.get_project()
        pom = Path(Util.cedar_home) / project[0].get_fqn() / 'pom.xml'
        # Maven 3 only warns about a profile a POM lacks, and would then build the servers against
        # whatever libraries ~/.m2 holds, so an outdated checkout is refused here.
        if pom.is_file() and f'<id>{LIBRARIES_PROFILE}</id>' not in pom.read_text():
            raise PlanError(f"{pom} has no '{LIBRARIES_PROFILE}' profile, which builds the libraries in "
                            "its reactor. Update cedar-project.")
        plan.add_task("Build libraries and project", TaskType.BUILD, project,
                      parameters={'maven_profiles': [LIBRARIES_PROFILE]})

        def activate(node):
            if node.command_list:
                node.command_list = [command.replace('./mvnw clean install', f'./mvnw clean install -P{LIBRARIES_PROFILE}', 1)
                                     if command.startswith('./mvnw clean install') else command
                                     for command in node.command_list]
            for child in node.tasks:
                activate(child)
        activate(plan.tasks[-1])

    @staticmethod
    def corpus_verifiers(plan: Plan):
        """Run the artifact library's corpus verifiers against $CEDAR_HOME/cedar-test-artifacts.

        They are programs rather than tests, so the library's Maven build never runs them, and a
        writer change that leaves the shared corpus stale would otherwise pass the local build and
        fail the library's CI after the push. A missing corpus fails the task rather than skipping
        it, so a green build always means the verifiers ran.
        """
        from org.metadatacenter.model.PlanTask import PlanTask
        library = next(repo for repo in GlobalContext.repos.get_list_top() if repo.name == 'cedar-artifact-library')
        corpus = shlex.quote(str(Path(Util.cedar_home) / 'cedar-test-artifacts'))
        task = PlanTask("Verify the shared corpus", TaskType.SHELL, library)
        task.command_list = [
            f'test -d {corpus} || {{ echo "The shared corpus {corpus} is missing; '
            f'clone metadatacenter/cedar-test-artifacts there." >&2; exit 1; }}',
            *(f'./mvnw --batch-mode --no-transfer-progress test-compile exec:java '
              f'-Dexec.classpathScope=test -Dexec.mainClass={verifier} -Dexec.args={corpus}'
              for verifier in CORPUS_VERIFIERS),
        ]
        plan.add_task_as_task_no_expand(task)

    @staticmethod
    def project(plan: Plan):
        plan.add_task(
            "Build project",
            TaskType.BUILD,
            GlobalContext.repos.get_project()
        )

    @staticmethod
    def clients(plan: Plan):
        plan.add_task(
            "Build clients",
            TaskType.BUILD,
            GlobalContext.repos.get_clients()
        )

    @staticmethod
    def frontends(plan: Plan, *, verification: bool = True, reuse: bool = False):
        plan.add_task(
            "Build frontends",
            TaskType.BUILD,
            [repo for repo in GlobalContext.repos.get_frontends()
             if repo.name != 'cedar-component-demo'],
            parameters={"force_frontend_build": True},
        )
        if not verification:
            return
        BuildPlanner.verify_frontends(plan, reuse=reuse)

    @staticmethod
    def verify_frontends(plan: Plan, *, reuse: bool = False):
        # Full reactor completion includes the owning packages' verification gates.
        from org.metadatacenter.frontend_inventory import reactor_checks
        checks = reactor_checks()
        def verify(task):
            if getattr(task, 'parameters', {}).get('isolated_frontend_build'):
                if task.repo.name not in checks:
                    raise ValueError(f"Frontend {task.repo.name} has no verification inventory entry")
                check = checks[task.repo.name]
                task.command_list = check.commands
                if check.package_ready_after is not None:
                    if not task.repo.published_package_path:
                        raise ValueError(f"Frontend {task.repo.name} declares a package phase but publishes no package")
                    # Its consumers may start once these commands pass, while its checks run on.
                    task.parameters["package_ready_after"] = check.package_ready_after
                # A passing result recorded for exactly these inputs stands for running them again.
                task.parameters["reuse_passing_result"] = reuse
            for child in task.tasks:
                verify(child)
        verify(plan)

    @staticmethod
    def split_frontends(plan: Plan, server_payload: bool = False):
        plan.add_task(
            "Build split frontends",
            TaskType.BUILD,
            GlobalContext.repos.get_split_frontends(),
            parameters={"force_frontend_build": True, "server_frontend_payload": server_payload}
        )

    @staticmethod
    def server_frontends(plan: Plan, server_payload: bool = False):
        plan.add_task(
            "Build server frontends",
            TaskType.BUILD,
            GlobalContext.repos.get_server_frontends(),
            parameters={"force_frontend_build": True, "server_frontend_payload": server_payload}
        )

    @staticmethod
    def this(plan: Plan, wd: str = None):
        plan.add_task(
            "Build current repo",
            TaskType.BUILD,
            [Planner.repo_containing(wd)],
            parameters={"force_frontend_build": True},
        )

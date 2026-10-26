import time
import re
from contextlib import nullcontext
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.style import Style

from org.metadatacenter import npm_install, reactor, reactor_evidence, reactor_results, reactor_stages, reactor_tasks, task_cache
from org.metadatacenter.build_diagnostics import failure_diagnostics, COMMAND_LOG
from org.metadatacenter.build_scheduler import (
    package_ready,
    record_dropped_optional_dependencies,
    record_timing,
    worker_pool,
)
from org.metadatacenter.util.worker_pool import POOL_VARIABLE, runs_gate, wanted
from org.metadatacenter.util.InvocationContext import current_context
from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.taskexecutor.TaskExecutor import TaskExecutor
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError,
    materialize_frontend_workspace,
    executable_build_workspace,
    is_maven_command,
    is_test_bearing_maven_command,
    isolated_frontend_workspace,
    require_no_embedded_mongo_processes,
    require_no_frontend_runtime_collision,
    wait_for_no_embedded_mongo_processes,
)
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure
from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.ProcessRunner import run_shell
from org.metadatacenter.util.NodeBuildCheck import is_frontend_build, require_build_node

console = Console()

# The variables through which a frontend's tools learn how many workers they may start.
_WORKER_VARIABLES = ('CEDAR_TEST_WORKERS', 'NG_BUILD_MAX_WORKERS', 'VITEST_MAX_WORKERS')


class ShellTaskExecutor(TaskExecutor):

    def __init__(self):
        super().__init__()

    def execute(self, task: PlanTask, job_progress: Progress, dry_run: bool) -> int:
        super().display_header(task, job_progress, 'yellow', "Shell task executor #" + str(task.node_id))
        return self.execute_shell_command_list(task, job_progress, dry_run)

    def execute_shell_command_list(self, task: PlanTask, job_progress: Progress, dry_run: bool) -> int:
        repo = task.repo
        # commands_to_execute = [cmd.format(repo.name) for cmd in task.command_list]
        commands_to_execute = task.command_list
        cwd = Util.get_wd(repo)
        job_progress.print(Panel(
            "[green]" +
            " 📂️ Location  : " + cwd + "\n" +
            " 🏷️️  Repo type : " + repo.repo_type + "\n" +
            " 🖥️  Commands  :\n" + "\n".join(commands_to_execute),
            title="Execute shell command list #" + str(task.node_id),
            title_align="left"),
            style=Style(color="green"))
        if not dry_run:
            try:
                if is_frontend_build(task):
                    require_build_node(Path(cwd))
                parameter = getattr(task, "get_parameter", lambda _name: None)
                if parameter("isolated_frontend_build") is True:
                    identity = reactor_evidence.begin(cwd)
                    with isolated_frontend_workspace(Path(cwd), manifests_only=True) as (isolated_cwd, environment, collisions), \
                            failure_diagnostics(
                                environment.get('CEDAR_BUILD_WORKSPACE_ROOT', isolated_cwd),
                                Util.cedar_home, job_progress.print,
                                npm_logs=npm_install.log_directory(environment),
                            ) as outcome:
                        diagnostic_root = Path(environment.get('CEDAR_BUILD_WORKSPACE_ROOT', isolated_cwd))
                        environment['CEDAR_BUILD_DIAGNOSTIC_LOG'] = str(diagnostic_root / COMMAND_LOG)
                        if collisions:
                            processes = ", ".join(f"PID {pid}" for pid, _ in collisions)
                            job_progress.print(
                                f"Active frontend runtime(s) {processes} detected; "
                                "the build is isolated from their checkout and Angular cache."
                            )
                        # The reactor. Resolve this copy's CEDAR dependencies from the siblings
                        # already built in this walk, the way a Maven reactor resolves from ~/.m2,
                        # and turn npm ci into npm install because a rewritten manifest no longer
                        # matches the lock. Scheduled producers must succeed before resolution.
                        cedar_home = Util.cedar_home
                        resolved = reactor.resolve(isolated_cwd, cedar_home)
                        if resolved:
                            job_progress.print(
                                "Reactor: " + ", ".join(resolved), markup=False)
                            commands_to_execute = reactor.install_commands(commands_to_execute)
                        workers = current_context().settings.build_workers
                        environment.update({name: str(workers) for name in _WORKER_VARIABLES})
                        pool = worker_pool()
                        if pool is not None:
                            environment[POOL_VARIABLE] = str(pool.directory)
                        checks = reactor.prepare_checks(repo, isolated_cwd, cedar_home,
                                                        environment, commands_to_execute)
                        ready_after = parameter("package_ready_after")
                        result = None
                        if parameter("reuse_passing_result") is True and identity is not None:
                            result = self._reusable(repo, isolated_cwd, identity, commands_to_execute,
                                                    ready_after, checks, cedar_home, job_progress)
                        if result is not None and result[2] is not None:
                            key, _inputs, reused = result
                            started = time.monotonic()
                            reactor.reuse(repo, isolated_cwd, cedar_home, reused['package'])
                            reactor_evidence.record(cwd, isolated_cwd, identity, commands_to_execute,
                                                    checks, reused=reused)
                            record_timing(repo.name, f"reuse {key[:12]}", started, 0)
                            job_progress.print(
                                f"Reactor: inputs unchanged since the result that passed at "
                                f"{reused['passedAt']} ({key[:12]}); reusing it", markup=False)
                            outcome['exitCode'] = 0
                            return 0
                        materialize_frontend_workspace(Path(cwd), Path(isolated_cwd))
                        reactor.resolve(isolated_cwd, cedar_home)
                        checks = reactor.prepare_checks(repo, isolated_cwd, cedar_home,
                                                        environment, commands_to_execute)
                        reactor_stages.configure(environment, repo, result, cedar_home)
                        task_config = reactor_tasks.configure(environment, repo, result, cedar_home, cwd, isolated_cwd)
                        code = self._execute_packaged_commands(
                            task, repo, commands_to_execute, isolated_cwd, job_progress,
                            environment, ready_after, cedar_home,
                        )
                        # A concurrent checkout edit invalidates all newly recorded stage evidence,
                        # including successful stages of an otherwise failed build.
                        if task_config and reactor_evidence.source_identity(cwd) == identity:
                            try:
                                task_cache.publish(task_config)
                            except OSError as error:
                                job_progress.print(f"Task cache: could not retain completed stages: {error}", markup=False)
                        if code == 0:
                            evidence = reactor_evidence.record(cwd, isolated_cwd, identity, commands_to_execute, checks)
                            stored = reactor.publish(repo, isolated_cwd, cedar_home, environment)
                            if stored:
                                job_progress.print(f"Reactor: stored {stored}", markup=False)
                            if result is not None and not (Path(isolated_cwd) / '.cedar-cache-unstable').exists():
                                reactor_results.save(cedar_home, result[0], result[1],
                                                     reactor.selected_digest(repo, cedar_home), evidence)
                        outcome['exitCode'] = code
                        return code
                if parameter("in_place_frontend_build") is True:
                    require_no_frontend_runtime_collision(Path(cwd))
                    with executable_build_workspace() as (_, environment):
                        return self._execute_commands(
                            task, repo, commands_to_execute, cwd, job_progress, environment)
                return self._execute_commands(
                    task, repo, commands_to_execute, cwd, job_progress, None,
                )
            except reactor.ReactorError as error:
                job_progress.print(f"Reactor failed: {error}", markup=False)
                return 1
            except BuildSafetyError as error:
                job_progress.print(f"Build safety check failed: {error}", markup=False)
                return 1
        else:
            time.sleep(0.1)
        return 0

    @staticmethod
    def _reusable(repo, build_root, identity, commands, ready_after, checks, cedar_home, job_progress):
        """This task's fingerprint, its inputs, and the passing result recorded for them, if any.

        A fingerprint that cannot be computed reuses nothing and records nothing, so the task
        runs as it would without reuse.
        """
        try:
            key, inputs = reactor_results.fingerprint(
                repo, build_root, identity, commands, ready_after, checks, cedar_home)
        except reactor_results.ResultError as error:
            job_progress.print(f"Reactor: {error}; running the checks", markup=False)
            return None
        return key, inputs, reactor_results.lookup(cedar_home, key, repo)

    def _execute_packaged_commands(self, task, repo, commands, build_root, job_progress, environment,
                                   ready_after, cedar_home):
        """Run a reactor build's commands, offering its package once the first `ready_after` pass.

        A surface with a package phase stores its package provisionally at that point and tells the
        scheduler, so consumers can start while this build's own checks run on. Without one, the
        commands run as a single list.
        """
        if not ready_after:
            return self._execute_commands(task, repo, commands, str(build_root), job_progress, environment)
        code = self._execute_commands(
            task, repo, commands[:ready_after], str(build_root), job_progress, environment)
        if code == 0:
            stored = reactor.publish(repo, build_root, cedar_home, environment, provisional=True)
            if stored:
                job_progress.print(
                    f"Reactor: packaged {stored} for this build's consumers; its checks continue",
                    markup=False)
                package_ready()
        elif GlobalContext.fail_on_error():
            return code
        rest = self._execute_commands(
            task, repo, commands[ready_after:], str(build_root), job_progress, environment)
        return code or rest

    def _execute_commands(self, task, repo, commands, cwd, job_progress, environment):
        first_failure = 0
        # Only an isolated build tells npm where to write its debug logs.
        npm_logs = npm_install.log_directory(environment)
        cache = reactor_tasks.load(environment)
        for command in commands:
            cache_started = time.monotonic()
            cached = task_cache.reuse(cache, command) if cache else None
            if cached:
                job_progress.print(f"Task cache: reuse {command} ({cached['key'][:12]}, passed {cached['passedAt']})", markup=False)
                record_timing(repo.name, f"reuse task {command}", cache_started, 0)
                continue
            output = []
            guarded_maven = is_test_bearing_maven_command(command)
            if guarded_maven:
                require_no_embedded_mongo_processes(
                    f"test-bearing Maven task for {repo.name}")
            with self._worker_lease(command, cwd, environment) as granted:
                command_environment = environment
                if granted is not None:
                    command_environment = {**environment,
                                           **{name: str(granted) for name in _WORKER_VARIABLES}}
                    want = wanted(command, int(environment['CEDAR_TEST_WORKERS']))
                    if granted < want:
                        job_progress.print(
                            f"Workers: {granted} of {want}, the rest being in use elsewhere in the build",
                            markup=False)
                workspace = (executable_build_workspace(command_environment, java=True)
                             if is_maven_command(command) else nullcontext((None, command_environment)))
                with workspace as (_, child_environment):
                    if npm_logs and npm_install.is_install(command):
                        return_code = self._execute_npm_install(
                            task, repo, command, cwd, job_progress, child_environment, npm_logs)
                    else:
                        output, return_code = self.execute_shell_command(
                            task, repo, command, cwd, job_progress, environment=child_environment,
                        )
                    if guarded_maven:
                        wait_for_no_embedded_mongo_processes(
                            f"completion of test-bearing Maven task for {repo.name}")
            if return_code == 0 and cache:
                try:
                    task_cache.save(cache, command, log='\n'.join(output))
                except OSError as error:
                    job_progress.print(f"Task cache: could not record {command}: {error}", markup=False)
            if return_code != 0:
                if first_failure == 0:
                    first_failure = return_code
                if GlobalContext.fail_on_error():
                    return return_code
        return first_failure

    @staticmethod
    def _worker_lease(command, cwd, environment):
        """Hold the workers a command takes from the build's shared pool, if it takes any.

        Only an isolated frontend build has a pool. A gate script takes each of its stages' workers
        itself, so the command that starts it holds none. Any other npm script asks for the
        repository's whole budget and runs with what it is granted; an install or a download holds one.
        """
        pool = worker_pool()
        if pool is None or not environment or POOL_VARIABLE not in environment or runs_gate(command, cwd):
            return nullcontext(None)
        return pool.lease(wanted(command, int(environment['CEDAR_TEST_WORKERS'])))

    def _execute_npm_install(self, task, repo, command, cwd, job_progress, environment, npm_logs):
        """Install once more if npm dropped an optional dependency, and fail if it does so again.

        npm_install explains why such an install cannot be trusted whatever its exit code. A second
        run needs no cleanup. An isolated build starts without node_modules, a failed install
        removes what it added, and running a successful one again adds just the package it dropped.
        """
        for attempt in (1, 2):
            before = npm_install.debug_logs(npm_logs)
            _, return_code = self.execute_shell_command(
                task, repo, command, cwd, job_progress, environment=environment)
            dropped = npm_install.dropped_optional_dependencies(npm_logs, before)
            if not dropped:
                return return_code
            record_dropped_optional_dependencies(repo.name, command, dropped)
            self._note(job_progress, environment, npm_install.dropped_message(dropped, attempt == 2))
        return return_code or 1

    @staticmethod
    def _note(job_progress, environment, message):
        job_progress.print(message, markup=False)
        log_path = (environment or {}).get('CEDAR_BUILD_DIAGNOSTIC_LOG')
        if log_path:
            with Path(log_path).open('a', encoding='utf-8') as log:
                log.write(message + '\n')

    def execute_shell_command(
        self, task: PlanTask, repo, command, cwd, job_progress: Progress, environment=None,
    ):
        job_progress.print(Panel(
            "[bright_cyan]" +
            " 📂️ Location  : " + cwd + "\n" +
            " 🏷️️  Repo type : " + repo.repo_type + "\n" +
            " 🖥️  Command   : " + command,
            title="Shell subprocess #" + str(task.node_id),
            title_align="left"),
            style=Style(color="bright_cyan"))

        stages = []
        allocations = {}
        def report(line):
            allocation = re.fullmatch(r"\[gate\] allocation ([\w-]+): (\d+) workers, ([\d.]+)s queued", line)
            if allocation:
                allocations[allocation[1]] = {"workers": int(allocation[2]), "queuedSeconds": float(allocation[3])}
            match = re.fullmatch(r"\[gate\] ([\w-]+): exit (-?\d+), ([\d.]+)s", line)
            if match:
                stages.append({"stage": match[1], "exitCode": int(match[2]), "seconds": float(match[3]),
                               **allocations.get(match[1], {})})
            reused = re.fullmatch(r"\[gate\] reuse ([\w-]+) \(([^,]+), passed (.+)\)", line)
            if reused:
                stages.append({"stage": reused[1], "exitCode": 0, "seconds": 0,
                               "reused": reused[2], "passedAt": reused[3]})
            if ((environment or {}).get('CEDAR_BUILD_WORKSPACE_ROOT')
                    and re.search(r'\b[1-9]\d* flaky\b', re.sub(r'\x1b\[[0-9;]*m', '', line))):
                (Path(cwd) / '.cedar-cache-unstable').touch()
            job_progress.print(line, markup=False)
            job_progress.update(1, advance=1)

        started = time.monotonic()
        log_path = (environment or {}).get('CEDAR_BUILD_DIAGNOSTIC_LOG')
        with (Path(log_path).open('a', encoding='utf-8') if log_path else nullcontext()) as log:
            if log:
                log.write(f'\nCommand: {command}\n')
                log.flush()
            def logged_report(line):
                if log:
                    log.write(line + '\n')
                    log.flush()
                report(line)
            stdout_parts = run_shell(
                command, shell=GlobalContext.get_shell(), cwd=cwd,
                env=environment, on_line=logged_report,
            )
        return_code = stdout_parts.returncode
        record_timing(repo.name, command, started, return_code, stages)
        description = describe_subprocess_failure(return_code)
        if return_code < 0 and not stdout_parts:
            description += "; the process produced no diagnostic output of its own"
        color = "green" if return_code == 0 else "red"
        msg = f"[{color}]Processing {repo.name} done: {description}."
        job_progress.print(Panel(msg, style=Style(color="green"), subtitle="Shell subprocess"))
        return stdout_parts, return_code

from typing import Annotated

import typer
from rich.console import Console

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.ReleaseCleanupPlanner import ReleaseCleanupPlanner
from org.metadatacenter.planner.ReleaseCommitPlanner import ReleaseCommitPlanner
from org.metadatacenter.planner.ReleasePreparePlanner import ReleasePreparePlanner
from org.metadatacenter.planner.ReleaseRollbackPlanner import ReleaseRollbackPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

app = typer.Typer(no_args_is_help=True)

console = Console()

plan_executor = PlanExecutor()


@app.command("prepare")
def prepare(dry_run: bool = typer.Option(False, help="Dry run")):
    Util.check_release_variables()
    GlobalContext.mark_global_task_type(TaskType.RELEASE_PREPARE)
    plan = Plan("Prepare release all")
    ReleasePreparePlanner.prepare(plan)
    plan_executor.execute(plan, dry_run)


@app.command("rollback", no_args_is_help=True)
def rollback(branch: Annotated[str, typer.Option(help="Branch to delete")],
             tag: Annotated[str, typer.Option(help="Tag to delete")],
             dry_run: bool = typer.Option(False, help="Dry run")):
    Util.mark_rollback_branch(branch)
    Util.mark_rollback_tag(tag)
    GlobalContext.mark_global_task_type(TaskType.RELEASE_ROLLBACK)
    plan = Plan("Prepare rollback release all")
    ReleaseRollbackPlanner.rollback(plan)
    plan_executor.execute(plan, dry_run)


@app.command("commit")
def commit(pre_branch: str = typer.Option(None, help="Branch to merge into main"),
           post_branch: str = typer.Option(None, help="Branch to merge into develop"),
           tag: str = typer.Option(None, help="Tag to use"),
           release_version: str = typer.Option(None, help="Release version"),
           next_dev_version: str = typer.Option(None, help="Next dev version"),
           dry_run: bool = typer.Option(False, help="Dry run")):
    pre_branch_old, post_branch_old, tag_old, release_version_old, next_dev_version_old = Util.check_release_commit_variables()
    if pre_branch is None or post_branch is None or tag is None or release_version is None or next_dev_version is None:
        command = 'cedarcli release commit'
        command += ' --pre-branch=' + pre_branch_old
        command += ' --post-branch=' + post_branch_old
        command += ' --tag=' + tag_old
        command += ' --release-version=' + release_version_old
        command += ' --next-dev-version=' + next_dev_version_old
        console.print("Previous release prepare data found. Use this command to commit the release:\n")
        console.print(command + "\n")
    else:
        console.print("Commiting previously prepared release")
        GlobalContext.mark_global_task_type(TaskType.RELEASE_COMMIT)
        plan = Plan("Commit prepared release all")
        params = {
            'pre_branch': pre_branch,
            'post_branch': post_branch,
            'tag': tag,
            'release_version': release_version,
            'next_dev_version': next_dev_version
        }
        ReleaseCommitPlanner.commit(plan, params)
        plan_executor.execute(plan, dry_run)


@app.command("cleanup")
def cleanup(pre_branch: str = typer.Option(None, help="Pre-branch to delete"),
            post_branch: str = typer.Option(None, help="Post-branch to delete"),
            dry_run: bool = typer.Option(False, help="Dry run")):
    pre_branch_old, post_branch_old, _, _, _ = Util.check_release_commit_variables()
    if pre_branch is None or post_branch is None:
        command = 'cedarcli release cleanup'
        command += ' --pre-branch=' + pre_branch_old
        command += ' --post-branch=' + post_branch_old
        console.print("Previous release data found. Use this command to clean up the release:\n")
        console.print(command + "\n")
    else:
        console.print("Cleaning up previous release")
        Util.mark_pre_branch(pre_branch)
        Util.mark_post_branch(post_branch)
        GlobalContext.mark_global_task_type(TaskType.RELEASE_CLEANUP)
        plan = Plan("Clean up release all")
        params = {
            'pre_branch': pre_branch,
            'post_branch': post_branch,
        }
        ReleaseCleanupPlanner.cleanup(plan, params)
        plan_executor.execute(plan, dry_run)

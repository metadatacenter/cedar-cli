import subprocess

from rich.console import Console

from org.metadatacenter.util.BuildTrain import BuildTrain


console = Console()


class BuildTrainWorker:
    WORKFLOW = 'build-train.yml'
    REPOSITORY = 'metadatacenter/cedar-development'

    @classmethod
    def dispatch(cls, resume=None):
        try:
            selected = BuildTrain.validate(resume) if resume else BuildTrain.allocate()
        except (OSError, ValueError) as error:
            console.print(f'[red]{error}[/red]')
            return 1

        command = [
            'gh', 'workflow', 'run', cls.WORKFLOW,
            '--repo', cls.REPOSITORY,
            # workflow_dispatch definitions are resolved from the default branch. The workflow
            # itself still captures the exact heads of every develop branch as train inputs.
            '--ref', 'main',
            '--field', f'version={selected}',
            '--field', f'resume={"true" if resume else "false"}',
        ]
        try:
            result = subprocess.run(command, text=True, check=False)
        except OSError as error:
            console.print(f'[red]Could not run GitHub CLI: {error}[/red]')
            return 1
        if result.returncode:
            return result.returncode
        console.print(f'[green]Dispatched build train {selected}.[/green]')
        console.print(
            f'Follow it with: gh run list --repo {cls.REPOSITORY} '
            f'--workflow {cls.WORKFLOW}'
        )
        return 0

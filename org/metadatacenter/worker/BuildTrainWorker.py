import subprocess

from rich.console import Console

from org.metadatacenter.util.BuildTrain import BuildTrain


console = Console()


class BuildTrainWorker:
    WORKFLOW = 'build-train.yml'
    REPOSITORY = 'metadatacenter/cedar-development'

    @classmethod
    def status(cls, version):
        try:
            selected = BuildTrain.validate(version)
        except ValueError as error:
            console.print(f'[red]{error}[/red]')
            return 1
        stages = (
            ('source', f'trains/{selected}.json'),
            ('Maven', f'completed/{selected}.json'),
            ('npm plan', f'npm/trains/{selected}.json'),
            ('npm', f'npm/completed/{selected}.json'),
            ('Docker plan', f'docker/trains/{selected}.json'),
            ('Docker', f'docker/completed/{selected}.json'),
        )
        console.print(f'Build train {selected}')
        for label, path in stages:
            try:
                BuildTrain._read(path)
                state = '[green]recorded[/green]'
            except ValueError as error:
                state = (
                    '[yellow]pending[/yellow]'
                    if 'does not exist' in str(error)
                    else '[red]unavailable[/red]'
                )
            console.print(f'  {label}: {state}')
        console.print(f'Manifests: {BuildTrain.STATE_BASE_URL}/')
        return 0

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
            # The workflow file is present on the default branch, while develop selects the exact
            # controller revision that is also captured as a source input by the train itself.
            '--ref', 'develop',
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

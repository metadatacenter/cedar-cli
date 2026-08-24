import sys

import typer
from rich.console import Console

from org.metadatacenter.util.DockerImages import DockerImages
from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.worker.DockerWorker import DockerWorker

console = Console()

TARGET_HELP = (
    "all, a group (" + " | ".join(DockerImages.GROUPS) + "), or an image: "
    "artifact-server, openview-frontend, nginx, kibana, java ..."
)


def build(
        target: str = typer.Argument(..., help=TARGET_HELP),
        no_deps: bool = typer.Option(False, "--no-deps",
                                     help="Do not build the CEDAR base images this target is built FROM."),
        local: bool = typer.Option(False, "--local",
                                   help="Use the development tag and checked-out JARs where the image carries one."),
        train: str = typer.Option(
            None,
            "--train",
            help="Use this completed immutable train instead of the current completed train.",
        ),
):
    """Build Docker images. Bases are built first, so a build cannot use a stale one."""
    try:
        selected = DockerImages.resolve(target)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    images = selected if no_deps else DockerImages.with_dependencies(selected)

    # Say what a short name resolved to: artifact and artifacts name different images.
    if len(selected) == 1 and selected[0] != target:
        console.print(f"{target} → {selected[0]}")
    added = [i for i in images if i not in selected]
    if added:
        console.print(f"Building {len(added)} base image(s) first: {', '.join(added)}")

    if local and train:
        console.print("[red]Use --local or --train, not both.[/red]")
        raise typer.Exit(code=1)

    if local:
        selected_train = None
    else:
        try:
            selected_train = BuildTrain.resolve(train)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(code=1)
        console.print(f"Using completed build train {selected_train}")

    sys.exit(DockerWorker.build_images(images, local=local, train=selected_train))

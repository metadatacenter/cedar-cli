import sys

import typer
from rich.console import Console

from org.metadatacenter.util.DockerImages import DockerImages
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
                                   help="Build against the jar in the checkout instead of the one published to Nexus."),
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

    if local:
        missing = [i for i in images if not DockerImages.stageable(i)]
        if len(missing) == len(images):
            console.print("[red]--local: none of these images carry a jar, so there is nothing to stage[/red]")
            raise typer.Exit(code=1)

    sys.exit(DockerWorker.build_images(images, local=local))

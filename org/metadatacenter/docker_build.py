import sys
from typing import Optional

import typer
from rich.console import Console

from org.metadatacenter.util.DockerImages import DockerImages
from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.worker.DockerWorker import (
    DockerWorker,
    FRONTEND_COMPOSE_SERVICES,
    MICROSERVICE_COMPOSE_SERVICES,
)

console = Console()

TARGET_HELP = (
    "A deployment target (all | infra | microservices | frontends | admin | keycloak | kk), "
    "a component family (frontend | microservice), or an image name."
)


def normalize_target(target: str, component: Optional[str] = None) -> str:
    """Map the shared Docker target grammar to a DockerImages target or image."""
    if component is None:
        if target in ("keycloak", "kk"):
            return "cedar-infra-keycloak"
        return target

    component_groups = {
        "frontend": (FRONTEND_COMPOSE_SERVICES, "frontends"),
        "microservice": (MICROSERVICE_COMPOSE_SERVICES, "microservices"),
    }
    if target not in component_groups:
        raise ValueError(f'"{target}" does not take a component name')

    services, all_target = component_groups[target]
    if component == "all":
        return all_target
    if component not in services:
        valid = ", ".join(("all", *services))
        raise ValueError(f'unknown {target} "{component}"; choose one of: {valid}')
    return f"cedar-{services[component]}"


def build(
        target: str = typer.Argument(..., help=TARGET_HELP),
        component: Optional[str] = typer.Argument(
            None,
            help="Frontend or microservice name when TARGET is frontend or microservice.",
        ),
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
        resolved_target = normalize_target(target, component)
        selected = DockerImages.resolve(resolved_target)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    images = selected if no_deps else DockerImages.with_dependencies(selected)

    # Say what a short name resolved to: artifact and artifacts name different images.
    requested_target = " ".join(value for value in (target, component) if value)
    if len(selected) == 1 and selected[0] != requested_target:
        console.print(f"{requested_target} → {selected[0]}")
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

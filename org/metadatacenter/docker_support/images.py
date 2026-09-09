"""CEDAR docker images."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.BuildTrain import DockerTrain
from org.metadatacenter.util.DockerImages import DockerImages
from org.metadatacenter.worker.Worker import Worker
import json
import os
import re
from org.metadatacenter.docker_support import engine as _engine_component
from org.metadatacenter.docker_support import output as _output_component
from org.metadatacenter.docker_support import policy as _policy_component


def _train_image_names(stack, services=()):
    selected = tuple(services) if services else _policy_component.STATUS_SERVICE_ORDER[stack]
    names = []
    for service in selected:
        if stack == 'infrastructure':
            names.append(f'cedar-infra-{service}')
        elif stack == 'microservices':
            names.append(f'cedar-{service}')
        elif stack == 'frontends':
            names.append(f'cedar-{service}')
    return names


def _inspect_image(reference):
    result = _engine_component._docker_command(['image', 'inspect', reference])
    if result.returncode != 0:
        return None, result.stderr.strip() or f'{reference} is not present locally'
    try:
        inspected = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return None, f'Could not parse docker image inspect for {reference}: {error}'
    if not isinstance(inspected, list) or len(inspected) != 1:
        return None, f'docker image inspect returned an invalid result for {reference}'
    return inspected[0], None


def _prepare_train_images(train, stack_names, pull, environment, services_by_stack=None):
    services_by_stack = services_by_stack or {}
    try:
        completion = DockerTrain.completion(train)
    except ValueError as error:
        _output_component.console.print(f'[red]❌ {error}[/red]')
        return False
    inventory = {entry['image']: entry for entry in completion['images']}
    required = []
    for stack in stack_names:
        required.extend(_train_image_names(
            stack, services_by_stack.get(stack, ())))

    for image in required:
        record = inventory.get(image)
        if record is None:
            _output_component.console.print(
                f'[red]❌ Docker completion record for {train} does not contain {image}.[/red]'
            )
            return False
        expected_reference = DockerImages.reference(image, train, environment)
        if record['reference'] != expected_reference:
            _output_component.console.print(
                f'[red]❌ {image} is configured as {expected_reference}, but the completed '
                f'train records {record["reference"]}.[/red]'
            )
            return False

        inspected, inspect_error = _inspect_image(expected_reference)
        should_pull = pull == 'always' or (pull == 'missing' and inspected is None)
        if should_pull:
            result = _engine_component._docker_command(['pull', expected_reference])
            if result.returncode != 0:
                _output_component.console.print(
                    f'[red]❌ Could not pull {expected_reference}: '
                    f'{result.stderr.strip() or result.stdout.strip()}[/red]'
                )
                return False
            inspected, inspect_error = _inspect_image(expected_reference)
        if inspected is None:
            _output_component.console.print(f'[red]❌ {inspect_error}[/red]')
            return False

        repository = expected_reference.rsplit(':', 1)[0]
        expected_digest = f'{repository}@{record["digest"]}'
        if expected_digest not in inspected.get('RepoDigests', []):
            _output_component.console.print(
                f'[red]❌ {expected_reference} is not the completed train image '
                f'{expected_digest}. Refusing to start it.[/red]'
            )
            return False
    _output_component.console.print(
        f'[green]Verified {len(required)} local image digests for Docker train {train}.[/green]'
    )
    return True


def _prepare_microservice_volumes(reference):
    return _prepare_writable_volumes(
        reference, _policy_component.MICROSERVICE_WRITABLE_VOLUMES, '10001:10001')


def _prepare_frontend_volumes(reference):
    return _prepare_writable_volumes(reference, _policy_component.FRONTEND_LOG_VOLUMES, '101:101')


def _prepare_writable_volumes(reference, volumes, owner):
    sentinel = f".cedar-owner-{owner.split(':', 1)[0]}"
    for volume in volumes:
        create = _engine_component._docker_command(['volume', 'create', volume])
        if create.returncode != 0:
            _output_component.console.print(
                f'[red]❌ Could not create or inspect volume {volume}: '
                f'{create.stderr.strip()}[/red]'
            )
            return False
        result = _engine_component._docker_command([
            'run', '--rm', '--pull=never', '--user', '0:0', '--entrypoint', '/bin/sh',
            '--volume', f'{volume}:/volume', reference,
            '-c',
            'owner=$(stat -c %u:%g /volume); '
            f'if [ "$owner" != "{owner}" ] || '
            f'[ ! -e /volume/{sentinel} ]; then '
            f'chown -R {owner} /volume && '
            f'touch /volume/{sentinel} && '
            f'chown {owner} /volume/{sentinel}; fi',
        ])
        if result.returncode != 0:
            _output_component.console.print(
                f'[red]❌ Could not prepare volume {volume} for the CEDAR service user: '
                f'{result.stderr.strip() or result.stdout.strip()}[/red]'
            )
            return False
    return True


def build_images(images, local=False, train=None):
    """Build the given images in order. Returns a process exit code.

        With local=True the jar is staged from the checkout before each image that carries one, and
        cleared afterwards: a staged jar is an input to one build, not a mode the tree stays in.
        Staging is strict, so a target whose jar has not been built fails rather than quietly
        falling back to the published one.
        """
    from org.metadatacenter.util.DockerImages import DockerImages

    try:
        environment = invocation_environment().copy()
        if train:
            environment['CEDAR_TRAIN_VERSION'] = train
        _, version, prefix = DockerImages.manifest(environment)
        base_prefix = DockerImages.base_image_prefix(environment)
    except ValueError as error:
        _output_component.console.print(f'[red]Build configuration is invalid: {error}[/red]')
        return 1
    build_home = DockerImages.build_home()

    # The locked server versions travel from the manifest into every build as build arguments.
    # Passing them to all images rather than working out which image wants which is deliberate:
    # Docker ignores a build argument a Dockerfile does not declare, and the alternative is a
    # second place recording which image installs which server.
    server_versions = DockerImages.server_versions(environment)
    if train:
        server_versions['CEDAR_MAVEN_VERSION'] = train
    build_args = ' '.join([
        f'--build-arg CEDAR_IMAGE_PREFIX="{base_prefix}"',
        f'--build-arg CEDAR_DOCKER_VERSION="{version}"',
    ] + [
        f'--build-arg {name}="{value}"' for name, value in sorted(server_versions.items())
    ])

    source_revision = DockerImages.source_revision()
    source_manifest = environment.get('CEDAR_TRAIN_MANIFEST_SHA256')
    if source_manifest and not re.fullmatch(r'[0-9a-f]{64}', source_manifest):
        _output_component.console.print('[red]CEDAR_TRAIN_MANIFEST_SHA256 must be a lowercase SHA-256 digest.[/red]')
        return 1
    frontend_manifest = environment.get('CEDAR_FRONTEND_MANIFEST_SHA256')
    if frontend_manifest and not re.fullmatch(r'[0-9a-f]{64}', frontend_manifest):
        _output_component.console.print('[red]CEDAR_FRONTEND_MANIFEST_SHA256 must be a lowercase SHA-256 digest.[/red]')
        return 1
    steps = []
    for image in images:
        stage = local and DockerImages.stageable(image)
        reference = DockerImages.reference(image, version, environment)
        labels = [
            f'--label org.opencontainers.image.source="https://github.com/metadatacenter/cedar-docker-build"',
            f'--label org.opencontainers.image.version="{version}"',
            f'--label org.metadatacenter.cedar.image="{image}"',
        ]
        if train:
            labels.append(f'--label org.metadatacenter.cedar.train="{train}"')
        if source_revision:
            labels.append(f'--label org.opencontainers.image.revision="{source_revision}"')
        if source_manifest:
            labels.append(
                '--label org.metadatacenter.cedar.source-manifest-sha256='
                f'"{source_manifest}"'
            )
        if frontend_manifest:
            labels.append(
                '--label org.metadatacenter.cedar.frontend-manifest-sha256='
                f'"{frontend_manifest}"'
            )
        steps.append(f"""
echo "==> {image}"
{f'"{build_home}/bin/stage-local-jar.sh" {image} || exit 1' if stage else ''}
docker build {build_args} {' '.join(labels)} -t "{reference}" "{build_home}/{image}"
rc=$?
{f'rm -f "{build_home}/{image}/local/"*.jar' if stage else ''}
if [ $rc -ne 0 ]; then
echo "Build failed: {image}"
exit $rc
fi
""")

    out = Worker.execute_generic_shell_commands(
        ["set -o pipefail\n" + "\n".join(steps) + "\necho 'All requested images built.'"],
        title=f"Building {len(images)} CEDAR image(s) at {version}",
    )
    if out.returncode != 0:
        return out.returncode
    return 0 if any("All requested images built." in line for line in out) else 1

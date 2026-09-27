"""Read-only release prerequisites that do not require train artifacts yet."""
import json
from pathlib import Path
import subprocess
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.planning import ReleasePlanner
from org.metadatacenter.release_support.policy import (
    NEXT_VERSION_RE, _stable_version_key, _validate_stable_version,
)
from org.metadatacenter.release_support.preflight import ReleasePreflight


def validate_intent(version, next_version, cee_version, train=None):
    if not any((version, next_version, cee_version)):
        return None
    if not all((version, next_version, cee_version)):
        raise ValueError('Release-intended trains require --release-version, --next-version and --cee-version together')
    try:
        _validate_stable_version(version, 'release version')
        _validate_stable_version(cee_version, 'CEE version')
        if not NEXT_VERSION_RE.fullmatch(next_version):
            raise ReleaseError('next version must be MAJOR.MINOR.PATCH-SNAPSHOT')
        if _stable_version_key(next_version.removesuffix('-SNAPSHOT')) <= _stable_version_key(version):
            raise ReleaseError('next development version must be newer than the release')
        if train and train.split('-dev.', 1)[0] != version:
            raise ReleaseError(f'train {train} does not target release {version}')
    except ReleaseError as error:
        raise ValueError(str(error)) from error
    return dict(releaseVersion=version, nextDevelopmentVersion=next_version, ceeVersion=cee_version)


def preflight(intent, *, source=None, environment=None, runner=None, preflight_factory=ReleasePreflight):
    environment = dict(invocation_environment() if environment is None else environment)
    runner = runner or subprocess.run
    home = Path(environment['CEDAR_HOME'])
    config = json.loads((home / 'cedar-development/ops/build-train.json').read_text())
    revisions = dict((source or {}).get('repositories', {}))
    if not revisions:
        for repository in config['repositories']:
            result = runner(['git', '-C', str(home / repository), 'rev-parse', 'HEAD'],
                            text=True, capture_output=True, check=False, env=environment)
            if result.returncode:
                raise ValueError(f'Cannot capture release preflight source: {repository}')
            revisions[repository] = result.stdout.strip()
    try:
        release, maven = ReleasePlanner._release_repositories(config, {'repositories': revisions})
        manifest = {**intent, 'sourceRepositories': revisions, 'releaseRepositories': release,
                    'mavenRepositories': maven, 'sourceVersion': intent['releaseVersion'] + '-SNAPSHOT',
                    'mavenPhases': ReleasePlanner._maven_phases(config, maven),
                    'publicationPlan': ReleasePlanner._publication_plan(config, release)}
        checker = preflight_factory(manifest, environment=environment)
        checks = ('check_no_release_in_progress', 'check_toolchain', 'check_profile',
                  'check_git_identity', 'check_nexus_authorization', 'check_npm_authorization',
                  'check_npm_configuration', 'check_push_permission', 'check_target_version_unused',
                  'check_target_artifacts_unused', 'check_source_contract', 'check_generated_version_files')
        findings = [finding for name in checks for finding in getattr(checker, name)()]
    except (ReleaseError, OSError, KeyError) as error:
        raise ValueError(f'Release prerequisite check failed: {error}') from error
    failures = [f'{f.check}: {f.message}' for f in findings if f.fatal]
    if failures:
        raise ValueError('Release prerequisites failed before train dispatch:\n' + '\n'.join(failures))
    return findings

from org.metadatacenter.util.InvocationContext import invocation_environment
import os
import re
from pathlib import Path

from rich.console import Console

from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.ArtifactServiceKey import CURRENT, manage_artifact_key
from org.metadatacenter.util.ModeManager import ModeManager
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.model.CedarProfile import CedarProfile
from org.metadatacenter.worker.Worker import Worker

console = Console()


class ProdError(ValueError):
    pass


class ProdWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def provision_artifact_key():
        if ModeManager.current() is not CedarMode.NATIVE or ModeManager.current_profile() is not CedarProfile.SERVER:
            raise ProdError('Run this command on the production application host in native mode with the server profile. '
                            'For development, use cedarcli env artifact-key init.')
        path = ModeManager.cedar_home() / '.cedar' / 'secrets' / 'artifact-service.sh'
        if not path.exists() and invocation_environment().get(CURRENT):
            raise ProdError('An artifact service key is already supplied through the environment. '
                            'Keep using that secret provider; no local key was generated.')
        console.print(manage_artifact_key('init'), markup=False)
        console.print(f'Private key file: {path} (owner read/write only).', markup=False)
        console.print('On this application host, the native launcher loads this file automatically for artifact, '
                      'resource, and worker. No copying or manual export is needed. '
                      'Other services and frontends do not receive the key.')
        console.print('No running process was changed. During a full stopped-stack deployment, start the upgraded '
                      'microservices normally. For the first rolling deployment, restart the upgraded bridge, '
                      'then resource and worker, then artifact last. Verify with cedarcli native status and your '
                      'deployment acceptance checks.')
        console.print('If those three services run on different hosts, stop here: this command provisions only this host. '
                      'All backend hosts must receive the same secret through your deployment secret provider; '
                      'do not generate an independent key on each host.')
        return 0

    @staticmethod
    def configure_frontends():
        """Point the three Angular payloads at this host's CEDAR domain.

        Since 2.9.8 these applications compile `cedarDomain` into their bundle from
        `src/environments/environment.production.ts`, which hardcodes `metadatacenter.org`. There is
        no staging environment file and no build parameterisation, so a non-production host has to
        rewrite the built artifact.

        This used to rewrite `window.cedarDomain` in each dist `index.html`. Those files have
        carried no such declaration since that change, so the command could only ever raise, and it
        was skipped on every staging deploy in favour of a hand `sed`. Rewriting the bundle is what
        the hand step did; doing it here is what makes the zero-match guard possible, because a
        content-hashed filename that a stale command no longer matches is the failure a `sed` cannot
        report.

        Every other URL is derived: `appConfig.json` templates carry `{{cedarDomain}}` and
        `AppConfig` substitutes this one value, so the separate content-host rewrite the index.html
        needed has nothing left to act on.
        """
        domain = invocation_environment().get(Const.CEDAR_HOST)
        if not domain:
            raise ProdError("CEDAR_HOST is not set. Load the production CEDAR profile first.")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
            raise ProdError(f"CEDAR_HOST is not a valid hostname suffix: {domain}")

        payload_files = ProdWorker.frontend_payload_files()

        configured = []
        for _, path in payload_files:
            content = path.read_text()
            content, replacement_count = re.subn(
                r'cedarDomain:"[^"]*"',
                f'cedarDomain:"{domain}"',
                content,
            )
            if replacement_count == 0:
                raise ProdError(
                    f"Cannot find a compiled cedarDomain in {path}. The bundle was built without "
                    f"one, or the property was renamed; do not assume this host is configured.")
            if replacement_count > 1:
                raise ProdError(
                    f"Found {replacement_count} compiled cedarDomain values in {path}; expected "
                    f"exactly one. Rewriting all of them may not be correct, so stop and look.")
            configured.append((path, content))

        temporary_files = []
        for path, content in configured:
            temp_path = path.with_name(f'.{path.name}.cedarcli.tmp')
            temp_path.write_text(content)
            temp_path.chmod(path.stat().st_mode)
            temporary_files.append((temp_path, path))
        for temp_path, path in temporary_files:
            os.replace(temp_path, path)
        console.print(f"[green]Configured {len(payload_files)} frontend payloads for {domain}.[/green]")
        return 0

    @staticmethod
    def reset_frontends():
        for repo_dir, path in ProdWorker.frontend_payload_files():
            if not repo_dir.is_dir():
                raise ProdError(f"Cannot reset frontend; repository is missing: {repo_dir}")
            relative_path = path.relative_to(repo_dir)
            result = Worker.execute_generic_shell_commands(
                [f"git restore --source=HEAD -- {relative_path}"],
                cwd=str(repo_dir),
                title=f"Resetting {repo_dir.name} frontend configuration",
            )
            if result.returncode:
                return result.returncode
        return 0

    FRONTEND_PAYLOAD_REPOS = ('cedar-openview', 'cedar-bridging', 'cedar-monitoring')

    @staticmethod
    def frontend_payload_files():
        """The one built bundle per Angular payload that carries the compiled configuration.

        The filename is content-hashed, so it changes with the build and cannot be named in advance.
        Resolve it by glob and insist on exactly one: no match means the payload was never built,
        and several means a previous build was left beside the current one, where picking either is
        a guess about which tree nginx serves.
        """
        cedar_home = Path(Util.cedar_home)
        payloads = []
        for repo in ProdWorker.FRONTEND_PAYLOAD_REPOS:
            repo_dir = cedar_home / repo
            dist_dir = repo_dir / f'{repo}-dist'
            bundles = sorted(dist_dir.glob('main-*.js'))
            if not bundles:
                raise ProdError(
                    f"Cannot configure frontends; no built bundle in {dist_dir}. Expected one "
                    f"main-*.js file.")
            if len(bundles) > 1:
                names = ', '.join(path.name for path in bundles)
                raise ProdError(
                    f"Cannot configure frontends; {dist_dir} holds several bundles ({names}). "
                    f"Remove the stale one so the served payload is unambiguous.")
            payloads.append((repo_dir, bundles[0]))
        return payloads

"""One read-only report over the existing release and train contracts."""
import io
import tarfile
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.readiness import readiness_findings, _configuration
from org.metadatacenter.release_support.transport import HttpClient
from org.metadatacenter.release_support.packages import _tarball_files, _verify_integrity, _public_release_changelog
from org.metadatacenter.release_support.planning import ReleasePlanner
from org.metadatacenter.train_support import preflight, survey, release_intent
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.BuildTrain import BuildTrain


class ReadinessReport:
    def __init__(self, workspace, *, http=None):
        self.root = Path(workspace)
        self.http = http
        self.rows = []

    def check(self, name, action, remedy):
        try:
            detail = action()
            self.rows.append(dict(check=name, status='pass', detail=detail or 'Verified', next=''))
            return detail
        except (ReleaseError, ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
            self.rows.append(dict(check=name, status='fail', detail=str(error), next=remedy))
            return None

    def missing(self, name, remedy):
        self.rows.append(dict(check=name, status='not checked', detail='Required input or source unavailable', next=remedy))

    @staticmethod
    def require_clean(findings):
        if findings:
            raise ReleaseError('; '.join(findings))

    def package(self, name, version):
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ReleaseError(f'Choose an explicit stable version for {name}')
        self.http = self.http or HttpClient()
        record, _ = self.http.read_json(f'https://registry.npmjs.org/{quote(name, safe="")}/{version}')
        content = self.http.read(record['dist']['tarball'])
        _verify_integrity(name, content, record['dist']['integrity'])
        if name == 'cedar-embeddable-editor':
            files = _tarball_files(name, content)
        else:
            try:
                with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
                    members = [item for item in archive if item.name == 'package/package.json' and item.isfile()]
                    if len(members) != 1:
                        raise ReleaseError('Public model tarball must contain exactly one package.json')
                    files = {'package.json': archive.extractfile(members[0]).read()}
            except tarfile.TarError as error:
                raise ReleaseError(f'Invalid public model tarball: {error}') from error
        identity = json.loads(files['package.json'])
        if identity.get('name') != name or identity.get('version') != version:
            raise ReleaseError(f'{name} tarball identity differs from the requested version')
        return files

    def model(self, version):
        self.package('cedar-model-typescript-library', version)
        return f'Public model {version}; tarball integrity and identity verified'

    def cee(self, version, model):
        files = self.package('cedar-embeddable-editor', version)
        _, declared = _public_release_changelog('public CEE', files['CHANGELOG.md'], version)
        if declared != model:
            raise ReleaseError(f'CEE {version} declares model {declared}, expected {model}')
        bundles = [data for name, data in files.items() if name.endswith('.js') and b'cedar-model-typescript-library' in data]
        pin = b'"cedar-model-typescript-library":"' + model.encode() + b'"'
        if not bundles or any(pin not in bundle for bundle in bundles):
            raise ReleaseError('CEE bundle provenance does not confirm the expected model pin')
        return f'CEE {version} declares and embeds model pin {model}; executable equivalence still requires a train'

    def pins(self):
        frontend, _ = _configuration(self.root)
        consumers = [dict(item['ceeConsumer'], repository=item['repository'])
                     for item in frontend.get('frontends', []) if item.get('ceeConsumer')]
        consumers += frontend.get('additionalCeeConsumers', [])
        if not consumers:
            raise ReleaseError('No CEE consumers declared')
        details = []
        for item in consumers:
            base = self.root / item['repository']
            manifest = json.loads((base / item['manifest']).read_text())
            lock = json.loads((base / item['lock']).read_text())
            dependencies = {**manifest.get('dependencies', {}), **manifest.get('devDependencies', {})}
            name = item.get('dependency', 'cedar-embeddable-editor')
            pin = dependencies.get(name)
            root = lock.get('packages', {}).get('', {})
            locked = {**root.get('dependencies', {}), **root.get('devDependencies', {})}.get(name)
            installed = lock.get('packages', {}).get('node_modules/' + name, {})
            expected = pin.rsplit('@', 1)[-1] if isinstance(pin, str) else None
            if not pin or pin != locked or installed.get('version') != expected:
                raise ReleaseError(f"{item['repository']}:{item['manifest']} has missing or inconsistent CEE pin/lock")
            details.append(f"{item['repository']}:{item['manifest']} = {pin} (locked {installed['version']})")
        return '\n'.join(details)

    def sources(self, train):
        if train:
            return BuildTrain._read(f'trains/{BuildTrain.validate(train)}.json')
        _, config = _configuration(self.root)
        revisions = {}
        for repo in config['repositories']:
            result = subprocess.run(['git', '-C', str(self.root / repo), 'rev-parse', 'HEAD'],
                capture_output=True, text=True, check=False, env=invocation_environment(), timeout=30)
            if result.returncode:
                raise ReleaseError(f'Cannot read source revision for {repo}')
            revisions[repo] = result.stdout.strip()
        return {'repositories': revisions}

    def run(self, *, version=None, next_version=None, model_version=None, cee_version=None,
            train=None, packaging=True):
        self.check('Workspace', lambda: self.require_clean(readiness_findings(
            self.root, version, next_version, packaging=packaging)), 'cedarcli release readiness (repair the listed files first)')
        if not packaging:
            self.missing('Packaging', 'cedarcli release readiness --full (omit --skip-packaging)')
        for label, value, action in [('Public model', model_version, lambda: self.model(model_version)),
                                     ('Public CEE/model wiring', cee_version and model_version, lambda: self.cee(cee_version, model_version))]:
            if value:
                self.check(label, action, 'Follow ops/NPMJS-RELEASE-RUNBOOK.md, then rerun with --model-version and --cee-version')
            else:
                self.missing(label, 'Supply --model-version and --cee-version; versions are never inferred')
        self.check('Consumer pins', self.pins, 'cedarcli check components; repair the reported manifest/lock pair')
        source = self.check('Source revisions', lambda: self.sources(train), 'cedarcli git status; cedarcli git pull')
        if source:
            self.rows[-1]['detail'] = f"Captured {len(source['repositories'])} exact revisions"
            self.check('Exact-source CI', lambda: preflight._source_ci_preflight(source), 'cedarcli check ci; fix or wait for the reported exact-source runs')
            self.check('Whole-stack smoke', lambda: preflight._smoke_gate_preflight(source), 'cedarcli test e2e')
        else:
            self.missing('Exact-source CI', 'Repair source capture, then rerun readiness')
            self.missing('Whole-stack smoke', 'Repair source capture, then run cedarcli test e2e')
        self.check('Clean/pushed source', lambda: self.require_clean(survey._open_work() + survey._source_alignment()),
                   'cedarcli git status; reconcile and commit/push source changes, then rerun readiness')
        if all((version, next_version, cee_version)) and source:
            self.check('Release prerequisites', lambda: release_intent.preflight(
                release_intent.validate_intent(version, next_version, cee_version, train), source=source),
                'cedarcli publish train --release-version <VER> --next-version <NEXT> --cee-version <CEE> --dry-run')
            if self.rows[-1]['status'] == 'pass':
                self.rows[-1]['detail'] = 'Pre-train release prerequisites passed'
        else:
            self.missing('Release prerequisites', 'Supply --version, --next-version and --cee-version')
        if train and all((version, next_version, cee_version)):
            self.check('Train artifacts/equivalence', lambda: ReleasePlanner().build(
                release_version=version, next_version=next_version, train=train, cee_version=cee_version),
                'cedarcli publish train-status; repair source issues and create a fresh train if needed')
            if self.rows[-1]['status'] == 'pass':
                self.rows[-1]['detail'] = 'Train artifact contracts and public CEE equivalence verified'
            if source:
                def aligned():
                    moved, unreadable, _ = survey.releasability_survey(source)
                    self.require_clean([f'Moved: {moved}'] if moved else [])
                    self.require_clean([f'Unreadable: {unreadable}'] if unreadable else [])
                self.check('Train source eligibility', aligned, 'cedarcli publish train-status; create a new train after source changes')
        else:
            self.missing('Train artifacts/equivalence', 'Supply --from-train plus all release versions after a train completes')
        return self.rows

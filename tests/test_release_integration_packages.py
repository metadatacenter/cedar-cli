import hashlib
import io
import json
import tarfile
import unittest
from unittest.mock import Mock

from org.metadatacenter.release_support.packages import (
    _model_runtime_fingerprints, compare_cee_packages, load_model_runtime,
)
from org.metadatacenter.release_support.errors import ReleaseError
from tests.test_release_train import (
    DEV_CEE_NAME, DEV_VERSION, PUBLIC_CEE_NAME, PUBLIC_VERSION, PUBLIC_CHANGELOG,
    package_tarball, provenance_bundle, integrity,
)

DEV_MODEL = '1.0.5-dev.20260827.abcdef0'
PUBLIC_MODEL = '1.0.4'


def model_tarball(version, code=b'export const model = 1;'):
    package = {'name': ('@org.metadatacenter/' if '-dev.' in version else '') + 'cedar-model-typescript-library',
               'version': version, 'module': 'esm/index.js'}
    if '-dev.' in version:
        package['publishConfig'] = {'registry': 'https://nexus.bmir.stanford.edu/repository/npm-cedar/', 'tag': 'dev'}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for path, content in {'package.json': json.dumps(package).encode(), 'esm/index.js': code}.items():
            info = tarfile.TarInfo('package/' + path)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


class IntegrationPromotionTest(unittest.TestCase):
    def pair(self, *, changed_code=False, changed_model=False, bad_hash=False,
             angular='22.2.4', bad_bytes=False, omitted_module=False):
        models = {DEV_MODEL: model_tarball(DEV_MODEL), PUBLIC_MODEL: model_tarball(
            PUBLIC_MODEL, b'export const model = 2;' if changed_model else b'export const model = 1;')}
        packages = []
        for dev in (True, False):
            version = DEV_VERSION if dev else PUBLIC_VERSION
            model = DEV_MODEL if dev else PUBLIC_MODEL
            model_identity = 'npm:@org.metadatacenter/cedar-model-typescript-library@' + model if dev else model
            bundle = provenance_bundle(version, model_identity, '2026-11-06 12:00', suffix='standalone')
            module = provenance_bundle(version, model_identity, '2026-11-06 12:00',
                                       suffix='changed-code' if not dev and changed_code else 'integration')
            raw, runtime = _model_runtime_fingerprints(models[model], model)
            manifest = {'format': 'cedar-integration', 'version': 1, 'source': ('a' if dev else 'b') * 64,
                        'bytes': len(module), 'sha256': hashlib.sha256(module).hexdigest(),
                        'runtimes': {'@angular/core': {'version': '22.2.4' if dev else angular},
                                     'cedar-model-typescript-library': {'version': model, 'sha256': raw, 'runtimeSha256': runtime}}}
            if bad_hash and not dev:
                manifest['runtimes']['cedar-model-typescript-library']['sha256'] = '0' * 64
            if bad_bytes and not dev:
                manifest['sha256'] = '0' * 64
            extra = {'integration.mjs': module, 'integration-manifest.json': json.dumps(manifest).encode(),
                     'integration-licenses.txt': b'Licenses unchanged'}
            if omitted_module and not dev:
                del extra['integration.mjs']
            packages.append(package_tarball(DEV_CEE_NAME if dev else PUBLIC_CEE_NAME, version,
                development=dev, bundle=bundle, changelog=PUBLIC_CHANGELOG, extra_files=extra))
        return packages, models

    def compare(self, **kwargs):
        (dev, public), models = self.pair(**kwargs)
        return compare_cee_packages(dev, DEV_VERSION, public, PUBLIC_VERSION, model_runtime_loader=models.__getitem__)

    def test_public_and_train_runtime_code_is_proved(self):
        proof = self.compare()
        self.assertIn('integration.mjs', proof['provenBundles'])
        self.assertIn('integration-manifest.json:source and verified model package provenance', proof['allowedMetadataChanges'])

    def test_code_and_runtime_changes_remain_fatal(self):
        for change in ({'changed_code': True}, {'changed_model': True}, {'bad_hash': True},
                       {'angular': '22.2.5'}, {'bad_bytes': True}, {'omitted_module': True}):
            with self.subTest(change=change), self.assertRaises(ReleaseError):
                self.compare(**change)

    def test_runtime_packages_are_required(self):
        (dev, public), _ = self.pair()
        with self.assertRaisesRegex(ReleaseError, 'verified model runtime packages'):
            compare_cee_packages(dev, DEV_VERSION, public, PUBLIC_VERSION)

    def test_identity_normalization_keeps_code(self):
        dev = _model_runtime_fingerprints(model_tarball(DEV_MODEL), DEV_MODEL)
        public = _model_runtime_fingerprints(model_tarball(PUBLIC_MODEL), PUBLIC_MODEL)
        self.assertNotEqual(dev[0], public[0])
        self.assertEqual(dev[1], public[1])
        changed = _model_runtime_fingerprints(model_tarball(PUBLIC_MODEL, b'changed code'), PUBLIC_MODEL)
        self.assertNotEqual(public[1], changed[1])

    def test_registry_tarball_integrity_and_identity_are_checked(self):
        payload = model_tarball(PUBLIC_MODEL)
        record = {'name': 'cedar-model-typescript-library', 'version': PUBLIC_MODEL,
                  'dist': {'tarball': 'https://registry.npmjs.org/model.tgz', 'integrity': integrity(payload)}}
        http = Mock()
        http.read_json.return_value = (record, b'')
        http.read.return_value = payload
        self.assertEqual(load_model_runtime(http, PUBLIC_MODEL), payload)
        http.read.return_value = b'changed'
        with self.assertRaisesRegex(ReleaseError, 'integrity'):
            load_model_runtime(http, PUBLIC_MODEL)


if __name__ == '__main__':
    unittest.main()

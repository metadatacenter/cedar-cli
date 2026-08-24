import datetime as dt
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from org.metadatacenter.util.Util import Util


class BuildTrain:
    """Names and resolves immutable development trains."""

    STATE_BASE_URL = (
        "https://raw.githubusercontent.com/metadatacenter/cedar-development/"
        "build-trains"
    )
    VERSION_RE = re.compile(r"^\d+\.\d+\.\d+-dev\.\d{8}\.\d{4}$")

    @classmethod
    def validate(cls, version):
        if not version or not cls.VERSION_RE.fullmatch(version):
            raise ValueError(
                f'invalid train {version!r}; expected 2.9.3-dev.YYYYMMDD.HHMM'
            )
        return version

    @staticmethod
    def development_base_version():
        cedar_home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not cedar_home:
            raise ValueError('CEDAR_HOME is not set')
        pom = Path(cedar_home) / 'cedar-parent' / 'pom.xml'
        match = re.search(
            r'<artifactId>cedar-parent</artifactId>\s*<version>([^<]+)</version>',
            pom.read_text(encoding='utf-8'),
        )
        if not match or not match.group(1).endswith('-SNAPSHOT'):
            raise ValueError(f'cedar-parent does not declare a development snapshot in {pom}')
        return match.group(1).removesuffix('-SNAPSHOT')

    @classmethod
    def allocate(cls, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        return f'{cls.development_base_version()}-dev.{now:%Y%m%d.%H%M}'

    @classmethod
    def _read(cls, relative_path, opener=None):
        opener = opener or urllib.request.urlopen
        url = f'{cls.STATE_BASE_URL}/{relative_path}'
        try:
            with opener(url, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ValueError(f'build-train state does not exist: {relative_path}') from error
            raise ValueError(f'cannot read build-train state: HTTP {error.code}') from error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise ValueError(f'cannot read build-train state: {error}') from error

    @classmethod
    def current(cls, environment=None, opener=None):
        environment = os.environ if environment is None else environment
        override = environment.get('CEDAR_TRAIN_VERSION')
        if override:
            return cls.validate(override)
        payload = cls._read('current.json', opener=opener)
        return cls.validate(payload.get('version'))

    @classmethod
    def completed(cls, version, opener=None):
        version = cls.validate(version)
        payload = cls._read(f'completed/{version}.json', opener=opener)
        if payload.get('version') != version:
            raise ValueError(f'completion record does not describe {version}')
        return version

    @classmethod
    def resolve(cls, requested=None, environment=None, opener=None):
        if requested:
            return cls.completed(requested, opener=opener)
        return cls.current(environment=environment, opener=opener)

import difflib
import os
import re
import subprocess

from org.metadatacenter.util.Util import Util

# Every image carries a short name, because typing cedar-server-artifact in a development loop is
# noise. For the fifteen servers and the admin tool the short name is exactly the source repository
# minus its cedar- prefix, so the name you build the jar in is the name you build the image with.
#
# The suffix matters: cedar-server-openview and cedar-frontend-openview would otherwise collide, and
# artifact/artifacts and monitor/monitoring differ by a character while naming different images.


class DockerImages:
    GROUPS = ['infra', 'microservices', 'frontends', 'admin']
    INTERNAL_IMAGES = {'cedar-java', 'cedar-microservice'}
    DEFAULT_IMAGE_PREFIX = 'metadatacenter'
    _REPOSITORY_COMPONENT = re.compile(r'^[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*$')
    _REGISTRY_HOST = re.compile(r'^[a-z0-9]+(?:[.-][a-z0-9]+)*$')

    @staticmethod
    def build_home():
        # Util.cedar_home is populated during CLI startup; fall back to the environment so this
        # module also works when used on its own.
        home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not home:
            raise ValueError('CEDAR_HOME is not set')
        return os.path.join(home, 'cedar-docker-build')

    @classmethod
    def source_revision(cls):
        """Exact cedar-docker-build commit used for OCI provenance labels, when available."""
        try:
            result = subprocess.run(
                ['git', '-C', cls.build_home(), 'rev-parse', 'HEAD'],
                text=True,
                capture_output=True,
                check=False,
            )
        except (OSError, ValueError):
            return None
        revision = result.stdout.strip()
        return revision if result.returncode == 0 and re.fullmatch(r'[0-9a-f]{40}', revision) else None

    @classmethod
    def _manifest_path(cls):
        return os.path.join(cls.build_home(), 'bin', 'cedar-images-base.sh')

    @classmethod
    def default_image_prefix(cls):
        """Return the compatibility default declared by the shell build manifest."""
        try:
            manifest_path = cls._manifest_path()
        except ValueError:
            return cls.DEFAULT_IMAGE_PREFIX
        with open(manifest_path, encoding='utf-8') as manifest:
            text = manifest.read()
        parameterized = re.search(
            r'^export CEDAR_IMAGE_PREFIX="\$\{CEDAR_IMAGE_PREFIX:-([^}]+)\}"',
            text,
            re.M,
        )
        if parameterized:
            return parameterized.group(1)
        legacy = re.search(r'^export CEDAR_IMAGE_PREFIX="([^"]+)"', text, re.M)
        return legacy.group(1) if legacy else cls.DEFAULT_IMAGE_PREFIX

    @classmethod
    def validate_image_prefix(cls, prefix):
        """Validate a Docker repository prefix such as registry.example.org:5000/cedar."""
        if not prefix:
            raise ValueError('CEDAR_IMAGE_PREFIX is empty')
        if len(prefix) > 255:
            raise ValueError('CEDAR_IMAGE_PREFIX is longer than 255 characters')
        if prefix != prefix.lower():
            raise ValueError('CEDAR_IMAGE_PREFIX must be lowercase')
        if '://' in prefix:
            raise ValueError('CEDAR_IMAGE_PREFIX must not include a URL scheme')
        if prefix.startswith('/') or prefix.endswith('/') or '//' in prefix:
            raise ValueError('CEDAR_IMAGE_PREFIX must not start or end with / or contain //')
        if any(character.isspace() for character in prefix) or '@' in prefix:
            raise ValueError('CEDAR_IMAGE_PREFIX must not contain whitespace or a digest')

        components = prefix.split('/')
        first = components[0]
        if ':' in first:
            host, separator, port = first.rpartition(':')
            if not separator or not cls._REGISTRY_HOST.fullmatch(host):
                raise ValueError('CEDAR_IMAGE_PREFIX has an invalid registry host')
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError('CEDAR_IMAGE_PREFIX has an invalid registry port')
            components = components[1:]

        if not components and ':' in first:
            return prefix
        if any(not cls._REPOSITORY_COMPONENT.fullmatch(component) for component in components):
            raise ValueError('CEDAR_IMAGE_PREFIX has an invalid repository component')
        return prefix

    @classmethod
    def image_prefix(cls, environment=None):
        environment = os.environ if environment is None else environment
        prefix = environment.get('CEDAR_IMAGE_PREFIX')
        if prefix is None:
            prefix = cls.default_image_prefix()
        return cls.validate_image_prefix(prefix)

    @classmethod
    def base_image_prefix(cls, environment=None):
        """Registry prefix for the two non-runtime Java base images.

        It defaults to the runtime prefix so existing Docker Hub and local builds keep using one
        namespace. Registry publication can put the bases in a private/internal repository without
        leaking that repository into Compose.
        """
        environment = os.environ if environment is None else environment
        prefix = environment.get('CEDAR_BASE_IMAGE_PREFIX')
        if prefix is None:
            prefix = cls.image_prefix(environment)
        return cls.validate_image_prefix(prefix)

    @classmethod
    def prefix_for(cls, image, environment=None):
        if image in cls.INTERNAL_IMAGES:
            return cls.base_image_prefix(environment)
        return cls.image_prefix(environment)

    @classmethod
    def reference(cls, image, version, environment=None):
        return f'{cls.prefix_for(image, environment)}/{image}:{version}'

    @classmethod
    def manifest(cls, environment=None):
        """Image names and version, read from the shell manifest that stays the source of truth."""
        environment = os.environ if environment is None else environment
        with open(cls._manifest_path(), encoding='utf-8') as manifest:
            text = manifest.read()
        version = re.search(r'^export IMAGE_VERSION=(\S+)', text, re.M)
        array = re.search(r'CEDAR_DOCKER_IMAGES=\((.*?)\)', text, re.S)
        images = re.findall(r'"([^"]+)"', array.group(1)) if array else []
        selected_version = environment.get('CEDAR_TRAIN_VERSION')
        if selected_version is None:
            selected_version = version.group(1) if version else None
        return images, selected_version, cls.image_prefix(environment)

    @classmethod
    def server_versions(cls):
        """The locked infrastructure server versions the images are built against.

        Every `export <NAME>_VERSION=` in the manifest other than the CEDAR image version itself,
        so adding a server here is a one-line change to the manifest and nothing else. The
        Dockerfiles declare these as build arguments with no default, so a version missing here
        fails the build rather than being silently substituted.
        """
        with open(cls._manifest_path(), encoding='utf-8') as manifest:
            text = manifest.read()
        found = re.findall(r'^export ([A-Z0-9_]+(?:_VERSION|_SHA256))=(\S+)', text, re.M)
        return {name: value for name, value in found if name != 'IMAGE_VERSION'}

    @staticmethod
    def short_name(image):
        if image == 'cedar-admin-tool':
            return 'admin-tool'
        for infix, suffix in (('cedar-server-', '-server'), ('cedar-frontend-', '-frontend')):
            if image.startswith(infix):
                return image[len(infix):] + suffix
        for infix in ('cedar-infra-', 'cedar-admin-'):
            if image.startswith(infix):
                return image[len(infix):]
        return image[len('cedar-'):]

    @staticmethod
    def group_of(image):
        if image.startswith('cedar-infra-'):
            return 'infrastructure'
        if image.startswith('cedar-frontend-'):
            return 'frontends'
        if image.startswith('cedar-admin-'):
            return 'admin'
        # The two base images are not services anywhere, but they are built with the servers.
        return 'microservices'

    @classmethod
    def base_images_of(cls, image):
        """The CEDAR images this one is built FROM, if any."""
        path = os.path.join(cls.build_home(), image, 'Dockerfile')
        if not os.path.exists(path):
            return []
        bases = []
        with open(path, encoding='utf-8') as dockerfile:
            lines = dockerfile.read().splitlines()
        for line in lines:
            m = re.match(r'\s*FROM\s+\$\{CEDAR_IMAGE_PREFIX\}/(\S+?):', line)
            if m:
                bases.append(m.group(1))
        return bases

    @classmethod
    def with_dependencies(cls, images):
        """Expand to include every CEDAR base needed, each once, bases before dependents."""
        ordered = []

        def visit(image):
            if image in ordered:
                return
            for base in cls.base_images_of(image):
                visit(base)
            ordered.append(image)

        for image in images:
            visit(image)
        return ordered

    @classmethod
    def resolve(cls, target):
        """Turn a target into an ordered image list, or raise ValueError explaining why not."""
        images, _, _ = cls.manifest()
        by_short = {cls.short_name(i): i for i in images}

        if target == 'all':
            return list(images)
        if target in cls.GROUPS:
            internal_group = 'infrastructure' if target == 'infra' else target
            selected = [i for i in images if cls.group_of(i) == internal_group]
            if not selected:
                raise ValueError(f'no images in group "{target}"')
            return selected
        if target in images:
            return [target]
        if target in by_short:
            return [by_short[target]]

        # artifact/artifacts and monitor/monitoring differ by a character and name different images,
        # so a near miss should be named rather than left to guesswork.
        candidates = list(by_short) + cls.GROUPS + ['all']
        near = difflib.get_close_matches(target, candidates, n=3, cutoff=0.6)
        hint = f' — did you mean {", ".join(near)}?' if near else ''
        raise ValueError(f'unknown target "{target}"{hint}')

    @classmethod
    def stageable(cls, image):
        """Images that carry a jar, and so can be built from a local checkout."""
        return os.path.isdir(os.path.join(cls.build_home(), image, 'local'))

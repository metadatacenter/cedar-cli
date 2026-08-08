import difflib
import os
import re

from org.metadatacenter.util.Util import Util

# Every image carries a short name, because typing cedar-server-artifact in a development loop is
# noise. For the fifteen servers and the admin tool the short name is exactly the source repository
# minus its cedar- prefix, so the name you build the jar in is the name you build the image with.
#
# The suffix matters: cedar-server-openview and cedar-frontend-openview would otherwise collide, and
# artifact/artifacts and monitor/monitoring differ by a character while naming different images.


class DockerImages:
    GROUPS = ['infrastructure', 'microservices', 'frontends', 'admin']

    @staticmethod
    def build_home():
        # Util.cedar_home is populated during CLI startup; fall back to the environment so this
        # module also works when used on its own.
        home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not home:
            raise ValueError('CEDAR_HOME is not set')
        return os.path.join(home, 'cedar-docker-build')

    @classmethod
    def _manifest_path(cls):
        return os.path.join(cls.build_home(), 'bin', 'cedar-images-base.sh')

    @classmethod
    def manifest(cls):
        """Image names and version, read from the shell manifest that stays the source of truth."""
        text = open(cls._manifest_path()).read()
        version = re.search(r'^export IMAGE_VERSION=(\S+)', text, re.M)
        prefix = re.search(r'^export CEDAR_IMAGE_PREFIX="([^"]+)"', text, re.M)
        array = re.search(r'CEDAR_DOCKER_IMAGES=\((.*?)\)', text, re.S)
        images = re.findall(r'"([^"]+)"', array.group(1)) if array else []
        return images, (version.group(1) if version else None), (prefix.group(1) if prefix else 'metadatacenter')

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
        for line in open(path).read().splitlines():
            m = re.match(r'\s*FROM\s+metadatacenter/(\S+?):', line)
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
            selected = [i for i in images if cls.group_of(i) == target]
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

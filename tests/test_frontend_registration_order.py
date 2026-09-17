"""The frontends must be registered in dependency order, and stay that way.

A plan is walked in the order repos are added in ReposFactory, and every consumer resolves its
CEDAR packages from Nexus when its own build starts. A consumer registered before the package it
consumes therefore builds against the snapshot that package replaced: it succeeds, and it is wrong.
Nothing but the position in that file enforces this, so it is asserted here.

Both halves matter. The declared edges below are the invariant, and they run anywhere. The second
test derives the edges from the manifests in the workspace instead, because a declared list is what
went stale before: the rule was once written down for two repositories that do not consume the
tokens at all, while the three that do were missing.
"""
import json
import unittest
from pathlib import Path

from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.util.Util import Util

# What consumes what, as the manifests declare it. Producer -> the repositories that consume it.
EDGES = {
    "cedar-design-tokens": (
        "cedar-embeddable-editor", "cedar-embeddable-term-picker", "cedar-embeddable-designer",
        "cedar-workspace",
    ),
    "cedar-model-typescript-library": (
        "cedar-embeddable-editor", "cedar-embeddable-designer",
        "cedar-model-typescript-library-demo",
    ),
    "cedar-embeddable-editor": (
        "cedar-template-editor", "cedar-workspace", "cedar-template-designer",
        "cedar-bridging", "cedar-openview", "cedar-component-demo",
    ),
    "cedar-embeddable-term-picker": ("cedar-template-designer",),
    "cedar-embeddable-designer": ("cedar-template-designer",),
}

DEPENDENCY_SECTIONS = ("dependencies", "devDependencies")


def _order():
    return [repo.name for repo in ReposFactory.build_repos().get_frontends()]


def _unscoped(name):
    return name.rsplit("/", 1)[-1]


class FrontendRegistrationOrderTest(unittest.TestCase):

    def test_every_producer_is_registered_before_the_repositories_that_consume_it(self):
        order = _order()
        for producer, consumers in EDGES.items():
            self.assertIn(producer, order, f"{producer} is not registered as a frontend")
            for consumer in consumers:
                self.assertIn(consumer, order, f"{consumer} is not registered as a frontend")
                self.assertLess(
                    order.index(producer), order.index(consumer),
                    f"{producer} must be registered before {consumer}, which consumes it")

    def test_the_declared_edges_match_what_the_workspace_manifests_declare(self):
        """Guards the list above against the drift that made its predecessor wrong."""
        root = Path(Util.cedar_home or "")
        order = _order()
        checked = 0
        for consumer in order:
            manifests = [root / consumer / "package.json"]
            manifests += sorted((root / consumer).glob("*/package.json"))
            for manifest in manifests:
                if not manifest.is_file() or "node_modules" in manifest.parts:
                    continue
                try:
                    package = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                checked += 1
                for section in DEPENDENCY_SECTIONS:
                    for key, value in (package.get(section) or {}).items():
                        name = key
                        if isinstance(value, str) and value.startswith("npm:"):
                            name = value[len("npm:"):].rsplit("@", 1)[0]
                        producer = _unscoped(name)
                        if producer == consumer or producer not in order:
                            continue
                        self.assertIn(
                            consumer, EDGES.get(producer, ()),
                            f"{manifest} depends on {producer}, which EDGES does not record")
        if not checked:
            self.skipTest("requires the sibling frontend checkouts")


if __name__ == "__main__":
    unittest.main()

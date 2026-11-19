"""The merged-coverage helper, compiled and run on real execution data.

Every other coverage test stands in for `scripts/java/MergedCoverage.java`, so a JaCoCo upgrade that
changed how it loads execution data or counts a bundle would pass them. This test records execution
data for two small classes with the JaCoCo agent cedar-parent pins and runs the helper over it. It
needs a JDK and JaCoCo's jars in the local Maven repository, as a workstation that has built CEDAR
has, and skips without them.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from org.metadatacenter import coverage

HOME = Path(__file__).resolve().parents[2]
REPOSITORY = Path.home() / '.m2' / 'repository'

USED = '''package fixture;
public class Used {
  public static int pick(boolean left) {
    if (left) {
      return 1;
    }
    return 2;
  }
}
'''
UNUSED = '''package fixture;
public class Unused {
  public static int never() {
    return 3;
  }
}
'''
MAIN = '''package fixture;
public class Main {
  public static void main(String[] arguments) {
    System.out.println(Used.pick(true));
  }
}
'''


def _jdk():
    home = os.environ.get('JAVA_HOME')
    directory = Path(home) / 'bin' if home else None
    tools = [str(directory / name) if directory else shutil.which(name) for name in ('java', 'javac')]
    return tools if all(tool and Path(tool).is_file() for tool in tools) else None


class MergedCoverageHelperTest(unittest.TestCase):

    def setUp(self):
        jdk = _jdk()
        if jdk is None:
            self.skipTest('no JDK')
        self.java, self.javac = jdk
        try:
            self.classpath = coverage.jacoco_classpath(HOME, REPOSITORY)
        except coverage.CoverageUnavailable as reason:
            self.skipTest(str(reason))
        version = self.classpath[0].parent.name
        self.agent = (REPOSITORY / 'org' / 'jacoco' / 'org.jacoco.agent' / version /
                      f'org.jacoco.agent-{version}-runtime.jar')
        if not self.agent.is_file():
            self.skipTest(f'no JaCoCo {version} agent in the local repository')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def compile(self):
        sources = self.root / 'src' / 'fixture'
        sources.mkdir(parents=True)
        for name, text in (('Used', USED), ('Unused', UNUSED), ('Main', MAIN)):
            (sources / f'{name}.java').write_text(text)
        classes = self.root / 'classes'
        subprocess.run([self.javac, '-d', str(classes), *map(str, sorted(sources.glob('*.java')))], check=True)
        return classes

    def record(self, classes, name):
        execution = self.root / f'{name}.exec'
        subprocess.run([self.java, f'-javaagent:{self.agent}=destfile={execution},includes=fixture.*',
                        '-cp', str(classes), 'fixture.Main'], check=True, capture_output=True)
        return execution

    def test_the_helper_credits_a_module_with_any_test_and_with_its_repository_alone(self):
        classes = self.compile()
        execution = self.record(classes, 'jacoco')
        class_files = [classes / 'fixture' / 'Used.class', classes / 'fixture' / 'Unused.class']
        tested, untested = self.root / 'cedar-a' / 'core', self.root / 'cedar-b' / 'core'

        figures = coverage.merged_figures(
            {tested: ('cedar-a', class_files), untested: ('cedar-b', class_files)},
            [('cedar-a', execution)], Path(self.java), self.classpath)

        # Used's pick runs its true branch only: two of its three lines and one of its two branches.
        # Unused and both default constructors never run. The repository figure counts cedar-a's
        # execution data alone, so cedar-b's module is covered by some test and by none of its own
        # repository's.
        self.assertEqual((2, 6, 1, 2, 2, 1), figures[tested])
        self.assertEqual((2, 6, 1, 2, 0, 0), figures[untested])


if __name__ == '__main__':
    unittest.main()

"""The lines of a Maven build's output that say which tests ran, which failed, and why the build stopped.

A build report kept only each command's exit code, so a failed `cedarcli build java` left no record of
its cause outside the console transcript. These lines are what a reader of the transcript looks for:
each module's test totals, each failing test class and test, the goal that failed and the module it
failed in, a test JVM Surefire killed, and a coverage floor that was not met.
"""
import re

LIMIT = 300
_ANSI = re.compile(r'\x1b\[[0-9;]*m')
_TOTALS = re.compile(r'Tests run: \d+, Failures: \d+, Errors: \d+, Skipped: \d+')
_KEPT = re.compile(r'<<< (FAILURE|ERROR)!|\[ERROR\] Failed to execute goal|Surefire is going to kill self fork JVM'
                   r'|covered ratio is|There was a timeout in the fork|The forked VM terminated')
_SECTION = re.compile(r'\[ERROR\] (Failures|Errors):\s*$')
_SECTION_ENTRY = re.compile(r'\[ERROR\]\s{2,}\S')


class MavenTestSummary:
    """Collects the summary lines of one command's output, line by line, up to LIMIT."""

    def __init__(self):
        self.lines = []
        self._in_section = False

    def add(self, line: str) -> None:
        text = _ANSI.sub('', line).rstrip()
        if _SECTION.search(text):
            self._in_section = True
            self._keep(text)
            return
        if self._in_section and _SECTION_ENTRY.match(text):
            self._keep(text)
            return
        self._in_section = False
        # A passing class's line carries its time; a module's totals do not.
        if (_TOTALS.search(text) and 'Time elapsed' not in text) or _KEPT.search(text):
            self._keep(text)

    def _keep(self, text: str) -> None:
        if len(self.lines) < LIMIT:
            self.lines.append(text)
